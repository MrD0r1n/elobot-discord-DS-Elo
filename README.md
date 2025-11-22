## INFO ##
An Elo Leaderboard Discord Bot for 1v1 games.

## PROJECT STATUS ##
This bot is written for the Doom Sumo Workshop discord server (https://discord.gg/3VmmEmxy6W).
You have to tweak some things yourself to make the bot work, find instructions below.

## INSTRUCTIONS ##
When you invite your discord bot with the OAuth2 URL Generator; Enable Scopes 'bot' and 'applications.commands'.
Bot permissions: View Channels, Manage Roles, Send messages, Add Reactions, Read Message History, Use Application Commands (probably doesn't need some of the last ones).

-Start with changing the 'perm' variable to a role ID from you server (for correct permissions). Change in top of file for elo_system.py and tournament_signup.py
-Optional: activate venv. 'python -m venv venv'
-Download the required pip packages (pip install -r requirements.txt) and make sure to create '.env' file in the main project folder. The env file should specify;
DISCORD_API_TOKEN = "your-token"
GUILD = "your-guild-id"
-To start: 'python main.py'
