import datetime
import os
import re
import sqlite3
from typing import Dict, Any, List, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# reuse elo-system functions
from cogs.elo_system import (
    get_elo,
    set_elo,
    calculate_score_change,
    update_elo,
    get_highest_elo,
    set_highest_elo,
    update_historical_rankings,
    get_multiplier,
)

# Load environment variables
load_dotenv()

CHALLONGE_API_KEY = os.getenv('CHALLONGE_API_TOKEN')
DB_NAME = 'elo_data.db'
CHALLONGE_BASE_URL = "https://api.challonge.com/v1"


class ChallongeCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def get_db_connection(self):
        return sqlite3.connect(DB_NAME)

    def clean_url_string(self, text: str):
        """Creates a valid URL string from a tournament name"""
        # Only keep alphanumeric characters and underscores, replace everything else
        clean = re.sub(r'[^a-zA-Z0-9_]', '_', text).lower()
        # Append a timestamp to avoid duplicate URLs when using similar names
        timestamp = int(datetime.datetime.now().timestamp())
        return f"{clean}_{timestamp}"

    async def challonge_request(self, method, endpoint, data=None):
        """Helper function for sending Challonge API requests"""
        url = f"{CHALLONGE_BASE_URL}/{endpoint}"
        params = {'api_key': CHALLONGE_API_KEY}

        if data:
            params.update(data)

        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, params=params, data=data) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Challonge API Error ({response.status}): {error_text}")
                return await response.json()

    # --- Challonge helpers
    async def get_participants(self, tournament_id: str) -> List[Dict[str, Any]]:
        """Returns the participant wrappers as provided by the Challonge v1 API (array of {participant:{...}})."""
        return await self.challonge_request("GET", f"tournaments/{tournament_id}/participants.json")

    async def get_matches(self, tournament_id: str) -> List[Dict[str, Any]]:
        """Returns the match wrappers as provided by the Challonge v1 API (array of {match:{...}})."""
        return await self.challonge_request("GET", f"tournaments/{tournament_id}/matches.json")

    def _ensure_processed_table(self):
        """Ensures the table for preventing duplicate match processing exists."""
        with self.get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS challonge_processed_matches (
                    match_id INTEGER PRIMARY KEY,
                    tournament_id TEXT,
                    processed_at TEXT
                )
                """
            )
            conn.commit()

    def _is_match_processed(self, match_id: int) -> bool:
        with self.get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM challonge_processed_matches WHERE match_id = ?", (match_id,))
            return c.fetchone() is not None

    def _mark_match_processed(self, match_id: int, tournament_id: str):
        with self.get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR IGNORE INTO challonge_processed_matches (match_id, tournament_id, processed_at) VALUES (?, ?, ?)",
                (match_id, tournament_id, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            )
            conn.commit()

    @app_commands.command(
        name="create_tournament",
        description="Creates a Challonge tournament based on a signup message"
    )
    @app_commands.describe(message_id="The ID of the Discord message where people signed up")
    async def create_tournament(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.defer(thinking=True)

        try:
            msg_id_int = int(message_id)
        except ValueError:
            await interaction.followup.send("❌ The message ID must be a number.")
            return

        # 1. Load participant data from the database
        participants = []
        tournament_name = None

        with self.get_db_connection() as conn:
            c = conn.cursor()
            # Fetch tournament name, usernames and user IDs associated with that message ID
            c.execute(
                "SELECT tournament_name, username, user_id FROM tournament_signups WHERE message_id = ?",
                (msg_id_int,)
            )
            rows = c.fetchall()

        if not rows:
            await interaction.followup.send(f"❌ No signup entries found in the database for message ID `{message_id}`.")
            return

        # Tournament name is the same for all rows, so take the first
        tournament_name = rows[0][0]

        # Collect all participants as (username, discord_user_id)
        participants = []
        for _t_name, username, user_id in rows:
            # Basic safety: skip entries without username or user_id
            if not username or user_id is None:
                continue
            participants.append((username, user_id))

        if not participants:
            await interaction.followup.send("❌ Tournament found, but no participants with valid Discord IDs.")
            return

        url_slug = self.clean_url_string(tournament_name)

        try:
            # 2. Create the tournament on Challonge
            create_payload = {
                "tournament[name]": tournament_name,
                "tournament[url]": url_slug,
                "tournament[tournament_type]": "double elimination",
            }

            tournament_resp = await self.challonge_request("POST", "tournaments.json", create_payload)
            tournament_obj = tournament_resp.get('tournament', {})
            challonge_id = tournament_obj.get('id')
            full_challonge_url = tournament_obj.get('full_challonge_url')

            # 3. Add participants (Bulk Add)
            # Challonge v1 expects keys like participants[][name]
            for name, user_id in participants:
                form = {
                    "participant[name]": name,
                    "participant[misc]": str(user_id)
                }

                await self.challonge_request(
                    "POST",
                    f"tournaments/{challonge_id}/participants.json",
                    data=form,
                )

            # 4. Send success embed
            embed = discord.Embed(
                title="🏆 Tournament Created!",
                description=f"The tournament **{tournament_name}** was successfully created on Challonge.",
                color=discord.Color.gold()
            )
            embed.add_field(name="Link", value=f"[Open Tournament]({full_challonge_url})", inline=False)
            embed.add_field(name="Participants", value=f"{len(participants)} players added.", inline=False)

            await interaction.followup.send(embed=embed)

        except Exception as e:
            await interaction.followup.send(f"⚠️ Error communicating with Challonge: {str(e)}")

    @app_commands.command(
        name="delete_tournament",
        description="Deletes a Challonge tournament based on the signup message"
    )
    @app_commands.describe(message_id="The ID of the Discord message")
    async def delete_tournament(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.defer(thinking=True)

        try:
            msg_id_int = int(message_id)
        except ValueError:
            await interaction.followup.send("❌ The message ID must be a number.")
            return

        # 1. Load the tournament name from local DB
        tournament_name = None
        with self.get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT tournament_name FROM tournament_signups WHERE message_id = ? LIMIT 1", (msg_id_int,))
            row = c.fetchone()

        if not row:
            await interaction.followup.send(f"❌ No local database entry found for message ID `{message_id}`.")
            return

        tournament_name = row[0]

        try:
            # 2. Find the tournament on Challonge by its name
            index_resp = await self.challonge_request("GET", "tournaments.json", {"state": "all"})

            target_id = None
            found_url = None

            for item in index_resp:
                t = item['tournament']
                if t['name'] == tournament_name:
                    target_id = t['id']
                    found_url = t['full_challonge_url']
                    break

            if not target_id:
                await interaction.followup.send(
                    f"⚠️ Could not find a tournament with the name **{tournament_name}** on Challonge.")
                return

            # 3. Delete the tournament
            await self.challonge_request("DELETE", f"tournaments/{target_id}.json")

            await interaction.followup.send(
                f"✅ Tournament **{tournament_name}** ({found_url}) has been deleted from Challonge.")

        except Exception as e:
            await interaction.followup.send(f"⚠️ Error while deleting tournament: {str(e)}")

    @app_commands.command(
        name="import_challonge_results",
        description="Imports completed Challonge matches into the ELO system"
    )
    @app_commands.describe(tournament_id="Challonge tournament ID or URL slug")
    async def import_challonge_results(self, interaction: discord.Interaction, tournament_id: str):
        """
        Fetches participants and matches from Challonge and records completed games in the local ELO database.
        - Registers missing players (equivalent to /register)
        - Records matches and updates ELO (equivalent to /report)
        - Prevents double-processing via match_id
        """
        await interaction.response.defer(thinking=True)

        # Basic requirements
        if not CHALLONGE_API_KEY:
            await interaction.followup.send("❌ CHALLONGE_API_TOKEN is not set.")
            return

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("❌ This command must be used in a server.")
            return

        # Create table if not present
        self._ensure_processed_table()

        try:
            participants_wrapped = await self.get_participants(tournament_id)
            matches_wrapped = await self.get_matches(tournament_id)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error loading Challonge data: {e}")
            return

        # Mapping: Challonge participant ID -> Discord user ID (from participant.misc)
        p_to_discord: Dict[int, int] = {}
        p_to_name: Dict[int, str] = {}
        for pw in participants_wrapped:
            p = pw.get('participant', {})
            pid = p.get('id')
            if pid is None:
                continue
            p_to_name[pid] = p.get('name') or str(pid)
            misc = p.get('misc')
            # misc should contain our Discord user ID (int)
            try:
                if misc is not None and str(misc).strip() != "":
                    p_to_discord[int(pid)] = int(str(misc))
            except Exception:
                # Ignore malformed misc values
                pass

        processed = 0
        skipped_no_discord = 0
        skipped_unfinished = 0
        skipped_already = 0
        newly_registered = 0
        processed_lines: List[str] = []  # Collect pretty lines to show in an embed at the end

        for mw in matches_wrapped:
            m = mw.get('match', {})
            match_id = m.get('id')
            player1_id = m.get('player1_id')
            player2_id = m.get('player2_id')
            winner_id = m.get('winner_id')
            scores_csv = m.get('scores_csv')
            completed_at = m.get('completed_at')  # ISO string or None
            state = m.get('state')

            # Only completed matches with both players
            if not match_id or not player1_id or not player2_id:
                continue
            if not (state == 'complete' or (scores_csv and str(scores_csv).strip() != "") or winner_id):
                skipped_unfinished += 1
                continue

            if self._is_match_processed(int(match_id)):
                skipped_already += 1
                continue

            # Determine Discord IDs
            d1 = p_to_discord.get(int(player1_id))
            d2 = p_to_discord.get(int(player2_id))
            if d1 is None or d2 is None:
                skipped_no_discord += 1
                continue

            # Determine winner/loser
            w_disc: Optional[int] = None
            l_disc: Optional[int] = None
            w_pid: Optional[int] = None
            l_pid: Optional[int] = None
            if winner_id:
                if int(winner_id) == int(player1_id):
                    w_disc, l_disc = d1, d2
                    w_pid, l_pid = int(player1_id), int(player2_id)
                else:
                    w_disc, l_disc = d2, d1
                    w_pid, l_pid = int(player2_id), int(player1_id)
            else:
                # Fallback via total scores_csv
                try:
                    total1 = 0
                    total2 = 0
                    if scores_csv:
                        for set_str in str(scores_csv).split(","):
                            set_str = set_str.strip()
                            if not set_str:
                                continue
                            a, b = set_str.split("-")
                            total1 += int(a)
                            total2 += int(b)
                    if total1 == total2:
                        # Draw? Skip
                        skipped_unfinished += 1
                        continue
                    if total1 > total2:
                        w_disc, l_disc = d1, d2
                        w_pid, l_pid = int(player1_id), int(player2_id)
                    else:
                        w_disc, l_disc = d2, d1
                        w_pid, l_pid = int(player2_id), int(player1_id)
                except Exception:
                    skipped_unfinished += 1
                    continue

            # Register if needed (equivalent to /register)
            g1 = get_elo(w_disc)
            if g1 is None:
                set_elo(w_disc, 1200)
                newly_registered += 1
            g2 = get_elo(l_disc)
            if g2 is None:
                set_elo(l_disc, 1200)
                newly_registered += 1

            # ELO update (equivalent to /report but without role/message logic)
            date_str = (
                datetime.datetime.fromisoformat(completed_at.replace("Z", "+00:00")).strftime('%Y-%m-%d %H:%M:%S')
                if isinstance(completed_at, str) and completed_at
                else datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )

            old_elo_winner = get_elo(w_disc)
            old_elo_loser = get_elo(l_disc)
            score_change = calculate_score_change(w_disc, l_disc)
            update_elo(w_disc, l_disc)
            elo_winner = get_elo(w_disc)
            elo_loser = get_elo(l_disc)

            # Maintain highest ELO as done in /report
            if get_highest_elo(w_disc) is None:
                set_highest_elo(w_disc, elo_winner)
            if get_highest_elo(l_disc) is None:
                set_highest_elo(l_disc, elo_loser)
            if elo_winner > (get_highest_elo(w_disc) or 0):
                set_highest_elo(w_disc, elo_winner)
            if elo_loser > (get_highest_elo(l_disc) or 0):
                set_highest_elo(l_disc, elo_loser)

            # Insert match into match_data (same as /report)
            multiplier = get_multiplier()
            with self.get_db_connection() as conn:
                c = conn.cursor()
                c.execute(
                    'INSERT INTO match_data (date, winner_id, loser_id, elo_change, elo_winner, elo_loser, multiplier) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (date_str, w_disc, l_disc, score_change, elo_winner, elo_loser, multiplier),
                )
                conn.commit()

            # Mark match as processed
            self._mark_match_processed(int(match_id), str(tournament_id))
            processed += 1

            # Build a display line: "Winner - Loser: +X / -Y (old_w->new_w | old_l->new_l)"
            try:
                w_member = interaction.guild.get_member(w_disc) if interaction.guild else None
                l_member = interaction.guild.get_member(l_disc) if interaction.guild else None
                w_name = (
                    w_member.display_name if w_member else (
                        p_to_name.get(w_pid, str(w_disc)) if w_pid is not None else str(w_disc)
                    )
                )
                l_name = (
                    l_member.display_name if l_member else (
                        p_to_name.get(l_pid, str(l_disc)) if l_pid is not None else str(l_disc)
                    )
                )
                w_delta = score_change * multiplier
                l_delta = -score_change
                processed_lines.append(
                    f"{w_name} - {l_name}: +{w_delta} / {l_delta}  ({old_elo_winner}->{elo_winner} | {old_elo_loser}->{elo_loser})"
                )
            except Exception:
                # If anything goes wrong while building the pretty line, just skip it
                pass

        # Update historical rankings after import
        try:
            update_historical_rankings()
        except Exception:
            pass

        # Build a nice embed summary in English, including per‑match lines if available
        summary_lines = [
            f"Processed: {processed}",
            f"Registered new players: {newly_registered}",
            f"Skipped (unfinished/no result): {skipped_unfinished}",
            f"Skipped (missing Discord ID): {skipped_no_discord}",
            f"Already processed: {skipped_already}",
        ]

        embed = discord.Embed(
            title="✅ Challonge Import Completed",
            description=(
                f"Tournament: `{tournament_id}`\n" + "\n".join(summary_lines)
            ),
            color=discord.Color.blue(),
        )

        # If we have match details, add them as fields, chunked to respect Discord limits
        if processed_lines:
            # Helper to chunk lines into field-sized strings (~1000 chars safety margin)
            chunks: List[str] = []
            current = ""
            for line in processed_lines:
                # +1 for newline if current not empty
                extra = ("\n" if current else "") + line
                if len(current) + len(extra) > 1000:
                    if current:
                        chunks.append(current)
                    current = line
                else:
                    current += extra if current else line
            if current:
                chunks.append(current)

            for idx, chunk in enumerate(chunks, start=1):
                name = "Processed Matches" if len(chunks) == 1 else f"Processed Matches (part {idx}/{len(chunks)})"
                embed.add_field(name=name, value=chunk, inline=False)

            if len(chunks) > 4:
                # Very long outputs are possible on big events; hint at truncation even though we chunk conservatively
                embed.set_footer(text="List truncated for length. Consider importing in smaller batches if needed.")

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ChallongeCommands(bot))
