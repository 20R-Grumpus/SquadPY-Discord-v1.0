"""Admin config: Admins.cfg generation, backups, remote list."""

import os
from pathlib import Path

import requests
from discord import app_commands

from config import logger, BACKUP_DIR, COMP_ADMIN_LIST_URL

ADMIN_HEADER = (
    "Group=Admin:forceteamchange,kick,ban,changemap,balance,pause,chat,"
    "private,cheat,immune,manageserver,teamchange,canseeadminchat,reserve,"
    "startvote,config,cameraman,featuretest"
)

REGION_CHOICES = [
    app_commands.Choice(name="NA", value="NA"),
    app_commands.Choice(name="EU", value="EU"),
    app_commands.Choice(name="Both", value="Both"),
]

REMOTE_LIST_URL = COMP_ADMIN_LIST_URL


def build_admins_cfg(steamids: list[str], role="Admin", comment="Match Config") -> str:
    lines = [ADMIN_HEADER] + [f"Admin={sid}:{role} // {comment}" for sid in steamids]
    return "\n".join(lines) + "\n"


def get_latest_backup(server_region: str) -> Path:
    pattern = BACKUP_DIR / f"*_{server_region}_Admins.cfg"
    backups = list(pattern.parent.glob(pattern.name))
    if not backups:
        raise FileNotFoundError("No local backup found")
    return max(backups, key=os.path.getctime)


def fetch_remote_list() -> str:
    try:
        r = requests.get(REMOTE_LIST_URL, timeout=10)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.error(f"Failed to fetch remote admin list: {e}")
        return ""
