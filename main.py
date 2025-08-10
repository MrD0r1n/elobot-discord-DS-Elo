import settings
import discord
from discord.ext import commands

logger = settings.logging.getLogger("bot")


class NotOwner(commands.CheckFailure):
    ...

def is_owner():
    async def predicate(ctx):
        if ctx.author.id != ctx.guild.owner_id:
            raise NotOwner("Hey you are not the owner")
        return True
    return commands.check(predicate)

def run():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = commands.Bot(command_prefix=">", intents=intents)

    @bot.event
    async def on_ready():
        logger.info(f"User: {bot.user} (ID: {bot.user.id})")
        logger.info(f"Guild ID: {bot.guilds[0].id}")

        #await bot.load_extension("cogs.elo_system")

        for cog_file in settings.COGS_DIR.glob("*.py"):
            if cog_file.name != "__init__.py":
                print(f"Loading cog: {cog_file}")
                await bot.load_extension(f"cogs.{cog_file.name[:-3]}")

        for cmd_file in settings.CMDS_DIR.glob("*.py"):
            if cmd_file.name != "__init__.py":
                await bot.load_extension(f"cmds.{cmd_file.name[:-3]}")

        bot.tree.copy_global_to(guild=settings.GUILDS_ID)
        await bot.tree.sync(guild=settings.GUILDS_ID)

    @bot.command()
    @is_owner()
    async def load(ctx, cog: str):
        await bot.load_extension(f"cogs.{cog.lower()}")
        await ctx.message.add_reaction("✅")

    @bot.command()
    @is_owner()
    async def unload(ctx, cog: str):
        await bot.unload_extension(f"cogs.{cog.lower()}")
        await ctx.message.add_reaction("✅")

    @bot.command()
    @is_owner()
    async def reload(ctx, cog: str):
        await bot.reload_extension(f"cogs.{cog.lower()}")
        await ctx.message.add_reaction("✅")


    # when you configure slash command in discord.py, they need to be synchronised with discord
    # we simply create a command
    #@bot.tree.command()
    #async def ciao(interaction: discord.Interaction):
        #await interaction.response.send_message(f"Ciao! {interaction.user.mention}", ephemeral=True)

    bot.run(settings.DISCORD_API_SECRET, root_logger=True)

if __name__ == '__main__':
    run()
