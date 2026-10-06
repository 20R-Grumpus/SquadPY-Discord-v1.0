"""SQLite operations for BattleMetrics bans."""

import json
import sqlite3
from datetime import datetime, timezone

from config import (
    logger,
    BATTLEMETRICS_BANS_DB_PATH,
    SQUADJS_SFTP_HOST,
    SQUADJS_SFTP_PORT,
    SQUADJS_SFTP_USER,
    SQUADJS_SFTP_PASSWORD,
    SQUADJS_SFTP_BANNED_PLAYERS_PATH,
)

from utils.sftp import sftp_write_content


def get_connection():
    conn = sqlite3.connect(BATTLEMETRICS_BANS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_battlemetrics_bans_db():
    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS battlemetrics_bans (
                ban_id TEXT PRIMARY KEY,

                uid TEXT,
                ban_list_id TEXT NOT NULL,

                player_id TEXT,
                player_name TEXT,
                steamid TEXT,
                eosid TEXT,

                server_id TEXT,
                organization_id TEXT,
                user_id TEXT,

                timestamp TEXT,
                expires TEXT,

                reason TEXT,
                note TEXT,

                org_wide INTEGER,
                auto_add_enabled INTEGER,
                native_enabled INTEGER,

                discord_thread_id TEXT,
                discord_posted INTEGER NOT NULL DEFAULT 0,
                discord_deleted_posted INTEGER NOT NULL DEFAULT 0,
                discord_last_posted_snapshot TEXT,

                bm_present INTEGER NOT NULL DEFAULT 1,

                last_seen_at TEXT NOT NULL,
                last_updated_at TEXT NOT NULL
            )
            """)

        # ---------------------------------------------------------
        # Database migration
        #
        # The database may already exist from the previous version.
        # CREATE TABLE IF NOT EXISTS does not add new columns to an
        # existing table, so check for the new column and add it if
        # necessary.
        # ---------------------------------------------------------

        cursor.execute("""
            PRAGMA table_info(battlemetrics_bans)
            """)

        columns = {row["name"] for row in cursor.fetchall()}

        if "discord_deleted_posted" not in columns:
            cursor.execute("""
                ALTER TABLE battlemetrics_bans
                ADD COLUMN discord_deleted_posted
                INTEGER NOT NULL DEFAULT 0
                """)

            logger.info(
                "Added discord_deleted_posted column to BattleMetrics bans database"
            )

            columns.add("discord_deleted_posted")

        if "discord_last_posted_snapshot" not in columns:
            cursor.execute("""
                ALTER TABLE battlemetrics_bans
                ADD COLUMN discord_last_posted_snapshot TEXT
                """)

            logger.info(
                "Added discord_last_posted_snapshot column to BattleMetrics bans database"
            )

            columns.add("discord_last_posted_snapshot")

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bans_steamid
            ON battlemetrics_bans(steamid)
            """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bans_eosid
            ON battlemetrics_bans(eosid)
            """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bans_expires
            ON battlemetrics_bans(expires)
            """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bans_timestamp
            ON battlemetrics_bans(timestamp)
            """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """)

        conn.commit()

    finally:
        conn.close()


def get_sync_state(key):
    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT value
            FROM sync_state
            WHERE key = ?
            """,
            (key,),
        )

        row = cursor.fetchone()

        if row is None:
            return None

        return row["value"]

    finally:
        conn.close()


def set_sync_state(key, value):
    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO sync_state(key, value)
            VALUES (?, ?)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )

        conn.commit()

    finally:
        conn.close()


def get_ban_by_id(ban_id):
    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT *
            FROM battlemetrics_bans
            WHERE ban_id = ?
            """,
            (str(ban_id),),
        )

        row = cursor.fetchone()

        if row is None:
            return None

        return dict(row)

    finally:
        conn.close()


def get_all_bans():
    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT *
            FROM battlemetrics_bans
            ORDER BY timestamp DESC
            """)

        return [dict(row) for row in cursor.fetchall()]

    finally:
        conn.close()


def upsert_ban(ban):
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT ban_id
            FROM battlemetrics_bans
            WHERE ban_id = ?
            """,
            (ban["ban_id"],),
        )

        existing = cursor.fetchone()

        cursor.execute(
            """
            INSERT INTO battlemetrics_bans (
                ban_id,
                uid,
                ban_list_id,

                player_id,
                player_name,
                steamid,
                eosid,

                server_id,
                organization_id,
                user_id,

                timestamp,
                expires,

                reason,
                note,

                org_wide,
                auto_add_enabled,
                native_enabled,

                discord_thread_id,
                discord_posted,
                discord_deleted_posted,
                discord_last_posted_snapshot,

                bm_present,

                last_seen_at,
                last_updated_at
            )
            VALUES (
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                NULL,
                0,
                0,
                NULL,
                1,
                ?, ?
            )
            ON CONFLICT(ban_id)
            DO UPDATE SET
                uid = excluded.uid,
                ban_list_id = excluded.ban_list_id,

                player_id = excluded.player_id,
                player_name = excluded.player_name,
                steamid = excluded.steamid,
                eosid = excluded.eosid,

                server_id = excluded.server_id,
                organization_id = excluded.organization_id,
                user_id = excluded.user_id,

                timestamp = excluded.timestamp,
                expires = excluded.expires,

                reason = excluded.reason,
                note = excluded.note,

                org_wide = excluded.org_wide,
                auto_add_enabled = excluded.auto_add_enabled,
                native_enabled = excluded.native_enabled,

                bm_present = 1,
                last_seen_at = excluded.last_seen_at,
                last_updated_at = excluded.last_updated_at
            """,
            (
                ban["ban_id"],
                ban.get("uid"),
                ban["ban_list_id"],
                ban.get("player_id"),
                ban.get("player_name"),
                ban.get("steamid"),
                ban.get("eosid"),
                ban.get("server_id"),
                ban.get("organization_id"),
                ban.get("user_id"),
                ban.get("timestamp"),
                ban.get("expires"),
                ban.get("reason"),
                ban.get("note"),
                ban.get("org_wide"),
                ban.get("auto_add_enabled"),
                ban.get("native_enabled"),
                now,
                now,
            ),
        )

        conn.commit()

        return existing is None

    finally:
        conn.close()


def bulk_import_battlemetrics_bans(bans):
    """
    Import the complete first BattleMetrics baseline in one
    SQLite transaction.

    Historical bans are marked as Discord-skipped and their
    current BattleMetrics state is saved as the Discord baseline.
    No Discord forum threads are created.
    """
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()

    try:
        cursor = conn.cursor()

        for ban in bans:
            snapshot = json.dumps(
                {
                    "expires": ban.get("expires"),
                    "reason": ban.get("reason"),
                    "note": ban.get("note"),
                    "player_name": ban.get("player_name"),
                    "server_id": ban.get("server_id"),
                    "user_id": ban.get("user_id"),
                },
                sort_keys=True,
                ensure_ascii=False,
            )

            cursor.execute(
                """
                INSERT INTO battlemetrics_bans (
                    ban_id,
                    uid,
                    ban_list_id,
                    player_id,
                    player_name,
                    steamid,
                    eosid,
                    server_id,
                    organization_id,
                    user_id,
                    timestamp,
                    expires,
                    reason,
                    note,
                    org_wide,
                    auto_add_enabled,
                    native_enabled,
                    discord_thread_id,
                    discord_posted,
                    discord_deleted_posted,
                    discord_last_posted_snapshot,
                    bm_present,
                    last_seen_at,
                    last_updated_at
                )
                VALUES (
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    NULL,
                    1,
                    0,
                    ?,
                    1,
                    ?, ?
                )
                ON CONFLICT(ban_id)
                DO UPDATE SET
                    uid = excluded.uid,
                    ban_list_id = excluded.ban_list_id,
                    player_id = excluded.player_id,
                    player_name = excluded.player_name,
                    steamid = excluded.steamid,
                    eosid = excluded.eosid,
                    server_id = excluded.server_id,
                    organization_id = excluded.organization_id,
                    user_id = excluded.user_id,
                    timestamp = excluded.timestamp,
                    expires = excluded.expires,
                    reason = excluded.reason,
                    note = excluded.note,
                    org_wide = excluded.org_wide,
                    auto_add_enabled = excluded.auto_add_enabled,
                    native_enabled = excluded.native_enabled,
                    discord_posted = 1,
                    discord_deleted_posted = 0,
                    discord_last_posted_snapshot = excluded.discord_last_posted_snapshot,
                    bm_present = 1,
                    last_seen_at = excluded.last_seen_at,
                    last_updated_at = excluded.last_updated_at
                """,
                (
                    ban["ban_id"],
                    ban.get("uid"),
                    ban["ban_list_id"],
                    ban.get("player_id"),
                    ban.get("player_name"),
                    ban.get("steamid"),
                    ban.get("eosid"),
                    ban.get("server_id"),
                    ban.get("organization_id"),
                    ban.get("user_id"),
                    ban.get("timestamp"),
                    ban.get("expires"),
                    ban.get("reason"),
                    ban.get("note"),
                    ban.get("org_wide"),
                    ban.get("auto_add_enabled"),
                    ban.get("native_enabled"),
                    snapshot,
                    now,
                    now,
                ),
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def mark_all_bans_not_seen():
    """
    Called only after a COMPLETE successful BattleMetrics sync.

    Historical records remain in the database.
    """
    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE battlemetrics_bans
            SET bm_present = 0
            """)

        conn.commit()

    finally:
        conn.close()


def mark_ban_discord_posted(ban_id, thread_id):
    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE battlemetrics_bans
            SET
                discord_posted = 1,
                discord_thread_id = ?
            WHERE ban_id = ?
            """,
            (str(thread_id), str(ban_id)),
        )

        conn.commit()

    finally:
        conn.close()


def mark_ban_discord_snapshot_posted(ban_id, snapshot):
    """
    Save the BattleMetrics ban state that was successfully posted
    to the Discord evidence thread.
    """
    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE battlemetrics_bans
            SET discord_last_posted_snapshot = ?
            WHERE ban_id = ?
            """,
            (snapshot, str(ban_id)),
        )

        conn.commit()

    finally:
        conn.close()


def mark_ban_discord_skipped(ban_id):
    """
    Used during the first baseline sync.

    The ban is historical data we have imported, so it should not
    generate a Discord forum post.
    """
    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE battlemetrics_bans
            SET discord_posted = 1
            WHERE ban_id = ?
            """,
            (str(ban_id),),
        )

        conn.commit()

    finally:
        conn.close()


def mark_ban_discord_deleted_posted(ban_id):
    """
    Mark the BattleMetrics deletion notification as successfully
    posted to the existing Discord evidence thread.
    """
    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE battlemetrics_bans
            SET discord_deleted_posted = 1
            WHERE ban_id = ?
            """,
            (str(ban_id),),
        )

        conn.commit()

    finally:
        conn.close()


def get_all_currently_active_bans():
    """
    Return only bans that are currently present in BattleMetrics
    and have not expired.
    """

    now = datetime.now(timezone.utc)

    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT *
            FROM battlemetrics_bans
            WHERE bm_present = 1
            ORDER BY timestamp DESC
            """)

        rows = [dict(row) for row in cursor.fetchall()]

    finally:
        conn.close()

    active = []

    for ban in rows:
        expires = ban.get("expires")

        # No expiration = permanent ban
        if not expires:
            active.append(ban)
            continue

        try:
            expiration = datetime.fromisoformat(expires.replace("Z", "+00:00"))

            if expiration > now:
                active.append(ban)

        except ValueError:
            logger.warning(
                "Could not parse expiration for BM ban %s: %s",
                ban.get("ban_id"),
                expires,
            )

    return active


def build_active_bans_json():
    active_bans = get_all_currently_active_bans()

    output = []

    for ban in active_bans:
        record = {
            "name": ban.get("player_name"),
            "steamid": ban.get("steamid"),
            "eosid": ban.get("eosid"),
            "ban_id": ban.get("ban_id"),
            "ban_timestamp": ban.get("timestamp"),
            "ban_expires": ban.get("expires"),
            "reason": ban.get("reason"),
        }

        # Remove null values.
        record = {key: value for key, value in record.items() if value is not None}

        output.append(record)

    return output


async def push_active_bans_to_sftp():
    bans = build_active_bans_json()

    content = json.dumps(
        bans,
        indent=2,
        ensure_ascii=False,
    )

    try:
        await sftp_write_content(
            host=SQUADJS_SFTP_HOST,
            port=SQUADJS_SFTP_PORT,
            username=SQUADJS_SFTP_USER,
            password=SQUADJS_SFTP_PASSWORD,
            remote_path=SQUADJS_SFTP_BANNED_PLAYERS_PATH,
            content=content,
        )

        logger.info(
            "Pushed %s active BattleMetrics bans to SquadJS",
            len(bans),
        )

        return True

    except Exception:
        logger.exception("Failed to push active BattleMetrics bans to SquadJS")

        return False


init_battlemetrics_bans_db()
