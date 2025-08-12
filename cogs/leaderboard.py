import discord
import settings
from discord import app_commands
import sqlite3
from cogs.paginator import PaginationView
import datetime
from collections import defaultdict

leaderboard_channel = settings.LEADERBOARD_CHANNEL_ID
logger = settings.logging.getLogger("bot")


class LeaderboardView(discord.ui.View):
    """Leaderboard view."""

    def __init__(self, interaction:discord.Interaction, filter_mode: str="months", filter_data: int=0):
        super().__init__(timeout=None)
        self.bot = interaction.client
        self.interaction = interaction
        self.filter_mode = filter_mode # "months" or "gameid"
        self.filter_data = filter_data # Number of months or game ID


    def create_embed(self, data):
        """Create the embed for the current page."""
        embed = discord.Embed(
            title = "Current Leaderboard \t\t\t\t\t\t\t\u200b",
            description = "Top 10 ranked ELO players",
            color=discord.Color.blue()
        )
        now = datetime.datetime.utcnow()
        
        if data:
            data[-1] = data[-1] + "\n\u200b"
        
        embed.add_field(name="\u200b", value="\n".join(data), inline=False)

        if self.filter_mode == "months":
            filter_text = f"last {self.filter_data} month(s)"
        if self.filter_mode == "gameid":
            filter_text = f"since game no.{self.filter_data}"

        embed.set_footer(
            text=f"{self.bot.user.name} • filter: {filter_text}",
            icon_url=self.bot.user.display_avatar.url
        )
        return embed

    #start new code

    def _filter_relevant_players_query(self):
        # Returns (sql, params) for relevant players based on filter
        if self.filter_mode == "months" and self.filter_data > 0:
            cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(days=1 * self.filter_data)
            cutoff_str = cutoff_date.strftime('%Y-%m-%d %H:%M:%S')
            sql = """
                SELECT DISTINCT p FROM (
                    SELECT winner_id AS p FROM match_data WHERE date >= ?
                    UNION
                    SELECT loser_id  AS p FROM match_data WHERE date >= ?
                )
            """
            return sql, [cutoff_str, cutoff_str]
        elif self.filter_mode == "gameid":
            sql = """
                SELECT DISTINCT p FROM (
                    SELECT winner_id AS p FROM match_data WHERE game_id >= ?
                    UNION
                    SELECT loser_id  AS p FROM match_data WHERE game_id >= ?
                )
            """
            return sql, [self.filter_data, self.filter_data]
        else:
            # default: all active players
            return "SELECT player_id AS p FROM elo_data WHERE inactive = 0", []

    def _build_context(self, conn, current_top_rows):
        """
        Build all in-memory structures we need in one go:
        - relevant_players
        - current_elo_map
        - elo_5_days_ago_map (using your 3-step logic)
        - old_rankings (sorted by elo_5_days_ago)
        - streaks
        """
        c = conn.cursor()

        # relevant players
        rel_sql, rel_params = self._filter_relevant_players_query()
        c.execute(rel_sql, rel_params)
        relevant_players = {row[0] for row in c.fetchall()}
        if not relevant_players:
            self._old_rankings = {}
            self._streaks = {}
            self._now_rankings = {}
            return

        # Last match date per relevant player (one grouped query)
        placeholders = ",".join(["?"] * len(relevant_players))
        c.execute(f"""
            SELECT player_id, MAX(date) AS last_date
            FROM (
                SELECT winner_id AS player_id, date FROM match_data WHERE winner_id IN ({placeholders})
                UNION ALL
                SELECT loser_id  AS player_id, date FROM match_data WHERE loser_id  IN ({placeholders})
            ) t
            GROUP BY player_id
        """, tuple([*relevant_players, *relevant_players]))

        self._last_match_date = {pid: d for pid, d in c.fetchall()}

        # current ELO for relevant players
        placeholders = ",".join(["?"] * len(relevant_players))
        c.execute(f"SELECT player_id, elo FROM elo_data WHERE player_id IN ({placeholders}) AND inactive = 0", tuple(relevant_players))
        current_elo_map = {pid: elo for pid, elo in c.fetchall()}

        # prepare time window
        now = datetime.datetime.utcnow()
        five_days_before = (now - datetime.timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S')
        now_str = now.strftime('%Y-%m-%d %H:%M:%S')

        # all matches for relevant players in the last 5 days (winner/loser unified)
        # We select: player_id, date, elo_after_match
        match_union_sql = f"""
            SELECT winner_id AS player_id, date, elo_winner AS elo_after
            FROM match_data
            WHERE date >= ? AND winner_id IN ({placeholders})
            UNION ALL
            SELECT loser_id  AS player_id, date, elo_loser  AS elo_after
            FROM match_data
            WHERE date >= ? AND loser_id  IN ({placeholders})
        """
        params = [five_days_before, *relevant_players, five_days_before, *relevant_players]
        c.execute(match_union_sql, tuple(params))
        rows = c.fetchall()

        # group by player -> list of (date, elo_after)
        per_player = defaultdict(list)
        for pid, d, elo_after in rows:
            per_player[pid].append((d, elo_after))

        # sort per player by date ASC to find "first after 5 days ago"
        for pid in per_player:
            per_player[pid].sort(key=lambda x: x[0])

        # apply 3-step logic to determine "elo_5_days_ago"
        elo_5_days_ago_map = {}
        for pid in relevant_players:
            entries = per_player.get(pid, [])

            # Step 1: first match AFTER 5 days ago (date > five_days_before) -> with our filter it's >=; we emulate ">" by skipping == if desired
            first_after = None
            for d, e in entries:
                if d > five_days_before:
                    first_after = e
                    break

            # Step 2: if none, oldest match WITHIN last 5 days (same window; already sorted asc)
            oldest_within = entries[0][1] if entries else None

            # Choose according to logic; if step1 failed, use step2
            candidate = first_after if first_after is not None else oldest_within

            # Step 3: if still none, fallback to current ELO
            if candidate is None:
                candidate = current_elo_map.get(pid)

            if candidate is not None:
                elo_5_days_ago_map[pid] = candidate

        # old rankings (desc by elo)
        self._old_rankings = {
            pid: rank + 1
            for rank, (pid, _) in enumerate(sorted(elo_5_days_ago_map.items(), key=lambda x: x[1], reverse=True))
        }

        # winning streaks: need recent matches ordered by date DESC for all relevant players
        # (use a single query; then count consecutive wins from most recent backwards)
        # We only need winner_id/loser_id/date to count wins until first loss.
        streak_sql = f"""
            SELECT winner_id, loser_id, date
            FROM match_data
            WHERE winner_id IN ({placeholders}) OR loser_id IN ({placeholders})
            ORDER BY date DESC
        """
        params = [*relevant_players, *relevant_players]
        c.execute(streak_sql, tuple(params))
        all_matches = c.fetchall()


        streaks = defaultdict(int)      # or keep your dict and use .get
        seen_loss_break = defaultdict(bool)

        for w, l, d in all_matches:
            if w in relevant_players and not seen_loss_break[w]:
                streaks[w] += 1
            if l in relevant_players and not seen_loss_break[l]:
                seen_loss_break[l] = True

        self._streaks = streaks

        # now rankings (current page set): map player_id -> current rank
        self._now_rankings = {}
        for idx, (pid, _elo) in enumerate(current_top_rows, start=1):
            self._now_rankings[pid] = idx

    #end new code

    #begin code2

    async def get_leaderboard_data(self, interaction, limit: int = 10):
        try:
            data = []
            query = """
                SELECT e.player_id, e.elo
                FROM elo_data e
                WHERE e.inactive = 0
            """
            params = []

            if self.filter_mode == "months":
                if self.filter_data > 0:
                    cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(days=30 * self.filter_data)
                    cutoff_str = cutoff_date.strftime('%Y-%m-%d %H:%M:%S')
                    query = """
                        SELECT DISTINCT e.player_id, e.elo
                        FROM elo_data e
                        JOIN (
                            SELECT winner_id AS p FROM match_data WHERE date >= ?
                            UNION
                            SELECT loser_id  AS p FROM match_data WHERE date >= ?
                        ) sub ON sub.p = e.player_id
                        WHERE e.inactive = 0
                    """
                    params = [cutoff_str, cutoff_str]

            elif self.filter_mode == "gameid":
                query = """
                    SELECT DISTINCT e.player_id, e.elo
                    FROM elo_data e
                    JOIN (
                        SELECT winner_id AS p FROM match_data WHERE game_id >= ?
                        UNION
                        SELECT loser_id  AS p FROM match_data WHERE game_id >= ?
                    ) sub ON sub.p = e.player_id
                    WHERE e.inactive = 0
                """
                params = [self.filter_data, self.filter_data]

            query += " ORDER BY e.elo DESC LIMIT ?"
            params.append(limit)

            with sqlite3.connect('elo_data.db') as conn:
                c = conn.cursor()
                c.execute(query, params)
                elo_rows = c.fetchall()  # [(player_id, elo), ...]

                # Build context once for all players shown
                self._build_context(conn, elo_rows)

                for rank, (player_id, elo) in enumerate(elo_rows, start=1):
                    movement = self.get_movement_emoji(player_id, rank)  # now a cheap lookup
                    rank_str = f"`{rank}) `" if rank > 3 else [":first_place:", ":second_place:", ":third_place:"][rank - 1] + " \u200b"
                    data.append(f"{rank_str} <@{player_id}> **({elo})** {movement}")
                    if rank == 3:
                        data.append("\u200b")

            return data

        except sqlite3.Error as e:
            logger.error(f"Database error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")

        return data

    #end new code2

    #begin code 3

    def get_movement_emoji(self, player_id, current_rank):
        # Only show movement if last played match was >= 30 days ago
        last_str = getattr(self, "_last_match_date", {}).get(player_id)
        if not last_str:
            return ""  # no matches known → show nothing

        # Parse to datetime (adjust format if your DB uses a different one)
        try:
            last_dt = datetime.datetime.strptime(last_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            last_dt = datetime.datetime.fromisoformat(last_str)

        if (datetime.datetime.utcnow() - last_dt).days > 30:
            return ""  # too recent → no emoji

        # existing rank movement + streak logic
        old_rank = self._old_rankings.get(player_id)
        if old_rank is None:
            return ""

        rank_difference = old_rank - current_rank
        movement = "<:testria7:1147540299434967081>" if rank_difference > 0 else \
                   ":small_red_triangle_down:" if rank_difference < 0 else ""

        streak = self._streaks.get(player_id, 0)
        rules = [
            (streak >= 3,  ":fire:"),
            (streak == 6,  ":boom:"),
            (streak == 7,  ":metal:"),
            (streak == 8,  ":rocket:"),
            (streak == 9,  ":trophy:"),
            (streak >= 10, ":crown:"),
            (streak >= 14, ":man_mage:"),
            (streak >= 20, ":goat:"),
        ]
        for cond, e in rules[::-1]:
            if cond:
                return f"{movement}{e}"
        return movement

   #end new code 3

    async def get_paginator_data(self):
        return await self.get_leaderboard_data(self.interaction, limit=200)


    @discord.ui.button(label='See all', style=discord.ButtonStyle.primary)
    async def button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)       


        title = "Leaderboard     \t\t\t\t\t\t\t\t\t\t\u200b"
        description = "Top ranking ELO players"
        embed_color = discord.Color.blue()

        data = await self.get_paginator_data()

        pagination_view = PaginationView(interaction, title, description, embed_color, ephemeral=True)
        pagination_view.data = data
        await pagination_view.send()


async def send_leaderboard_mentions(channel, player_mentions):
    """Handles sending large numbers of mentions in multiple messages while avoiding pings."""
    
    MAX_MESSAGE_LENGTH = 1000  # Discord message limit
    BASE_MESSAGE = "This message is so all names are visible/cached in lb: "  # Prefix text
    BASE_LENGTH = len(BASE_MESSAGE)  # Length of prefix text

    chunks = []
    current_chunk = BASE_MESSAGE
    for mention in player_mentions:
        mention_length = len(mention) + 6  # "||" for and after + ", " separator
        
        if len(current_chunk) + mention_length > MAX_MESSAGE_LENGTH:
            chunks.append(f"||{current_chunk}||")  # Store the full chunk
            current_chunk = BASE_MESSAGE + mention  # Start a new chunk
        else:
            current_chunk += (", " if current_chunk != BASE_MESSAGE else "") + mention

    if current_chunk:  # Add the last chunk
        chunks.append(f"||{current_chunk}||")

    # Now, send a placeholder for each chunk before editing it
    messages = []
    for _ in chunks:
        msg = await channel.send("Caching leaderboard names...")  # Send placeholder
        messages.append(msg)

    # Now edit each message with the corresponding chunk
    for msg, chunk in zip(messages, chunks):
        await msg.edit(content=chunk)  # Edit each message with correct mentions

@app_commands.command(name="set_leaderbord", description="Sets the leaderboard channel)")
@app_commands.describe(
    channel="The channel to set the leaderboard in",
    filter_type="Choose how to filter the leaderboard",
    filter_value="Specify the number of months or starting game ID"
)
@app_commands.choices(filter_type=[
    app_commands.Choice(name="Months", value="months"),
    app_commands.Choice(name="Game ID", value="gameid")
])
@discord.app_commands.checks.has_any_role(settings.PERMS, 76209678462382090, 828304201586442250, 775177858237857802)
async def set_leaderbord(interaction, channel: discord.TextChannel, filter_type: app_commands.Choice[str], filter_value: int):
    """Set up the leaderboard channel with either a months filter or game ID filter."""
    await interaction.response.defer()
    leaderboard_channel = await interaction.guild.fetch_channel(channel.id)

    # Set the appropriate filter
    if filter_type.value == "gameid":
        filter_mode = "gameid"
        filter_data = filter_value
    else:
        filter_mode = "months"
        filter_data = filter_value

    view = LeaderboardView(interaction, filter_mode=filter_mode, filter_data=filter_data) 
    data = await view.get_leaderboard_data(interaction, limit=10)
    data_for_mentions = await view.get_leaderboard_data(interaction, limit=1000)

    if not data:
        await interaction.followup.send(f"No matches found for `{filter_type.value}` ({filter_value}).")
        return ""

    await interaction.followup.send(f"Leaderboard channel set to {channel.mention} with filter `{filter_mode}` ({filter_data})")

    # Extract player IDs from data (assuming format: `rank) <@ID> (ELO) ...`)
    player_mentions = []
    for entry in data_for_mentions:
        if "<@" in entry:  # Check if the entry contains a mention
            player_id = entry.split("<@")[1].split(">")[0]  # Extract ID
            player_mentions.append(f"<@{player_id}>")

#    if player_mentions:
#        message = await leaderboard_channel.send("Caching leaderboard names...")
#        await message.edit(content=f"||This message is so all names are visible/cached in lb: {', '.join(player_mentions)}||")  # No pings after edit

    if player_mentions:
        await send_leaderboard_mentions(leaderboard_channel, player_mentions)

    global leaderboard_message
    leaderboard_message = await leaderboard_channel.send(embed=view.create_embed(data), view=view)


async def setup(bot):
    bot.tree.add_command(set_leaderbord)
    return

