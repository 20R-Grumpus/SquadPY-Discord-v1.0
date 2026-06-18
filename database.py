"""SQLite operations for the banned-players database."""

import json
import sqlite3

from config import (
    logger,
    BANNED_PLAYERS_DB_PATH,
    SQUADJS_SFTP_HOST,
    SQUADJS_SFTP_PORT,
    SQUADJS_SFTP_USER,
    SQUADJS_SFTP_PASSWORD,
    SQUADJS_SFTP_BANNED_PLAYERS_PATH,
)
from utils.sftp import sftp_write_content


def init_banned_players_db():
    """Initialize SQLite database for banned players"""
    try:
        conn = sqlite3.connect(BANNED_PLAYERS_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Create banned_players table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS banned_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                steamid TEXT UNIQUE NOT NULL,
                eosid TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        logger.info("Banned players database initialized successfully")
        return True
    except Exception as e:
        logger.error(
            f"Failed to initialize banned players database: {e}", exc_info=True
        )
        return False


# Initialize database on startup
init_banned_players_db()
def load_banned_players() -> list:
    """Load all banned players from SQLite database"""
    try:
        conn = sqlite3.connect(BANNED_PLAYERS_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM banned_players ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()

        # Convert rows to list of dicts
        banned_list = [dict(row) for row in rows]
        logger.info(f"Loaded {len(banned_list)} banned players from database")
        return banned_list
    except Exception as e:
        logger.warning(f"Could not load banned_players from database: {e}")
    return []


def player_exists(banned_list: list, steamid: str) -> bool:
    """Check if player already exists in banned list by steamid"""
    try:
        conn = sqlite3.connect(BANNED_PLAYERS_DB_PATH)
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM banned_players WHERE steamid = ?", (steamid,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    except Exception as e:
        logger.error(f"Error checking if player exists: {e}")
        return False


def find_player_by_steamid(banned_list: list, steamid: str) -> dict | None:
    """Find player in banned list by steamid and return the player object"""
    try:
        conn = sqlite3.connect(BANNED_PLAYERS_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM banned_players WHERE steamid = ?", (steamid,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return dict(row)
        return None
    except Exception as e:
        logger.error(f"Error finding player by steamid: {e}")
        return None


def save_banned_players(banned_list: list) -> bool:
    """This function is kept for compatibility but is not used with SQLite"""
    # With SQLite, changes are committed immediately when adding/removing players
    logger.info(f"save_banned_players called (no-op with SQLite backend)")
    return True


async def push_banned_players_to_sftp(banned_list: list = None) -> bool:
    """Export banned players from database to JSON and upload to squadjs SFTP"""
    try:
        # Load all banned players from database if not provided
        if banned_list is None:
            banned_list = load_banned_players()

        # Convert database format to JSON-compatible format
        json_list = []
        for player in banned_list:
            json_entry = {
                "name": player.get("name"),
                "steamid": player.get("steamid"),
            }
            if player.get("eosid"):
                json_entry["eosid"] = player.get("eosid")
            json_list.append(json_entry)

        content = json.dumps(json_list, indent=2)
        success = await sftp_write_content(
            SQUADJS_SFTP_HOST,
            SQUADJS_SFTP_PORT,
            SQUADJS_SFTP_USER,
            SQUADJS_SFTP_PASSWORD,
            SQUADJS_SFTP_BANNED_PLAYERS_PATH,
            content,
        )
        if success:
            logger.info(f"Pushed {len(json_list)} banned players to SFTP")
        return success
    except Exception as e:
        logger.error(f"Failed to push banned_players to SFTP: {e}", exc_info=True)
        return False
