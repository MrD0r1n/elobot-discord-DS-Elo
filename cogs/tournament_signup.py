import sqlite3
import discord
from discord import app_commands
import datetime
from io import BytesIO
import asyncio
import settings

# Create database table for tournament signups
with sqlite3.connect('elo_data.db') as conn:
    c = conn.cursor()
    
    try:
        # Create the table if it doesn't exist
        c.execute('''
            CREATE TABLE IF NOT EXISTS tournament_signups (
                signup_id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                user_id INTEGER,
                username TEXT,
                signup_date TEXT,
                tournament_name TEXT,
                is_closed INTEGER DEFAULT 0
            )
        ''')
    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
        conn.rollback()

class TournamentSignupView(discord.ui.View):
    def __init__(self, tournament_name, timeout=None):
        super().__init__(timeout=timeout)
        self.tournament_name = tournament_name
        self.is_closed = False

    def update_buttons(self):
        """Update button states based on whether signups are closed"""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.label in ["Sign Up", "Sign Out"]:
                    item.disabled = self.is_closed
                    if self.is_closed:
                        item.style = discord.ButtonStyle.gray
                    else:
                        if item.label == "Sign Up":
                            item.style = discord.ButtonStyle.green
                        else:
                            item.style = discord.ButtonStyle.red

    async def update_signup_count(self, interaction: discord.Interaction):
        """Update the embed to show current signup count"""
        try:
            message = interaction.message
            if not message.embeds:
                return
                
            embed = message.embeds[0]
            
            # Get current signup count
            with sqlite3.connect('elo_data.db') as conn:
                c = conn.cursor()
                c.execute('''
                    SELECT COUNT(*) FROM tournament_signups 
                    WHERE message_id = ? AND tournament_name = ?
                ''', (message.id, self.tournament_name))
                signup_count = c.fetchone()[0]
            
            # Update the embed description to include signup count
            status_line = f"**Status:** 🟢 **OPEN** - **Total Signups: {signup_count}**"
            
            if "**Status:**" in embed.description:
                # Replace existing status line
                lines = embed.description.split('\n')
                for i, line in enumerate(lines):
                    if line.startswith("**Status:**"):
                        lines[i] = status_line
                        break
                embed.description = '\n'.join(lines)
            else:
                # Add status line at the beginning
                embed.description = status_line + "\n\n" + embed.description
            
            await message.edit(embed=embed)
            
        except Exception as e:
            print(f"Error updating signup count: {e}")

    async def handle_signup(self, interaction: discord.Interaction):
        if self.is_closed:
            await interaction.response.send_message(
                f"❌ Signups for **{self.tournament_name}** are currently closed!", 
                ephemeral=True
            )
            return

        user_id = interaction.user.id
        username = interaction.user.name
        message_id = interaction.message.id
        signup_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Check if user is already signed up
        with sqlite3.connect('elo_data.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT * FROM tournament_signups 
                WHERE message_id = ? AND user_id = ? AND tournament_name = ?
            ''', (message_id, user_id, self.tournament_name))
            existing_signup = c.fetchone()

            if existing_signup:
                await interaction.response.send_message(
                    f"{interaction.user.mention} You are already signed up for this tournament!", 
                    ephemeral=True
                )
                return

            # Add signup to database
            c.execute('''
                INSERT INTO tournament_signups (message_id, user_id, username, signup_date, tournament_name)
                VALUES (?, ?, ?, ?, ?)
            ''', (message_id, user_id, username, signup_date, self.tournament_name))
            conn.commit()

        await interaction.response.send_message(
            f"{interaction.user.mention} successfully signed up for **{self.tournament_name}**! ✅",
            ephemeral=True
        )

        # Grant the Tournament Contender role (marks them as playing in this tournament,
        # used to gate access to tournament-only channels) - separate from the Challenger
        # rank role, which is only earned through match results.
        if interaction.guild is not None and isinstance(interaction.user, discord.Member):
            tournament_contender_role = interaction.guild.get_role(settings.ROLE_TOURNAMENT_CONTENDER)
            if tournament_contender_role is None:
                await interaction.followup.send(
                    ":warning:  Couldn't find the Tournament Contender role in this server "
                    "(check ROLE_TOURNAMENT_CONTENDER in settings.py/.env) — skipping role assignment.",
                    ephemeral=True,
                )
            else:
                await interaction.user.add_roles(tournament_contender_role)

        # Update the signup count in the embed
        await self.update_signup_count(interaction)

    async def handle_signout(self, interaction: discord.Interaction):
        if self.is_closed:
            await interaction.response.send_message(
                f"❌ Signups for **{self.tournament_name}** are currently closed!", 
                ephemeral=True
            )
            return

        user_id = interaction.user.id
        message_id = interaction.message.id

        # Check if user is signed up
        with sqlite3.connect('elo_data.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT * FROM tournament_signups 
                WHERE message_id = ? AND user_id = ? AND tournament_name = ?
            ''', (message_id, user_id, self.tournament_name))
            existing_signup = c.fetchone()

            if not existing_signup:
                await interaction.response.send_message(
                    f"{interaction.user.mention} You are not signed up for this tournament!", 
                    ephemeral=True
                )
                return

            # Remove signup from database
            c.execute('''
                DELETE FROM tournament_signups 
                WHERE message_id = ? AND user_id = ? AND tournament_name = ?
            ''', (message_id, user_id, self.tournament_name))
            conn.commit()

        await interaction.response.send_message(
            f"{interaction.user.mention} successfully signed out from **{self.tournament_name}**! ❌",
            ephemeral=True
        )

        # Remove the Tournament Contender role granted on signup
        if interaction.guild is not None and isinstance(interaction.user, discord.Member):
            tournament_contender_role = interaction.guild.get_role(settings.ROLE_TOURNAMENT_CONTENDER)
            if tournament_contender_role is not None:
                await interaction.user.remove_roles(tournament_contender_role)

        # Update the signup count in the embed
        await self.update_signup_count(interaction)

    async def handle_show_players(self, interaction: discord.Interaction):
        message_id = interaction.message.id
        
        with sqlite3.connect('elo_data.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT username, signup_date FROM tournament_signups 
                WHERE message_id = ? ORDER BY signup_date
            ''', (message_id,))
            signups = c.fetchall()
        
        if not signups:
            await interaction.response.send_message("No players signed up for this tournament.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title=f"👥 Players Signed Up: {self.tournament_name}",
            color=discord.Color.red() if self.is_closed else discord.Color.blue()
        )
        
        signup_list = "\n".join([f"• {username} - {signup_date}" for username, signup_date in signups])
        embed.description = f"**Total Signups: {len(signups)}**\n\n{signup_list}"
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Sign Up", style=discord.ButtonStyle.green, emoji="✅")
    async def sign_up_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_signup(interaction)

    @discord.ui.button(label="Sign Out", style=discord.ButtonStyle.red, emoji="❌")
    async def sign_out_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_signout(interaction)

    @discord.ui.button(label="Show Players", style=discord.ButtonStyle.gray, emoji="👥")
    async def show_players_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_show_players(interaction)

# Commands
@app_commands.command(name="create_tournament_signup", description="Create a tournament signup message")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
@app_commands.describe(
    tournament_name="Name of the tournament",
    close_after_hours="Automatically close signups after X hours (optional)",
    recover_message_id="If an old signup message stopped working (e.g. after a bot restart), "
                        "paste its message ID & name here to migrate those signups onto this fresh message (optional)"
)
async def create_tournament_signup(
    interaction: discord.Interaction,
    tournament_name: str,
    close_after_hours: int = None,
    recover_message_id: str = None,
):
    """Create an embedded message for tournament signups with a button"""

    # If recovering, count what's already signed up under the old (now-dead-button) message
    recovered_count = 0
    old_message_id_int = None
    if recover_message_id:
        try:
            old_message_id_int = int(recover_message_id)
        except ValueError:
            await interaction.response.send_message("❌ recover_message_id must be a number.", ephemeral=True)
            return
        with sqlite3.connect('elo_data.db') as conn:
            c = conn.cursor()
            c.execute(
                'SELECT COUNT(*) FROM tournament_signups WHERE message_id = ? AND tournament_name = ?',
                (old_message_id_int, tournament_name)
            )
            recovered_count = c.fetchone()[0]

    embed = discord.Embed(
        title=f"🏆 {tournament_name} - Sign Up",
        description=(
            "Click the buttons below to manage your tournament registration!\n\n"
            f"**Status:** 🟢 **OPEN** - **Total Signups: {recovered_count}**\n\n"
            "**Info:**\n"
            "• Receive the tournament contender role on sing up\n"
            "• Tournament details will be announced in #info-and-date"
        ),
        color=discord.Color.green(),
        timestamp=datetime.datetime.now()
    )

    if close_after_hours:
        close_time = datetime.datetime.now() + datetime.timedelta(hours=close_after_hours)
        embed.description += f"\n\n⏰ **Signups will automatically close:** {close_time.strftime('%Y-%m-%d %H:%M:%S')}"

    embed.set_footer(text="Tournament Signups")

    view = TournamentSignupView(tournament_name=tournament_name)

    # Send the message and get the actual message object
    await interaction.response.send_message(embed=embed, view=view)
    message = await interaction.original_response()

    # Migrate recovered signups onto the new message
    if old_message_id_int is not None and recovered_count > 0:
        with sqlite3.connect('elo_data.db') as conn:
            c = conn.cursor()
            c.execute(
                'UPDATE tournament_signups SET message_id = ? WHERE message_id = ? AND tournament_name = ?',
                (message.id, old_message_id_int, tournament_name)
            )
            conn.commit()

        await interaction.followup.send(
            f"✅ Recovered {recovered_count} signup(s) from message ID `{old_message_id_int}` onto this new message.",
            ephemeral=True
        )

    # Schedule automatic closing if timer is set
    if close_after_hours:
        await schedule_signup_close(interaction.client, interaction.channel_id, message.id, tournament_name, close_after_hours)

async def schedule_signup_close(bot, channel_id, message_id, tournament_name, hours):
    """Schedule automatic closing of signups"""
    await asyncio.sleep(hours * 3600)  # Convert hours to seconds
    
    try:
        channel = bot.get_channel(channel_id)
        if channel:
            message = await channel.fetch_message(message_id)
            if message:
                # Update the embed to show closed status
                embed = message.embeds[0]
                embed.color = discord.Color.red()
                
                # Update description to show closed
                if "**Status:**" in embed.description:
                    # Get current signup count
                    with sqlite3.connect('elo_data.db') as conn:
                        c = conn.cursor()
                        c.execute('''
                            SELECT COUNT(*) FROM tournament_signups 
                            WHERE message_id = ? AND tournament_name = ?
                        ''', (message.id, tournament_name))
                        signup_count = c.fetchone()[0]
                    
                    # Replace any existing status line
                    lines = embed.description.split('\n')
                    for i, line in enumerate(lines):
                        if line.startswith("**Status:**"):
                            lines[i] = f"**Status:** 🔴 **CLOSED** - **Total Signups: {signup_count}**"
                            break
                    embed.description = '\n'.join(lines)
                
                # Update the view to disable buttons
                if message.components:
                    view = TournamentSignupView(tournament_name=tournament_name)
                    view.is_closed = True
                    view.update_buttons()
                    
                    await message.edit(embed=embed, view=view)
    except Exception as e:
        print(f"Error auto-closing tournament signups: {e}")

@app_commands.command(name="close_tournament_signup", description="Close tournament signups")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
@app_commands.describe(message_id="The message ID of the tournament signup")
async def close_tournament_signup(interaction: discord.Interaction, message_id: str):
    """Close tournament signups for a specific message"""
    
    try:
        message = await interaction.channel.fetch_message(int(message_id))
    except discord.NotFound:
        await interaction.response.send_message("❌ Message not found in this channel!", ephemeral=True)
        return
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to access that message!", ephemeral=True)
        return
    
    # Update the embed to show closed status
    if not message.embeds:
        await interaction.response.send_message("❌ This message doesn't have an embed!", ephemeral=True)
        return
    
    embed = message.embeds[0]
    embed.color = discord.Color.red()
    
    # Get current signup count
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('''
            SELECT COUNT(*) FROM tournament_signups 
            WHERE message_id = ?
        ''', (int(message_id),))
        signup_count = c.fetchone()[0]
    
    # Update description to show closed
    if "**Status:**" in embed.description:
        # Replace any existing status line
        lines = embed.description.split('\n')
        for i, line in enumerate(lines):
            if line.startswith("**Status:**"):
                lines[i] = f"**Status:** 🔴 **CLOSED** - **Total Signups: {signup_count}**"
                break
        embed.description = '\n'.join(lines)
    else:
        # Add status if it doesn't exist
        embed.description = f"**Status:** 🔴 **CLOSED** - **Total Signups: {signup_count}**\n\n" + embed.description
    
    # Update the view to disable buttons
    view = None
    if message.components:
        for component in message.components:
            if component.children:
                # Get tournament name from embed title
                tournament_name = embed.title.replace("🏆 ", "").replace(" - Sign Up", "")
                view = TournamentSignupView(tournament_name=tournament_name)
                view.is_closed = True
                view.update_buttons()
                break
    
    await message.edit(embed=embed, view=view)
    
    # Update database
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE tournament_signups 
            SET is_closed = 1 
            WHERE message_id = ?
        ''', (int(message_id),))
        conn.commit()
    
    await interaction.response.send_message(f"✅ Tournament signups for message ID `{message_id}` have been closed!")

@app_commands.command(name="reopen_tournament_signup", description="Reopen tournament signups")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
@app_commands.describe(message_id="The message ID of the tournament signup")
async def reopen_tournament_signup(interaction: discord.Interaction, message_id: str):
    """Reopen tournament signups for a specific message"""
    
    try:
        message = await interaction.channel.fetch_message(int(message_id))
    except discord.NotFound:
        await interaction.response.send_message("❌ Message not found in this channel!", ephemeral=True)
        return
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to access that message!", ephemeral=True)
        return
    
    # Update the embed to show open status
    if not message.embeds:
        await interaction.response.send_message("❌ This message doesn't have an embed!", ephemeral=True)
        return
    
    embed = message.embeds[0]
    embed.color = discord.Color.green()
    
    # Get current signup count
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('''
            SELECT COUNT(*) FROM tournament_signups 
            WHERE message_id = ?
        ''', (int(message_id),))
        signup_count = c.fetchone()[0]
    
    # Update description to show open
    if "**Status:**" in embed.description:
        # Replace any existing status line
        lines = embed.description.split('\n')
        for i, line in enumerate(lines):
            if line.startswith("**Status:**"):
                lines[i] = f"**Status:** 🟢 **OPEN** - **Total Signups: {signup_count}**"
                break
        embed.description = '\n'.join(lines)
    else:
        # Add status if it doesn't exist
        embed.description = f"**Status:** 🟢 **OPEN** - **Total Signups: {signup_count}**\n\n" + embed.description
    
    # Update the view to enable buttons
    view = None
    if message.components:
        for component in message.components:
            if component.children:
                # Get tournament name from embed title
                tournament_name = embed.title.replace("🏆 ", "").replace(" - Sign Up", "")
                view = TournamentSignupView(tournament_name=tournament_name)
                view.is_closed = False
                view.update_buttons()
                break
    
    await message.edit(embed=embed, view=view)
    
    # Update database
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE tournament_signups 
            SET is_closed = 0 
            WHERE message_id = ?
        ''', (int(message_id),))
        conn.commit()
    
    await interaction.response.send_message(f"✅ Tournament signups for message ID `{message_id}` have been reopened!")

@app_commands.command(name="list_tournament_signups", description="List all users signed up for a tournament")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
async def list_tournament_signups(interaction: discord.Interaction, message_id: str = None, tournament_name: str = None):
    """List all signups for a specific tournament message or tournament name"""
    
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        
        if message_id:
            # Get signups by message ID
            c.execute('''
                SELECT username, signup_date FROM tournament_signups 
                WHERE message_id = ? ORDER BY signup_date
            ''', (int(message_id),))
        elif tournament_name:
            # Get signups by tournament name
            c.execute('''
                SELECT username, signup_date FROM tournament_signups 
                WHERE tournament_name = ? ORDER BY signup_date
            ''', (tournament_name,))
        else:
            # Get all signups
            c.execute('''
                SELECT username, tournament_name, signup_date FROM tournament_signups 
                ORDER BY tournament_name, signup_date
            ''')
        
        signups = c.fetchall()
    
    if not signups:
        await interaction.response.send_message("No tournament signups found.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🏆 Tournament Signups",
        color=discord.Color.blue()
    )
    
    if message_id:
        embed.title = f"Signups for Message ID: {message_id}"
        signup_list = "\n".join([f"• {username} - {signup_date}" for username, signup_date in signups])
        embed.description = f"**Total Signups: {len(signups)}**\n\n{signup_list}"
    
    elif tournament_name:
        embed.title = f"Signups for: {tournament_name}"
        signup_list = "\n".join([f"• {username} - {signup_date}" for username, signup_date in signups])
        embed.description = f"**Total Signups: {len(signups)}**\n\n{signup_list}"
    
    else:
        # Group by tournament name
        tournaments = {}
        for username, tournament, signup_date in signups:
            if tournament not in tournaments:
                tournaments[tournament] = []
            tournaments[tournament].append(f"• {username} - {signup_date}")
        
        for tournament, users in tournaments.items():
            embed.add_field(
                name=f"{tournament} ({len(users)} players)",
                value="\n".join(users[:10]) + ("\n..." if len(users) > 10 else ""),
                inline=False
            )
    
    await interaction.response.send_message(embed=embed)

@app_commands.command(name="clear_tournament_signups", description="Clear all signups for a tournament")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
async def clear_tournament_signups(interaction: discord.Interaction, message_id: str = None, tournament_name: str = None):
    """Clear signups for a specific message or tournament"""
    
    if not message_id and not tournament_name:
        await interaction.response.send_message(
            "Please specify either a message_id or tournament_name to clear signups.", 
            ephemeral=True
        )
        return
    
    # First, check how many signups will be deleted
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        
        if message_id:
            c.execute('SELECT COUNT(*) FROM tournament_signups WHERE message_id = ?', (int(message_id),))
            action = f"message ID {message_id}"
        else:
            c.execute('SELECT COUNT(*) FROM tournament_signups WHERE tournament_name = ?', (tournament_name,))
            action = f"tournament '{tournament_name}'"
        
        signup_count = c.fetchone()[0]
    
    if signup_count == 0:
        await interaction.response.send_message(
            f"No signups found for {action}.", 
            ephemeral=True
        )
        return
    
    warning = (
        f"⚠️ **This will permanently delete {signup_count} tournament signup(s) for {action}.**\n"
        "This action cannot be undone!\n\n"
        "React with ✅ within 10 seconds to confirm."
    )

    # Create an embed for the warning
    embed = discord.Embed(
        title="⚠️ Clear Tournament Signups",
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
        await interaction.followup.send("⏳ Timed out. No signups were deleted.")
        return

    # If confirmed: delete the signups, and grab who was signed up first so their
    # Tournament Contender role can be removed below
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()

        if message_id:
            c.execute('SELECT DISTINCT user_id FROM tournament_signups WHERE message_id = ?', (int(message_id),))
        else:
            c.execute('SELECT DISTINCT user_id FROM tournament_signups WHERE tournament_name = ?', (tournament_name,))
        signed_up_user_ids = [row[0] for row in c.fetchall()]

        if message_id:
            c.execute('DELETE FROM tournament_signups WHERE message_id = ?', (int(message_id),))
        else:
            c.execute('DELETE FROM tournament_signups WHERE tournament_name = ?', (tournament_name,))

        deleted_count = c.rowcount
        conn.commit()

    # Remove the Tournament Contender role from everyone who was signed up
    roles_removed = 0
    guild = interaction.guild
    tournament_contender_role = guild.get_role(settings.ROLE_TOURNAMENT_CONTENDER) if guild else None
    if guild is not None and tournament_contender_role is not None:
        for user_id in signed_up_user_ids:
            member = guild.get_member(user_id)
            if member is not None and tournament_contender_role in member.roles:
                await member.remove_roles(tournament_contender_role)
                roles_removed += 1

    await interaction.followup.send(
        f"🗑️ Successfully deleted {deleted_count} signup(s) for {action}."
        + (f"\nRemoved the Tournament Contender role from {roles_removed} player(s)." if roles_removed else "")
    )

@app_commands.command(name="export_tournament_signups", description="Export tournament signups as a text file")
@discord.app_commands.checks.has_any_role(*settings.STAFF_ROLES)
async def export_tournament_signups(interaction: discord.Interaction, tournament_name: str):
    """Export signups to a text file"""
    
    with sqlite3.connect('elo_data.db') as conn:
        c = conn.cursor()
        c.execute('''
            SELECT username, signup_date FROM tournament_signups 
            WHERE tournament_name = ? ORDER BY signup_date
        ''', (tournament_name,))
        signups = c.fetchall()
    
    if not signups:
        await interaction.response.send_message(f"No signups found for tournament '{tournament_name}'.", ephemeral=True)
        return
    
    # Create text content
    content = f"Tournament Signups: {tournament_name}\n"
    content += f"Export Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    content += f"Total Players: {len(signups)}\n\n"
    
    for i, (username, signup_date) in enumerate(signups, 1):
        content += f"{i}. {username} - Signed up: {signup_date}\n"
    
    # Create file
    filename = f"tournament_signups_{tournament_name.replace(' ', '_')}.txt"
    file = discord.File(fp=BytesIO(content.encode()), filename=filename)
    
    await interaction.response.send_message(
        f"📊 Exported {len(signups)} signups for **{tournament_name}**", 
        file=file
    )

async def setup(bot):
    """Add tournament commands to the bot"""
    bot.tree.add_command(create_tournament_signup)
    bot.tree.add_command(close_tournament_signup)
    bot.tree.add_command(reopen_tournament_signup)
    bot.tree.add_command(list_tournament_signups)
    bot.tree.add_command(clear_tournament_signups)
    bot.tree.add_command(export_tournament_signups)
