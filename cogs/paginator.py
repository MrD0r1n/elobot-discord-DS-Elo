import settings
import discord
from discord.ext import commands
from discord import app_commands
import datetime
import sqlite3

logger = settings.logging.getLogger("bot")

PAGE_SIZE = 11
INITIAL_PAGE = 1
EMBED_COLOR = discord.Color.blue()  # or any color you prefer


class PaginationView(discord.ui.View):
    """View for paginated embeds."""

    def __init__(self, interaction:discord.Interaction, title:str, description:str, embed_color:discord.Color, ephemeral:bool):
        super().__init__(timeout=300)
        self.bot = interaction.client
        self.interaction = interaction
        self.current_page : int = INITIAL_PAGE
        self.sep : int = PAGE_SIZE
        self.title = title
        self.description = description
        self.embed_color = embed_color
        self.ephemeral = ephemeral
        self.message = None


    #async def send(self):
        """Send the initial message."""
        #self.message = await self.interaction.response.defer(ephemeral=self.ephemeral)
        #self.message = message #deze uit houden
        #await self.update_message(self.data[:self.sep])

    async def send(self):
        """Send the initial message."""
        if not self.interaction.response.is_done():
            await self.interaction.response.defer(ephemeral=self.ephemeral)  # Acknowledge the interaction
        # Send a follow-up message and store it for editing later
        self.message = await self.interaction.followup.send(embed=self.create_embed(self.data[:self.sep]), view=self, ephemeral=self.ephemeral)



    def create_embed(self, data):
        """Create the embed for the current page."""
        embed = discord.Embed(
            title=self.title,
            description=self.description,
            color=self.embed_color
        )
        data[-1] += "\n\u200b"  # Add a newline to the last item
        embed.add_field(name="\u200b", value="\n".join(data), inline=False)
        embed.set_footer(text=f"{self.bot.user.name} • Page {self.current_page} / {int(len(self.data) / self.sep) + 1}",
                        icon_url=self.bot.user.display_avatar.url)
        return embed 
   
    
    #async def update_message(self, data):
        """Updates the message with the new embed and updates the buttons."""
        #self.update_buttons()
        #if self.message:
            #await self.message.edit(embed=self.create_embed(data), view=self)
        #else:
            #followup = await self.interaction.followup.send(embed=self.create_embed(data), view=self, ephemeral=self.ephemeral)
            #self.message = followup

    async def update_message(self, data):
        """Updates the message with the new embed and buttons."""
        self.update_buttons()
    
        if isinstance(self.message, discord.Interaction):  # If it's an interaction, get the original message
            self.message = await self.interaction.original_response()
    
        await self.message.edit(embed=self.create_embed(data), view=self)



    def update_buttons(self):
        if self.current_page == 1:
            self.first_page_button.disabled = True
            self.prev_button.disabled = True
            self.first_page_button.style = discord.ButtonStyle.gray
            self.prev_button.style = discord.ButtonStyle.gray
        else:
            self.first_page_button.disabled = False
            self.prev_button.disabled = False
            self.first_page_button.style = discord.ButtonStyle.green
            self.prev_button.style = discord.ButtonStyle.primary

        if self.current_page == int(len(self.data) / self.sep) + 1:
            self.next_button.disabled = True
            self.last_page_button.disabled = True
            self.last_page_button.style = discord.ButtonStyle.gray
            self.next_button.style = discord.ButtonStyle.gray
        else:
            self.next_button.disabled = False
            self.last_page_button.disabled = False
            self.last_page_button.style = discord.ButtonStyle.green
            self.next_button.style = discord.ButtonStyle.primary

    def get_current_page_data(self):
        until_item = self.current_page * self.sep
        from_item = until_item - self.sep
        return self.data[from_item:until_item]

    @discord.ui.button(label="|<",
                       style=discord.ButtonStyle.green)
    async def first_page_button(self, interaction:discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_page = 1

        await self.update_message(self.get_current_page_data())

    @discord.ui.button(label="<",
                       style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction:discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_page -= 1
        await self.update_message(self.get_current_page_data())

    @discord.ui.button(label=">",
                       style=discord.ButtonStyle.primary)
    async def next_button(self, interaction:discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_page += 1
        await self.update_message(self.get_current_page_data())

    @discord.ui.button(label=">|",
                       style=discord.ButtonStyle.green)
    async def last_page_button(self, interaction:discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_page = int(len(self.data) / self.sep) + 1
        await self.update_message(self.get_current_page_data())


class PaginatorCog(commands.Cog):
    """Cog for the paginate command."""

    def __init__(self, bot):
        self.bot = bot

    async def get_wl_ratio_data(self):
        # Connect to the SQLite database
        with sqlite3.connect('elo_data.db') as conn:
            c = conn.cursor()
            data = []
            
            # The SQL query for fetching the W/L ratio leaderboard
            query = '''
            WITH Wins AS (
                SELECT winner_id as player_id, COUNT(*) as win_count
                FROM match_data
                GROUP BY winner_id
            ),
            Losses AS (
                SELECT loser_id as player_id, COUNT(*) as loss_count
                FROM match_data
                GROUP BY loser_id
            )
            SELECT 
                COALESCE(Wins.player_id, Losses.player_id) as player_id,
                COALESCE(win_count, 0) as wins,
                COALESCE(loss_count, 0) as losses,
                CASE 
                    WHEN COALESCE(loss_count, 0) = 0 THEN COALESCE(win_count, 0) 
                    ELSE CAST(COALESCE(win_count, 0) AS FLOAT) / COALESCE(loss_count, 0) 
                END AS wl_ratio 
            FROM Wins 
            LEFT JOIN Losses ON Wins.player_id = Losses.player_id
            UNION 
            SELECT 
                COALESCE(Wins.player_id, Losses.player_id) as player_id,
                COALESCE(win_count, 0) as wins,
                COALESCE(loss_count, 0) as losses,
                CASE 
                    WHEN COALESCE(loss_count, 0) = 0 THEN COALESCE(win_count, 0) 
                    ELSE CAST(COALESCE(win_count, 0) AS FLOAT) / COALESCE(loss_count, 0) 
                END AS wl_ratio 
            FROM Losses 
            LEFT JOIN Wins ON Losses.player_id = Wins.player_id
            ORDER BY wl_ratio DESC, wins DESC
            '''

            c.execute(query)
            wl_data = c.fetchall()
            
            for rank, (player_id, wins, losses, wl_ratio) in enumerate(wl_data, start=1):
                data.append(f"`{rank})` <@{player_id}> **{wl_ratio:.2f} ({wins}W/{losses}L)**")
            
            return data

    @app_commands.command(name = "paginate", description = "Shows the leaderboard or other data")
    @app_commands.choices(choices=[
        app_commands.Choice(name="Leaderboard", value="leaderboard"),
        app_commands.Choice(name="Highest ELO achieved", value="highest_elo_achieved"),
        app_commands.Choice(name="W/L ratio", value="wl_ratio"),
        app_commands.Choice(name="Recent matches", value="recent_matches"),
        app_commands.Choice(name="Other", value="other")
    ])
    @app_commands.describe(months="Filter by last X months (optional, only for Leaderboard)")
    async def paginate(self, interaction:discord.Interaction, choices: app_commands.Choice[str], private:bool=True, months:int=1):
        try:
            # Create a connection to the SQLite database
            with sqlite3.connect('elo_data.db') as conn:
                c = conn.cursor()
                data = []

                # The defer interaction is passed to the PaginationView, it is possible to do it here if the interaction takes too long
                # self.message = await interaction.response.defer(ephemeral=private)

                # Fetch the ELO data from the database and sort it by ELO score
                if (choices.value == "leaderboard"):
                    title = "Leaderboard     \t\t\t\t\t\t\t\t\t\t\u200b"
                    description = "Top ranking ELO players"
                    embed_color = discord.Color.blue()


                    query = '''
                        SELECT DISTINCT e.player_id, e.elo
                        FROM elo_data e
                        WHERE e.inactive = 0
                    '''
                    params = []

                    if months > 0:
                        cutoff_date = (datetime.datetime.utcnow() - datetime.timedelta(days=30 * months)).strftime('%Y-%m-%d %H:%M:%S')
                        query = '''
                            SELECT DISTINCT e.player_id, e.elo
                            FROM elo_data e
                            JOIN (
                                SELECT winner_id AS p FROM match_data WHERE date >= ?
                                UNION
                                SELECT loser_id AS p FROM match_data WHERE date >= ?
                            ) sub ON sub.p = e.player_id
                            WHERE e.inactive = 0
                        '''
                        params = [cutoff_date, cutoff_date]

                    query += ' ORDER BY e.elo DESC'

                    #c.execute('SELECT player_id, elo FROM elo_data WHERE inactive = 0 ORDER BY elo DESC')
                    c.execute(query, params)
                    elo_data = c.fetchall()     

                    for rank, (player_id, elo) in enumerate(elo_data, start=1):
                        #player = await self.bot.fetch_user(player_id)
                        if rank == 1:
                            rank_str = ":first_place: \u200b"
                        elif rank == 2:
                            rank_str = ":second_place: \u200b"
                        elif rank == 3:
                            rank_str = ":third_place: \u200b"
                        else:
                            rank_str = f"`{rank}) `"
                        data.append(f"{rank_str} <@{player_id}> **({elo})**")
                        if rank == 3:
                            data.append("\u200b")  # Add a newline after the 3rd rank

                if (choices.value == "highest_elo_achieved"):
                    title = "Highest ELO's achieved   \t\t\t\t\t\t\u200b"
                    description = "Personal best of all time"
                    embed_color = discord.Color.yellow()

                    c.execute('SELECT player_id, highest_elo FROM elo_data WHERE inactive = 0 ORDER BY highest_elo DESC')
                    elo_data = c.fetchall()

                    # Convert the ELO data to the format expected by the PaginationView
                    for rank, (player_id, elo_highest) in enumerate(elo_data, start=1):
                        #player = await self.bot.fetch_user(player_id)
                        data.append(f"`{rank}) `<@{player_id}> **[{elo_highest}]**")
                
                if (choices.value == "other"):
                    title = "Some other stuff \t\t\t\t\t\t\t\t\t\u200b"
                    description = "Top ranking highest Balls"
                    embed_color = discord.Color.purple()

                    for i in range(1,100):
                        data.append(f"Balls{i} has been added")
                
                if (choices.value == "wl_ratio"):
                    title = "W/L Ratio Leaderboard \t\t\t\t\t\t\t\t\t\u200b"
                    description = "Current W/L ratio ranking"
                    embed_color = discord.Color.orange()

                    data = await self.get_wl_ratio_data()
                
                if (choices.value == "recent_matches"):
                    title = "Recent matches \t\t\t\t\t\t\t\t\t\u200b"
                    description = "Last 99 matches played"
                    embed_color = discord.Color.green()

                    c.execute('SELECT game_id, date, winner_id, loser_id FROM match_data ORDER BY game_id DESC LIMIT 99')
                    matches = c.fetchall()

                    for game_id, date, winner_id, loser_id in matches:
                            data.append(f"**Match {game_id}** on {date}")
                            data.append(f"Winner: <@{winner_id}> | Loser: <@{loser_id}>")

                if not data:  # If no data is found
                    if (choices.value == "leaderboard"):
                        await interaction.response.send_message("No matches have been played in the selected period.", ephemeral=True)
                        return ""
                    else:
                        await interaction.response.send_message("No data found", ephemeral=True)
                        return ""

                pagination_view = PaginationView(interaction, title, description, embed_color, ephemeral=private)
                pagination_view.data = data
                await pagination_view.send()

        except sqlite3.Error as e:
            logger.error(f"Database error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")


async def setup(bot):
    await bot.add_cog(PaginatorCog(bot))
