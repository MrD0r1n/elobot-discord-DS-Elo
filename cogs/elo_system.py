import sqlite3
import discord
from discord import app_commands
import math
import datetime
import asyncio
import settings
from cogs.backup import backup_db


# Role IDs live in settings.py now - see settings.RANK_ROLES / settings.STAFF_ROLES.
roles = settings.RANK_ROLES


# Create a connection to the SQLite database
# If the database doesn't exist, it will be created
with sqlite3.connect('elo_data.db') as conn:
    c = conn.cursor()
    
    try:
        # Create the table if it doesn't exist
        c.execute('''
            CREATE TABLE IF NOT EXISTS elo_data (
                player_id INTEGER PRIMARY KEY,
                elo INTEGER,
                highest_elo INTEGER,
                inactive INTEGER DEFAULT 0
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS match_data (
                game_id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                winner_id INTEGER,
                loser_id INTEGER,
                elo_change INTEGER,
                elo_winner INTEGER,
                elo_loser INTEGER,
                multiplier INTEGER DEFAULT 1
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS historical_rankings (
                ranking_id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER,
                date TEXT,
                rank INTEGER
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                setting_name TEXT PRIMARY KEY,
                setting_value TEXT
            )
        ''')

    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
        conn.rollback()


# ELO-related functions
def get_multiplier():
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('SELECT setting_value FROM settings WHERE setting_name = "elo_multiplier"')
        result = c.fetchone()
        if result and result[0] == 'on':
            return 2
        return 1

def calculate_elo_rank(winner_rank, loser_rank, k=40):

    if winner_rank >= 1800:
        k = 20
    if winner_rank >= 2400:
        k = 10

    rank_diff = loser_rank - winner_rank
    expected_outcome = 1 / (1 + math.pow(10, rank_diff / 400))
    return winner_rank + k * (1 - expected_outcome)

def update_elo(winner, loser):
    winner_elo = get_elo(winner)
    loser_elo = get_elo(loser)

    winner_new_elo = int(calculate_elo_rank(winner_elo, loser_elo))
    loser_new_elo = loser_elo - (winner_new_elo - winner_elo)

    multiplier = get_multiplier()

    if multiplier == 2:
        winner_new_elo = winner_new_elo + (winner_new_elo - winner_elo)

    set_elo(winner, winner_new_elo)
    set_elo(loser, loser_new_elo)

def calculate_score_change(winner, loser):
    winner_elo = get_elo(winner)
    loser_elo = get_elo(loser)

    winner_new_elo = int(calculate_elo_rank(winner_elo, loser_elo))
    winner_score_change = winner_new_elo - winner_elo

    return winner_score_change

def get_elo(player_id):
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('SELECT elo FROM elo_data WHERE player_id = ?', (player_id,))
        result = c.fetchone()
        return result[0] if result else None

def set_elo(player_id, elo):
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO elo_data (player_id, elo) VALUES (?, ?)', (player_id, elo))
        c.execute('UPDATE elo_data SET elo = ? WHERE player_id = ?', (elo, player_id))
        conn.commit()

def get_highest_elo(player_id):
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('SELECT highest_elo FROM elo_data WHERE player_id = ?', (player_id,))
        result = c.fetchone()
        return result[0] if result else None

def set_highest_elo(player_id, highest_elo):
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('UPDATE elo_data SET highest_elo = ? WHERE player_id = ?', (highest_elo, player_id))
        conn.commit()


def update_historical_rankings():
    """Update the historical rankings table."""
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('SELECT player_id FROM elo_data ORDER BY elo DESC')
        current_rankings = c.fetchall()
        current_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Store new rankings in historical_rankings table
        for i, (player_id,) in enumerate(current_rankings):
            c.execute("INSERT INTO historical_rankings (player_id, date, rank) VALUES (?, ?, ?)", (player_id, current_date, i+1))

        conn.commit()

# Other functions
async def grant_winner_rank_roles(member: discord.Member, extra_role_ids: frozenset = frozenset()):
    """Grants the Challenger/Baller roles to a match winner.

    `extra_role_ids` lets a caller processing many matches in one batch (e.g. Challonge import) tell
    us about roles it already granted this same member earlier in the batch - member.roles won't
    reflect those yet since add_roles() doesn't update discord.py's local cache.

    Returns (messages, granted_role_ids): status messages (earned-role announcements or missing-role
    warnings) without sending them, so callers can present them however they like (/report sends them
    immediately, Challonge import batches them into a summary); and the set of role IDs actually
    granted, for the caller to fold into extra_role_ids on the next call for this member."""
    known_role_ids = {role.id for role in member.roles} | extra_role_ids
    if known_role_ids & roles:
        return [], set()

    guild = member.guild
    brawler_obj = guild.get_role(settings.ROLE_BALLER)
    contender_obj = guild.get_role(settings.ROLE_CONTENDER)
    contender_role = settings.ROLE_CONTENDER

    if contender_role in known_role_ids:
        if brawler_obj is None:
            return [f":warning:  Couldn't find the Baller role in this server (check the role ID) — skipping role assignment for {member.name}."], set()
        await member.add_roles(brawler_obj)
        return [f"{member.name} earned the Baller role!"], {brawler_obj.id}

    if contender_obj is None or brawler_obj is None:
        missing = [name for name, obj in (("Challenger", contender_obj), ("Baller", brawler_obj)) if obj is None]
        return [f":warning:  Couldn't find the {' / '.join(missing)} role(s) in this server (check the role ID{'s' if len(missing) > 1 else ''}) — skipping role assignment for {member.name}."], set()
    await member.add_roles(contender_obj)
    await member.add_roles(brawler_obj)
    return [f"{member.name} earned the Challenger and Baller role!"], {contender_obj.id, brawler_obj.id}


async def grant_loser_rank_roles(member: discord.Member, extra_role_ids: frozenset = frozenset()):
    """Grants the Challenger role to a match loser if they don't already have a rank role.
    See grant_winner_rank_roles for extra_role_ids and the (messages, granted_role_ids) return shape."""
    known_role_ids = {role.id for role in member.roles} | extra_role_ids
    if known_role_ids & roles:
        return [], set()

    contender_role = settings.ROLE_CONTENDER
    if contender_role in known_role_ids:
        return [], set()

    contender_obj = member.guild.get_role(contender_role)
    if contender_obj is None:
        return [f":warning:  Couldn't find the Challenger role in this server (check the role ID) — skipping role assignment for {member.name}."], set()
    await member.add_roles(contender_obj)
    return [f"{member.name} earned the Challenger role!"], {contender_obj.id}


def create_embed(description):
    """Create the embed for the current page."""
    embed = discord.Embed(
        title=":ballot_box:  Match reported",
        description=description,
        color=discord.Color.blue()
    )
    return embed 


# Commands
@app_commands.command(name = "register", description = "Register a player")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
async def register(interaction, player: discord.Member):
    if get_elo(player.id) is None:
            set_elo(player.id, 1200)
            await interaction.response.send_message(f"{player.mention} has been registered with an initial ELO of 1200")
    else:
        await interaction.response.send_message(f"{player.mention} is already registered")

@app_commands.command(name = "report", description = "Report a match")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
async def report(interaction: discord.Interaction, winner: discord.Member, loser: discord.Member):
    if winner.id == loser.id:
        await interaction.response.send_message(":face_with_monocle:  Winner and loser can't be the same person.")
        return

    await interaction.response.defer()
    new_players = []

    # Check for new players
    if get_elo(winner.id) is None and get_elo(loser.id) is not None:
        set_elo(winner.id, 1200)
        new_players.append(f"{winner.mention} has been registered with an initial ELO of 1200")
    if get_elo(loser.id) is None and get_elo(winner.id) is not None:
        set_elo(loser.id, 1200)
        new_players.append(f"{loser.mention} has been registered with an initial ELO of 1200")
    if get_elo(winner.id) is None and get_elo(loser.id) is None:
        set_elo(winner.id, 1200)
        set_elo(loser.id, 1200)
        new_players.append(f"{winner.mention} & {loser.mention} both have been registered with an ELO of 1200")
    if new_players: 
        await interaction.followup.send("\n".join(new_players))

    # Grant rank roles based on standing (Challenger/Baller for the winner, Challenger for the loser)
    winner_role_msgs, _ = await grant_winner_rank_roles(winner)
    for msg in winner_role_msgs:
        await interaction.followup.send(msg)
    loser_role_msgs, _ = await grant_loser_rank_roles(loser)
    for msg in loser_role_msgs:
        await interaction.followup.send(msg)

    # Update the ELO scores
    #date = datetime.date.today().isoformat()  # get the current date in 'YYYY-MM-DD' format
    date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')  # 'YYYY-MM-DD HH:MM:SS'
    old_elo_winner = get_elo(winner.id)
    old_elo_loser = get_elo(loser.id)
    score_change = calculate_score_change(winner.id, loser.id)
    update_elo(winner.id, loser.id)
    elo_winner = get_elo(winner.id)
    elo_loser = get_elo(loser.id)

    # Update highest ELO achieved
    if get_highest_elo(winner.id) is None:
        set_highest_elo(winner.id, elo_winner)
    if get_highest_elo(loser.id) is None:
        set_highest_elo(loser.id, elo_loser)
    if elo_winner > get_highest_elo(winner.id):
        set_highest_elo(winner.id, elo_winner)
    if elo_loser > get_highest_elo(loser.id):
        set_highest_elo(loser.id, elo_loser)

    # Update historical rankings
    update_historical_rankings() 
    # Check multiplier status for k-factor
    multiplier = get_multiplier()

    # Insert a new row into the match_data table
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('INSERT INTO match_data (date, winner_id, loser_id, elo_change, elo_winner, elo_loser, multiplier) VALUES (?, ?, ?, ?, ?, ?, ?)', (date, winner.id, loser.id, score_change, elo_winner, elo_loser, multiplier))
        last_game_id = c.lastrowid
        conn.commit()

    # send embed message
    description = (f"Match played by {winner.mention} :crossed_swords: {loser.mention}\n"
                f"Game No.{last_game_id} {'(with multiplier)' if multiplier == 2 else ''} successfully submitted!\n\u200b```\n\n"
                f"Player:\t\t\tELO:\n"
                f"――――――――――――――――――――――――――――――――――――\n"
                f"{winner.display_name[:15]:<15}\t({old_elo_winner} > {elo_winner}) +{score_change * multiplier}\n"
                f"{loser.display_name[:15]:<15}\t({old_elo_loser} > {elo_loser}) -{score_change}```")
 
    await interaction.followup.send(embed=create_embed(description))

@app_commands.command(name = "set_elo", description = "Sets the elo of a player")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
async def change_elo(interaction, player: discord.Member, elo: int):
    if get_elo(player.id) is None:
        await interaction.response.send_message(f"{player.mention} is not registered.")
    else:
        set_elo(player.id, elo)
        await interaction.response.send_message(f"{player.mention}'s ELO has been set to {elo}.")

@app_commands.command(name = "show_elo", description = "Show elo")
async def elo(interaction, player: discord.Member):
    if get_elo(player.id) is None:
        await interaction.response.send_message(f"{player.mention} is not registered.")
    else:
        elo = get_elo(player.id)
        await interaction.response.send_message(f"{player.mention}'s ELO is {elo:.0f}.")

@app_commands.command(name = "show_highest_elo", description = "Show highest elo achieved")
async def highest_elo(interaction, player: discord.Member):
    if get_highest_elo(player.id) is None:
        await interaction.response.send_message(f"{player.mention} is not registered.")
    else:
        highest_elo = get_highest_elo(player.id)
        await interaction.response.send_message(f"{player.mention}'s highest ELO achieved is {highest_elo:.0f}.")

@app_commands.command(name = "game", description = "Show game details")
async def game(interaction: discord.Interaction, game_id: int):
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM match_data WHERE game_id = ?', (game_id,))
        result = c.fetchone()
        if result is None:
            await interaction.response.send_message(f"No game found with ID {game_id}.")
        else:
            game_id, date, winner_id, loser_id, elo_change, elo_winner, elo_loser, multiplier = result

            # Fetch the winner and loser as Member objects
            winner = await interaction.guild.fetch_member(winner_id)
            loser = await interaction.guild.fetch_member(loser_id)

            await interaction.response.send_message(f"Game ID: {game_id}\nDate: {date}\nWinner: {winner.name} ({elo_winner - elo_change * multiplier}  > {elo_winner}) +{elo_change * multiplier}\nLoser: {loser.name} ({elo_loser + elo_change}  > {elo_loser}) -{elo_change}")

@app_commands.command(name = "remove_game", description = "Remove a game and undo ELO changes")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
async def remove_game(interaction, game_id: int):
    # Fetch the game details
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM match_data WHERE game_id = ?', (game_id,))
        result = c.fetchone()
        if result is None:
            await interaction.response.send_message(f"No game found with ID {game_id}.")
        else:
            game_id, date, winner_id, loser_id, elo_change, elo_winner, elo_loser, multiplier = result

            # Undo the ELO changes
            set_elo(winner_id, get_elo(winner_id) - elo_change * multiplier)
            set_elo(loser_id, get_elo(loser_id) + elo_change)

            # Fetch the winner and loser as Member objects
            winner = await interaction.guild.fetch_member(winner_id)
            loser = await interaction.guild.fetch_member(loser_id)

            # Delete the game from the match_data table
            c.execute('DELETE FROM match_data WHERE game_id = ?', (game_id,))
            conn.commit()

            await interaction.response.send_message(f"Game {game_id} removed and ELO changes undone.\n{winner.name}'s ELO is now: {get_elo(winner_id)} ({get_elo(winner_id) + elo_change * multiplier}-{elo_change * multiplier})\n{loser.name}'s ELO is now: {get_elo(loser_id)} ({get_elo(loser_id) - elo_change}+{elo_change})")

@app_commands.command(name="toggle_elo_multiplier", description="Toggle the ELO multiplier")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
async def toggle_elo_multiplier(interaction):
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        current_multiplier = get_multiplier()
        if current_multiplier == 1:
            c.execute('INSERT OR REPLACE INTO settings (setting_name, setting_value) VALUES (?, ?)', ("elo_multiplier", "on"))
            await interaction.response.send_message("ELO multiplier has been turned ON :sparkles:. Winners will now receive double the ELO points!")
        else:
            c.execute('INSERT OR REPLACE INTO settings (setting_name, setting_value) VALUES (?, ?)', ("elo_multiplier", "off"))
            await interaction.response.send_message("ELO multiplier has been turned OFF. Winners will now receive the regular ELO points.")
        conn.commit()

@app_commands.command(name='set_inactive', description='Mark a player as inactive')
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
async def set_inactive(interaction, player_id: str):
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        player_id = int(player_id)
        c.execute("UPDATE elo_data SET inactive = 1 WHERE player_id = ?", (player_id,))
        conn.commit()
        await interaction.response.send_message(f"Player with ID {player_id} has been set to inactive :man_detective:")

@app_commands.command(name='set_active', description='Mark a player as active')
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
async def set_active(interaction, player_id: str):
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        player_id = int(player_id)
        c.execute("UPDATE elo_data SET inactive = 0 WHERE player_id = ?", (player_id,))
        conn.commit()
        await interaction.response.send_message(f"Player with ID {player_id} has been set to active")

@app_commands.command(name='get_player_id')
async def get_player_id(interaction, member: discord.Member):
    """Get the player ID of a member"""
    await interaction.response.send_message(f"The player ID for {member.name} is {member.id}.")

@app_commands.command(name="list_inactive", description="Lists all inactive players")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
async def list_inactive(interaction: discord.Interaction):
    """Lists all players marked as inactive in the database."""
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute("SELECT player_id FROM elo_data WHERE inactive = 1")
        inactive_players = c.fetchall()

    if not inactive_players:
        await interaction.response.send_message("No inactive players found.")
        return

    guild = interaction.guild
    found_members = []
    missing_members = []

    for (player_id,) in inactive_players:
        member = guild.get_member(player_id)  # Try to fetch member from server
        if member:
            found_members.append(member.name)  # Mention active members
        else:
            missing_members.append(f"`{player_id}`")  # List non-existent members as IDs

    # Construct message
    message = "**Inactive Players:**\n"
    if found_members:
        message += "\n".join(found_members) + "\n"
    if missing_members:
        message += "\n**Players no longer in the server:**\n" + ", ".join(missing_members)

    await interaction.response.send_message(message)

@app_commands.command(name="clean_commands", description="Force clean all global commands and resync only to this guild.")
@discord.app_commands.checks.has_permissions(administrator=True)
async def clean_commands(interaction: discord.Interaction):
    guild = interaction.guild

    # ✅ Clear all GLOBAL commands (not async!)
    interaction.client.tree.clear_commands(guild=None)

    # ✅ Sync to remove old global commands
    await interaction.client.tree.sync(guild=None)

    # ✅ Resync current guild commands
    await interaction.client.tree.sync(guild=guild)

    await interaction.response.send_message("✅ Global commands cleared and guild commands re-synced.", ephemeral=True)

@app_commands.command(name="reset_all_elo", description="Reset everyone's ELO to 1200 (dangerous)")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
async def reset_all_elo(interaction: discord.Interaction):
    warning = (
        "⚠️ **This will reset ALL players' ELO to 1200.**\n"
        "This is a big irreversible operation.\n\n"
        "React with ✅ within 10 seconds to confirm."
    )

    # Create an embed for the warning
    embed = discord.Embed(
        title="⚠️ ELO Reset Confirmation",
        description=warning,
        color=discord.Color.red()
    )

    # Send the embed
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("✅")

    def check(reaction, user):
        return (
            str(reaction.emoji) == "✅"
            and user.id == interaction.user.id
            and reaction.message.id == msg.id
        )

    try:
        reaction, user = await interaction.client.wait_for(
            "reaction_add", timeout=10.0, check=check
        )
    except asyncio.TimeoutError:
        await interaction.followup.send("⏳ Timed out. No changes made.")
        return

    # If confirmed: first make a backup, then reset
    try:
#        backup_name = f"pre_reset_{time.strftime('%Y%m%d-%H%M%S')}"
        backup_name = f"pre_reset_{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        backup_db(custom_name=backup_name, folder='backups_auto')

        with sqlite3.connect('elo_data.db') as conn:
            c = conn.cursor()
            c.execute("UPDATE elo_data SET elo = 1200")
            changed = c.rowcount
            conn.commit()
    except Exception as e:
        await interaction.followup.send(f"❌ Backup or reset failed: {e}\nNo changes were made.")
        return

    await interaction.followup.send(
        f"🗄️ Backup created: `{backup_name}.db` in `backups/backups_auto/`.\n"
        f"✅ Reset complete. Set ELO to 1200 for **{changed}** players."
    )


async def setup(bot):
    bot.tree.add_command(register)
    bot.tree.add_command(report)
    bot.tree.add_command(elo)
    bot.tree.add_command(change_elo)
    bot.tree.add_command(game)
    bot.tree.add_command(remove_game)
    bot.tree.add_command(highest_elo)
    bot.tree.add_command(toggle_elo_multiplier)
    bot.tree.add_command(set_inactive)
    bot.tree.add_command(set_active)
    bot.tree.add_command(get_player_id)
    bot.tree.add_command(list_inactive)
    bot.tree.add_command(clean_commands)
    bot.tree.add_command(reset_all_elo)
