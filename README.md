# Elo Bot

An Elo leaderboard Discord bot for 1v1 games.

## Project Status

This bot is written for the [Doom Sumo Workshop Discord server](https://discord.gg/3VmmEmxy6W).
You'll need to tweak a few things to make it work on your own server - instructions below.

## Setup

1. **Invite the bot** using the OAuth2 URL Generator, with:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `View Channels`, `Manage Roles`, `Send Messages`, `Add Reactions`, `Read Message History`, `Use Application Commands` (some of these may not be strictly required)

2. **(Optional) Create a virtual environment:**

   ```bash
   python -m venv venv
   ```

3. **Install the required packages:**

   ```bash
   pip install -r requirements.txt
   ```

4. **Create a `.env` file** in the project root with:

   ```env
   DISCORD_API_TOKEN="your-token"
   GUILD="your-guild-id"
   CHALLONGE_API_TOKEN="your-challonge-v1-token"   # only needed for the Challonge integration
   CHALLONGE_COMMUNITY="your-community-permalink"  # optional, e.g. "doomsumo" - creates tournaments under a Challonge community instead of your personal account
   ```

5. **Configure role IDs.** All Discord role IDs the bot checks against (staff permissions, ELO rank roles) live in `settings.py`, not hardcoded in the cogs. Each one can be overridden per-environment via `.env` instead of editing code - handy when testing against a separate server, since you only need to override the roles that differ there. See the `ROLE_*` variables in `settings.py` for the full list and their `.env` override names (e.g. `ROLE_ADMIN`, `ROLE_TEST_PERM`).

6. **Start the bot:**

   ```bash
   python main.py
   ```
