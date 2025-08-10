import settings
from discord.ext import commands, tasks
import shutil
import os
import time

logger = settings.logging.getLogger("bot")


def backup_db(custom_name=None, folder='backups_auto'):
    backup_folder = os.path.join('backups', folder)
    if not os.path.exists(backup_folder):
        os.makedirs(backup_folder)
    if custom_name is None:
        timestamp = time.strftime('%Y%m%d-%H%M%S')
        backup_filename = f'elo_data_{timestamp}.db'
    else:
        backup_filename = f'{custom_name}.db'
    backup_path = os.path.join(backup_folder, backup_filename)
    shutil.copy2('elo_data.db', backup_path)

def remove_backup(custom_name, folder='backups_manual'):
    backup_folder = os.path.join('backups', folder)
    backup_filename = f'{custom_name}.db'
    backup_path = os.path.join(backup_folder, backup_filename)
    if os.path.exists(backup_path):
        os.remove(backup_path)
        return True
    else:
        return False

def delete_oldest_files(directory, file_limit=200):
    files = os.listdir(directory)
    if len(files) > file_limit:
        files.sort(key=os.path.getmtime)
        for file in files[:len(files)-file_limit]:
            os.remove(os.path.join(directory, file))


class BackupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.backup_task.start()

    def cog_unload(self):
        self.backup_task.cancel()

    @tasks.loop(hours=(4*7*24)) 
    async def backup_task(self):
        delete_oldest_files(os.path.join('backups', 'backups_auto'))
        backup_db()

    @commands.hybrid_command(name='backup', description='Make a backup of the database')
    @commands.has_any_role(828304201586442250, 775177858237857802)
    async def backup(self, ctx, custom_name: str):
        backup_db(custom_name, folder='backups_manual')
        await ctx.send(f"Backup made with the name `{custom_name}`.")

    @commands.hybrid_command(name='remove_backup', description='Remove a backup from the server')
    @commands.has_any_role(828304201586442250, 775177858237857802)
    async def remove_backup(self, ctx, custom_name: str):
        if remove_backup(custom_name):
            await ctx.send(f"Backup `{custom_name}` has been removed.")
        else:
            await ctx.send(f"No backup found with the name `{custom_name}`.")


async def setup(bot):
    await bot.add_cog(BackupCog(bot))
