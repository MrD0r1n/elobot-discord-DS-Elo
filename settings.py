import pathlib
import os
import logging
from logging.config import dictConfig
from dotenv import load_dotenv
import discord

load_dotenv()

DISCORD_API_SECRET = os.getenv("DISCORD_API_TOKEN")

BASE_DIR = pathlib.Path(__file__).parent

CMDS_DIR = BASE_DIR / 'cmds'
COGS_DIR = BASE_DIR / 'cogs'

VIDEOCMDS_DIR = BASE_DIR / "videocmds"

GUILDS_ID = discord.Object(id=int(os.getenv("GUILD")))


def _role_id(env_name, default):
    """Read a role ID from the environment, falling back to `default`.

    Lets a .env in a test server override any role below without touching
    code - e.g. add `ROLE_ADMIN=123456789012345678` to .env there.
    """
    return int(os.getenv(env_name, default))


# --- Roles: staff/permission roles (gate admin-only commands) -------------
# "Test role" - the one you swap out most often when standing up a new/test
# server. Override via ROLE_TEST_PERM in .env or here.
ROLE_TEST_PERM = _role_id("ROLE_TEST_PERM", 1135241759010590803)
ROLE_LEAD_PERMS = _role_id("ROLE_LEAD_PERMS", 876209678462382090)  # "Lead perms"
ROLE_MOD = _role_id("ROLE_MOD", 828304201586442250)                # "Mod"
ROLE_ADMIN = _role_id("ROLE_ADMIN", 775177858237857802)            # "Admin"

# Common combinations used across the has_any_role() checks in the cogs.
STAFF_ROLES = (ROLE_TEST_PERM, ROLE_LEAD_PERMS, ROLE_MOD, ROLE_ADMIN)
BACKUP_ROLES = (ROLE_MOD, ROLE_ADMIN)

# --- Roles: ELO rank roles (auto-assigned based on standing) --------------
ROLE_BALLER = _role_id("ROLE_BALLER", 1038774212413882438)
ROLE_APPRENTICE = _role_id("ROLE_APPRENTICE", 1040336000859246604)
ROLE_NOBLE = _role_id("ROLE_NOBLE", 1038774518128328725)
ROLE_HEROIC = _role_id("ROLE_HEROIC", 1038774679223160863)
ROLE_EMPEROR = _role_id("ROLE_EMPEROR", 1040724697286979585)
ROLE_ETERNAL = _role_id("ROLE_ETERNAL", 1038775020673056778)
ROLE_CHALLENGER = _role_id("ROLE_CHALLENGER", 1040152291694624818)  # "Challenger" (lowest rank role)

RANK_ROLES = {ROLE_BALLER, ROLE_APPRENTICE, ROLE_NOBLE, ROLE_HEROIC, ROLE_EMPEROR, ROLE_ETERNAL}

# --- Roles: tournament participation -----------------------------------
# NOT a rank role - just marks "signed up for the current tournament", used to
# gate access to tournament-only channels etc.
ROLE_TOURNAMENT_CONTENDER = _role_id("ROLE_TOURNAMENT_CONTENDER", 1176099066363527208)

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(levelname)-10s - %(asctime)s - %(module)-15s : %(message)s"
        },
        "standard": {"format": "%(levelname)-10s - %(name)-15s : %(message)s"},
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "standard"
        },
        "console2": {
            "level": "WARNING",
            "class": "logging.StreamHandler",
            "formatter": "standard"
        },
        "file": {
            "level": "INFO",
            "class": "logging.FileHandler",
            "filename": "logs/infos.log",
            "mode": "w",
            "formatter": "verbose"
        },
    },
    "loggers": {
        "bot": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False
        },
        "discord": {
            "handlers": ["console2", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

dictConfig(LOGGING_CONFIG)
