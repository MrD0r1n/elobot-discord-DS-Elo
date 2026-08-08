import datetime
import os
import re
import sqlite3
from io import BytesIO
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
    grant_winner_rank_roles,
    grant_loser_rank_roles,
)

# Load environment variables
load_dotenv()

CHALLONGE_API_KEY = os.getenv('CHALLONGE_API_TOKEN')
# Permalink/subdomain of the community tournaments should be created under
# (e.g. "doomsumo" for challonge.com/communities/doomsumo). Leave unset to
# create tournaments under the personal account instead.
CHALLONGE_COMMUNITY = os.getenv('CHALLONGE_COMMUNITY') or None
DB_NAME = 'elo_data.db'
CHALLONGE_BASE_URL = "https://api.challonge.com/v2.1"
CHALLONGE_HEADERS = {
    "Content-Type": "application/vnd.api+json",
    "Accept": "application/json",
    "Authorization-Type": "v1",
    "Authorization": CHALLONGE_API_KEY or "",
}


def _unwrap(resource: Dict[str, Any]) -> Dict[str, Any]:
    """Flattens a JSON:API resource object ({id, type, attributes}) into a plain dict."""
    if not isinstance(resource, dict):
        return {}
    flat = dict(resource.get("attributes", {}))
    flat["id"] = resource.get("id")
    return flat


def _unwrap_list(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flattens a JSON:API list response ({data: [...]}) into a list of plain dicts."""
    return [_unwrap(item) for item in payload.get("data", [])]


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

    async def challonge_request(self, method, endpoint, json_body=None, params=None):
        """Helper function for sending Challonge API (v2.1, JSON:API) requests"""
        url = f"{CHALLONGE_BASE_URL}/{endpoint}"

        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=CHALLONGE_HEADERS, params=params, json=json_body) as response:
                if response.status // 100 != 2:
                    error_text = await response.text()
                    raise Exception(f"Challonge API Error ({response.status}): {error_text}")
                if response.status == 204 or not await response.text():
                    return {}
                return await response.json()

    def _community_params(self) -> Optional[Dict[str, str]]:
        """Query params to scope a tournament-level request to CHALLONGE_COMMUNITY, if configured."""
        return {"community_id": CHALLONGE_COMMUNITY} if CHALLONGE_COMMUNITY else None

    # --- Challonge helpers
    async def get_participants(self, tournament_id: str) -> List[Dict[str, Any]]:
        """Returns flattened participant dicts (attributes + id) for a tournament."""
        resp = await self.challonge_request(
            "GET", f"tournaments/{tournament_id}/participants.json", params=self._community_params()
        )
        return _unwrap_list(resp)

    async def get_matches(self, tournament_id: str) -> List[Dict[str, Any]]:
        """Returns flattened match dicts (attributes + id) for a tournament."""
        resp = await self.challonge_request(
            "GET", f"tournaments/{tournament_id}/matches.json", params=self._community_params()
        )
        return _unwrap_list(resp)

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
            # 2. Create the tournament on Challonge (under CHALLONGE_COMMUNITY if configured)
            create_body = {
                "data": {
                    "type": "tournament",
                    "attributes": {
                        "name": tournament_name,
                        "url": url_slug,
                        "tournament_type": "double elimination",
                    },
                }
            }

            tournament_resp = await self.challonge_request(
                "POST", "tournaments.json", json_body=create_body, params=self._community_params()
            )
            tournament_obj = _unwrap(tournament_resp.get('data', {}))
            challonge_id = tournament_obj.get('id')
            # full_challonge_url isn't confirmed in the v2.1 response - fall back to building it.
            full_challonge_url = tournament_obj.get('full_challonge_url') or (
                f"https://challonge.com/{CHALLONGE_COMMUNITY}-{url_slug}" if CHALLONGE_COMMUNITY
                else f"https://challonge.com/{url_slug}"
            )

            # 3. Add participants (one request per participant)
            for name, user_id in participants:
                participant_body = {
                    "data": {
                        "type": "participant",
                        "attributes": {
                            "name": name,
                            "misc": str(user_id),
                        },
                    }
                }

                await self.challonge_request(
                    "POST",
                    f"tournaments/{challonge_id}/participants.json",
                    json_body=participant_body,
                    params=self._community_params(),
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
            index_resp = await self.challonge_request("GET", "tournaments.json", params=self._community_params())

            target_id = None
            found_url = None

            for t in _unwrap_list(index_resp):
                if t.get('name') == tournament_name:
                    target_id = t.get('id')
                    found_url = t.get('full_challonge_url') or f"https://challonge.com/{t.get('url')}"
                    break

            if not target_id:
                await interaction.followup.send(
                    f"⚠️ Could not find a tournament with the name **{tournament_name}** on Challonge.")
                return

            # 3. Delete the tournament (community_id required again if it belongs to one)
            await self.challonge_request(
                "DELETE", f"tournaments/{target_id}.json", params=self._community_params()
            )

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
            participants = await self.get_participants(tournament_id)
            matches = await self.get_matches(tournament_id)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error loading Challonge data: {e}")
            return

        # Mapping: Challonge participant ID -> Discord user ID (from participant.misc)
        p_to_discord: Dict[int, int] = {}
        p_to_name: Dict[int, str] = {}
        for p in participants:
            pid = p.get('id')
            if pid is None:
                continue
            pid = int(pid)  # JSON:API ids come back as strings; keep this in sync with match participant_ids (ints)
            p_to_name[pid] = p.get('name') or str(pid)
            misc = p.get('misc')
            # misc should contain our Discord user ID (int)
            try:
                if misc is not None and str(misc).strip() != "":
                    p_to_discord[pid] = int(str(misc))
            except Exception:
                # Ignore malformed misc values
                pass

        processed = 0
        skipped_no_discord = 0
        skipped_unfinished = 0
        skipped_already = 0
        newly_registered = 0
        processed_lines: List[str] = []  # Collect pretty lines to show in an embed at the end
        role_grant_lines: List[str] = []  # Collect role-earned/warning lines, shown after the match list
        # Roles granted to a given Discord user earlier in this same import run - add_roles() doesn't
        # update discord.py's local member cache, so without this a player winning/losing several
        # matches in one import would get the same "earned X role" message repeated per match.
        granted_role_ids_by_user: Dict[int, set] = {}

        # Confirmed against a real v2.1 tournament: matches have no player1_id/player2_id
        # or scores_csv (those are v1 fields). Instead there's `points_by_participant`
        # (an array of {participant_id, scores}) and an explicit `tie` flag for draws.
        for m in matches:
            match_id = m.get('id')
            state = m.get('state')
            winner_id = m.get('winner_id')
            tie = bool(m.get('tie'))
            completed_at = (m.get('timestamps') or {}).get('updated_at')  # no dedicated completed_at field in v2.1
            points = m.get('points_by_participant') or []

            # Only 1v1 matches with both participants recorded
            if not match_id or len(points) != 2:
                continue
            player1_id = points[0].get('participant_id')
            player2_id = points[1].get('participant_id')
            if player1_id is None or player2_id is None:
                continue

            if state != 'complete' or tie or not winner_id:
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
            if int(winner_id) == int(player1_id):
                w_disc, l_disc = d1, d2
                w_pid, l_pid = int(player1_id), int(player2_id)
            else:
                w_disc, l_disc = d2, d1
                w_pid, l_pid = int(player2_id), int(player1_id)

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

            # Grant rank roles based on standing, same logic as /report (Challenger/Baller for the
            # winner, Challenger for the loser) - only possible for players still in the server.
            w_member = interaction.guild.get_member(w_disc) if interaction.guild else None
            l_member = interaction.guild.get_member(l_disc) if interaction.guild else None
            if w_member is not None:
                msgs, granted = await grant_winner_rank_roles(
                    w_member, extra_role_ids=frozenset(granted_role_ids_by_user.get(w_disc, ()))
                )
                if granted:
                    granted_role_ids_by_user.setdefault(w_disc, set()).update(granted)
                role_grant_lines.extend(msgs)
            if l_member is not None:
                msgs, granted = await grant_loser_rank_roles(
                    l_member, extra_role_ids=frozenset(granted_role_ids_by_user.get(l_disc, ()))
                )
                if granted:
                    granted_role_ids_by_user.setdefault(l_disc, set()).update(granted)
                role_grant_lines.extend(msgs)

            # Build a display line: "Winner - Loser: +X / -Y (old_w->new_w | old_l->new_l)"
            try:
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

        # Build an embed summary
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

        # Discord embeds cap out at 6000 total characters (title+description+fields) and
        # 25 fields, both easy to exceed on a big tournament. Show short lists inline
        # (matches first, then roles); anything larger goes into one attached text file.
        match_text = "\n".join(processed_lines)
        role_text = "\n".join(role_grant_lines)
        file_sections: List[str] = []

        if processed_lines:
            if len(match_text) <= 1000 and len(processed_lines) <= 20:
                embed.add_field(name="Processed Matches", value=match_text, inline=False)
            else:
                embed.add_field(
                    name="Processed Matches",
                    value=f"{len(processed_lines)} matches processed - see attached file.",
                    inline=False,
                )
                file_sections.append(f"=== Processed Matches ===\n{match_text}")

        if role_grant_lines:
            if len(role_text) <= 1000 and len(role_grant_lines) <= 20:
                embed.add_field(name="Roles Granted", value=role_text, inline=False)
            else:
                embed.add_field(
                    name="Roles Granted",
                    value=f"{len(role_grant_lines)} role update(s) - see attached file.",
                    inline=False,
                )
                file_sections.append(f"=== Roles Granted ===\n{role_text}")

        file_to_send: Optional[discord.File] = None
        if file_sections:
            file_to_send = discord.File(
                BytesIO("\n\n".join(file_sections).encode("utf-8")), filename="import_results.txt"
            )

        if file_to_send:
            await interaction.followup.send(embed=embed, file=file_to_send)
        else:
            await interaction.followup.send(embed=embed)


    @app_commands.command(
        name="challonge_substitute",
        description="Substitutes a participant in a Challonge tournament"
    )
    @app_commands.describe(
        tournament_id="Challonge tournament ID or URL slug",
        existing_player="The existing participant to replace (pick a Discord user)",
        new_player="The new participant (pick a Discord user)"
    )
    async def challonge_substitute(
        self,
        interaction: discord.Interaction,
        tournament_id: str,
        existing_player: discord.User,
        new_player: discord.User,
    ):
        """Updates an existing Challonge participant by selecting Discord users.
        Notes:
        - Finds the participant via misc (Discord ID) of the existing Discord user.
        - Updates the participant's display name to the new Discord user's display name.
        - Updates misc to the new Discord user's ID.
        """
        await interaction.response.defer(thinking=True)

        # Basic requirements
        if not CHALLONGE_API_KEY:
            await interaction.followup.send("❌ CHALLONGE_API_TOKEN is not set.")
            return

        try:
            # Load all participants for the tournament
            plist = await self.get_participants(tournament_id)

            # Primary lookup: misc stores Discord user ID
            target = None
            for p in plist:
                misc_val = p.get("misc")
                if misc_val is not None and str(misc_val) == str(existing_player.id):
                    target = p
                    break

            # Fallback: exact case-insensitive name match against the existing Discord display name
            if target is None:
                # Safely resolve a display name for the existing user
                disp = getattr(existing_player, 'display_name', None) or getattr(existing_player, 'global_name', None) or existing_player.name
                for p in plist:
                    name = str(p.get("name", ""))
                    if name.lower() == disp.lower():
                        target = p
                        break

            if target is None:
                await interaction.followup.send(
                    "❌ Could not find a participant linked to the selected existing Discord user. Make sure their Discord ID is stored in Challonge 'misc'."
                )
                return

            target_id = target.get("id")
            old_name = target.get("name")
            old_misc = target.get("misc")
            if not target_id:
                await interaction.followup.send("❌ Found a participant without a valid ID; cannot update.")
                return

            # Perform the update using Challonge v2.1 API
            # Safely resolve a display name for the new user
            new_disp = getattr(new_player, 'display_name', None) or getattr(new_player, 'global_name', None) or new_player.name
            update_body = {
                "data": {
                    "type": "participant",
                    "attributes": {
                        "name": new_disp,
                        "misc": str(new_player.id),
                    },
                }
            }

            updated = await self.challonge_request(
                "PUT",
                f"tournaments/{tournament_id}/participants/{int(target_id)}.json",
                json_body=update_body,
                params=self._community_params(),
            )

            updated_p = _unwrap(updated.get("data", {})) if isinstance(updated, dict) else {}
            new_name = updated_p.get("name", new_disp)
            new_misc = updated_p.get("misc", str(new_player.id))

            embed = discord.Embed(
                title="🔁 Participant Substitution Completed",
                description="The participant has been updated on Challonge (name and misc).",
                color=discord.Color.green(),
            )
            embed.add_field(name="Tournament", value=str(tournament_id), inline=False)
            embed.add_field(name="Old Name", value=str(old_name), inline=True)
            embed.add_field(name="New Name", value=str(new_name), inline=True)
            embed.add_field(name="Old misc (Discord ID)", value=str(old_misc) if old_misc is not None else "None", inline=False)
            embed.add_field(name="New misc (Discord ID)", value=str(new_misc), inline=False)
            embed.set_footer(text="Both the display name and misc (Discord ID) were updated using the selected Discord users.")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            await interaction.followup.send(f"⚠️ Error performing substitution: {e}")


async def setup(bot):
    await bot.add_cog(ChallongeCommands(bot))
