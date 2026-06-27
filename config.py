"""Configuration loading and validation.

All environment-derived constants live here. Importing this module loads the
`.env`/`env.txt` file and validates required values.
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(funcName)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Timezone setup
london_tz = pytz.timezone("Europe/London")

# Project root and a portable data directory for files the bot creates.
BASE_DIR = Path(__file__).resolve().parent

load_dotenv(dotenv_path=os.getenv("ENV_FILE", str(BASE_DIR / "env.txt")))

DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Discord and environment config
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
COMMAND_CHANNEL_ID = int(os.getenv("COMMAND_CHANNEL_ID"))
MESSAGE_CHANNEL_ID = int(os.getenv("MESSAGE_CHANNEL_ID"))
SQUAD_ROLE_ID = int(os.getenv("SQUAD_ROLE_ID"))
PROSPECT_ROLE_ID = int(os.getenv("PROSPECT_ROLE_ID"))
BATTLEMETRICS_SERVER_ID = int(os.getenv("MAIN_BATTLEMETRICS_SERVER_ID"))
MILESTONE_STEP = int(os.getenv("MILESTONE_STEP"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS"))
LIVE_PLAYER_COUNT = int(os.getenv("LIVE_PLAYER_COUNT"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
allowed_roles_env = os.getenv("ALLOWED_ROLE_IDS", "")
RESTART_HOUR = int(os.getenv("RESTART_HOUR"))
RESTART_MINUTE = int(os.getenv("RESTART_MINUTE"))
SERVER_LIST = json.loads(os.getenv("COMPETING_SERVER_DICT", "{}"))
ALLOWED_ROLE_IDS = set(
    int(role_id.strip()) for role_id in allowed_roles_env.split(",") if role_id.strip()
)
exclude_roles_env = os.getenv("PROSPECT_EXCLUDE_ROLE_IDS", "")
PROSPECT_EXCLUDE_ROLE_IDS = set(
    int(role_id.strip()) for role_id in exclude_roles_env.split(",") if role_id.strip()
)
GUILD_ID = int(os.getenv("GUILD_ID"))
STICKY_CHANNEL_ID = int(os.getenv("STICKY_CHANNEL_ID", "0"))
FORUM_CHANNEL_ID = int(os.getenv("FORUM_CHANNEL_ID"))
PROSPECT_ANNOUNCEMENT_CHANNEL_ID = int(os.getenv("PROSPECT_ANNOUNCEMENT_CHANNEL_ID"))

# Channel used to mirror seeding messages when a command is run there
TEST_CHANNEL_ID = int(os.getenv("TEST_CHANNEL_ID", "0"))

# Channels referenced in the prospect welcome message
PROSPECT_QUESTIONS_CHANNEL_ID = int(os.getenv("PROSPECT_QUESTIONS_CHANNEL_ID", "0"))
PROSPECT_FORM_CHANNEL_ID = int(os.getenv("PROSPECT_FORM_CHANNEL_ID", "0"))
PROSPECT_RULES_CHANNEL_ID = int(os.getenv("PROSPECT_RULES_CHANNEL_ID", "0"))
PROSPECT_INFO_CHANNEL_ID = int(os.getenv("PROSPECT_INFO_CHANNEL_ID", "0"))

# Sticky reminder embed references
STICKY_PING_ROLE_ID = int(os.getenv("STICKY_PING_ROLE_ID", "0"))
STICKY_TICKET_CHANNEL_ID = int(os.getenv("STICKY_TICKET_CHANNEL_ID", "0"))
STICKY_STATE_FILE = os.getenv("STICKY_STATE_FILE", str(DATA_DIR / "sticky_state.json"))

# Custom emoji used in Discord messages (Discord emoji markup, e.g. <a:name:id>)
ALARM_EMOJI = os.getenv("ALARM_EMOJI", "")
ROTATION_EMOJI = os.getenv("ROTATION_EMOJI", "")

# Community-specific text
COMMUNITY_NAME = os.getenv("COMMUNITY_NAME", "the community")
PROSPECT_NICK_PREFIX = os.getenv("PROSPECT_NICK_PREFIX", "")

# Optional public join-link redirect template, e.g. "https://example.com/join?id={lobby_id}".
# When empty, the raw join URL is used as-is.
JOIN_REDIRECT_URL_TEMPLATE = os.getenv("JOIN_REDIRECT_URL_TEMPLATE", "")

# squad browser config
JOIN_LINK_API_KEY = os.getenv("JOIN_LINK_API_KEY")
JOIN_LINK_API_URL = os.getenv("JOIN_LINK_API_URL")
JOIN_LINK_SERVER_NAME = os.getenv("JOIN_LINK_SERVER_NAME")
JOIN_LINK_REFRESH_INTERVAL = int(os.getenv("JOIN_LINK_REFRESH_INTERVAL", "300"))
JOIN_LINK_RCON_HOST = os.getenv("JOIN_LINK_RCON_HOST")
JOIN_LINK_RCON_PORT = int(os.getenv("JOIN_LINK_RCON_PORT"))
JOIN_LINK_RCON_PASSWORD = os.getenv("JOIN_LINK_RCON_PASSWORD")

# Seeding cooldown and milestones
COOLDOWN_HOURS = 8
milestones = list(range(10, 70, MILESTONE_STEP))

# Auto-seeding range
AUTO_TRIGGER_MIN = 45
AUTO_TRIGGER_MAX = 55

# Time window in London time.
AUTO_SEED_START_HOUR_LONDON = 11  # 11:00 London time
AUTO_SEED_END_HOUR_LONDON = 18  # 18:00 London time

# Google Sheets config
WEEKLY_SHEET_IDS = {
    1: os.getenv("GOOGLE_SHEET_ID_WEEK_1"),
    2: os.getenv("GOOGLE_SHEET_ID_WEEK_2"),
}

COMP_ROSTER_SHEET_ID = os.getenv("COMP_ROSTER_SHEET_ID")

GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE")
cycle_start_str = os.getenv("CYCLE_START_DATE", "2025-06-01")
try:
    CYCLE_START_DATE = datetime.strptime(cycle_start_str, "%Y-%m-%d").replace(
        tzinfo=london_tz
    )
except ValueError:
    logger.error(
        f"Invalid CYCLE_START_DATE format: {cycle_start_str}. Expected YYYY-MM-DD."
    )
    raise

# Google Sheets API Scopes
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# SFTP Config
SFTP_HOST = os.getenv("SFTP_HOST")
SFTP_PORT = int(os.getenv("SFTP_PORT", "22"))
SFTP_USER = os.getenv("SFTP_USER")
SFTP_PASSWORD = os.getenv("SFTP_PASSWORD")
SFTP_REMOTE_PATH = os.getenv("SFTP_REMOTE_PATH")
SFTP_REMOTE_LOG_PATH = os.getenv("SFTP_REMOTE_LOG_PATH")

# Fog SFTP Config
SQUADJS_SFTP_HOST = os.getenv("SQUADJS_SFTP_HOST")
SQUADJS_SFTP_PORT = int(os.getenv("SQUADJS_SFTP_PORT", "22"))
SQUADJS_SFTP_USER = os.getenv("SQUADJS_SFTP_USER")
SQUADJS_SFTP_PASSWORD = os.getenv("SQUADJS_SFTP_PASSWORD")
SQUADJS_SFTP_FOG_PATH = os.getenv("SQUADJS_SFTP_FOG_PATH")
SQUADJS_SFTP_IP_TRACK_PATH = os.getenv("SQUADJS_SFTP_IP_TRACK_PATH")
SQUADJS_SFTP_BANNED_PLAYERS_PATH = os.getenv(
    "SQUADJS_SFTP_BANNED_PLAYERS_PATH", "/data/banned_players.json"
)
FOG_JSON_PATH = Path(os.getenv("FOG_JSON_PATH", str(DATA_DIR / "fog_off_maps.json")))
BANNED_PLAYERS_DB_PATH = os.getenv(
    "BANNED_PLAYERS_DB_PATH", str(DATA_DIR / "banned_players.db")
)

# Comp SFTP config
SFTP_COMP_NA_HOST = os.getenv("SFTP_COMP_NA_HOST")
SFTP_COMP_NA_PORT = int(os.getenv("SFTP_COMP_NA_PORT", "22"))
SFTP_COMP_NA_USER = os.getenv("SFTP_COMP_NA_USER")
SFTP_COMP_NA_PASSWORD = os.getenv("SFTP_COMP_NA_PASSWORD")

SFTP_COMP_EU_HOST = os.getenv("SFTP_COMP_EU_HOST")
SFTP_COMP_EU_PORT = int(os.getenv("SFTP_COMP_EU_PORT", "22"))
SFTP_COMP_EU_USER = os.getenv("SFTP_COMP_EU_USER")
SFTP_COMP_EU_PASSWORD = os.getenv("SFTP_COMP_EU_PASSWORD")

SFTP_COMP_ADMIN_PATH = "SquadGame/ServerConfig/Admins.cfg"
SFTP_COMP_REMOTEADMIN_PATH = "SquadGame/ServerConfig/RemoteAdminListHosts.cfg"

COMP_SFTP_SERVERS = {
    "NA": {
        "host": SFTP_COMP_NA_HOST,
        "port": SFTP_COMP_NA_PORT,
        "username": SFTP_COMP_NA_USER,
        "password": SFTP_COMP_NA_PASSWORD,
    },
    "EU": {
        "host": SFTP_COMP_EU_HOST,
        "port": SFTP_COMP_EU_PORT,
        "username": SFTP_COMP_EU_USER,
        "password": SFTP_COMP_EU_PASSWORD,
    },
}

# Message sent to Discord when seeding is triggered
SEEDING_MESSAGE = (
    f"{ALARM_EMOJI}{ALARM_EMOJI}{ALARM_EMOJI} "
    f"<@&{SQUAD_ROLE_ID}> We're seeding! Hop on and help fill the server! "
    f"{ALARM_EMOJI}{ALARM_EMOJI}{ALARM_EMOJI}"
)
LIVE_MESSAGE = "Live!"
PLAYER_COUNT_MESSAGE = "Currently {milestone} players online!"

# Unified persistent state store (all sections live here)
UNIFIED_STATE_FILE = os.getenv("UNIFIED_STATE_FILE", str(DATA_DIR / "bot_state.json"))

# Legacy per-feature state files (migrated into UNIFIED_STATE_FILE on first read)
POP_TRACK_FILE = os.getenv("POP_TRACK_FILE", str(DATA_DIR / "seedtrack_state.json"))
# Persistent file for rotation tracking
ROTATION_TRACK_FILE = os.getenv(
    "ROTATION_TRACK_FILE", str(DATA_DIR / "rotation_state.json")
)

# Seeding leaderboard data file
SEEDING_LEADERBOARD_FILE = os.getenv(
    "SEEDING_LEADERBOARD_FILE", str(DATA_DIR / "seeding_leaderboard.json")
)

# Remote competitive admin list URL
COMP_ADMIN_LIST_URL = os.getenv("COMP_ADMIN_LIST_URL", "")

# Local directory for admin config backups
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(DATA_DIR / "admin_backups")))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Whitelist system
# ---------------------------------------------------------------------------

# SQLite database for whitelist data (linked IDs, manual whitelists, friends)
WHITELIST_DB_PATH = os.getenv("WHITELIST_DB_PATH", str(DATA_DIR / "whitelist.db"))

# Local path where the generated Admins.cfg is written
WHITELIST_OUTPUT_PATH = os.getenv(
    "WHITELIST_OUTPUT_PATH", str(DATA_DIR / "Admins.cfg")
)

# How often (seconds) the background task regenerates Admins.cfg and prunes
# expired entries / revoked roles.
WHITELIST_REFRESH_INTERVAL = int(os.getenv("WHITELIST_REFRESH_INTERVAL", "300"))

# JSON mapping of Discord role IDs to Squad permission group names.
# Example: {"123456": "Admin", "789012": "Moderator"}
WHITELIST_ROLE_GROUP_MAP = json.loads(os.getenv("WHITELIST_ROLE_GROUP_MAP", "{}"))

# JSON mapping of Squad group names to their permissions.
# Example: {"Admin": "balance,ban,cameraman,...", "Moderator": "canseeadminchat,..."}
WHITELIST_GROUP_PERMS = json.loads(os.getenv("WHITELIST_GROUP_PERMS", "{}"))

# Role IDs allowed to use /whitelist (comma-separated). Falls back to
# ALLOWED_ROLE_IDS when empty.
_wl_roles_env = os.getenv("WHITELIST_ALLOWED_ROLE_IDS", "")
WHITELIST_ALLOWED_ROLE_IDS = set(
    int(r.strip()) for r in _wl_roles_env.split(",") if r.strip()
) or ALLOWED_ROLE_IDS

# The default group assigned by /whitelist when no explicit group is given.
WHITELIST_DEFAULT_GROUP = os.getenv("WHITELIST_DEFAULT_GROUP", "Whitelisted")

# Friend-whitelist tiers: JSON mapping role ID -> max friends allowed.
# Example: {"111": 3, "222": 5, "333": 10}
WHITELIST_FRIEND_TIERS = json.loads(os.getenv("WHITELIST_FRIEND_TIERS", "{}"))

# Tag prefix added to Admin= comment lines, e.g. "[20R] ".
WHITELIST_TAG_PREFIX = os.getenv("WHITELIST_TAG_PREFIX", "")

# Optional: SFTP target to push generated Admins.cfg.
# Uses main SFTP credentials. Leave blank to skip SFTP push.
WHITELIST_SFTP_REMOTE_PATH = os.getenv("WHITELIST_SFTP_REMOTE_PATH", "")
