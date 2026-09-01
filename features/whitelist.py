"""Whitelist system: DB operations, Admins.cfg generation, background sync.

Tables
------
linked_ids      -- Discord user <-> Steam/EOS mapping (one per user).
whitelist_entries -- Manual /whitelist additions with optional expiry.
friend_entries  -- DM-based friend whitelist (tier-limited per user).
whitelist_log   -- Append-only audit trail for every mutation.
"""

import asyncio
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import discord

from config import (
    logger,
    GUILD_ID,
    WHITELIST_DB_PATH,
    WHITELIST_OUTPUT_PATH,
    WHITELIST_REFRESH_INTERVAL,
    WHITELIST_ROLE_GROUP_MAP,
    WHITELIST_GROUP_PERMS,
    WHITELIST_DEFAULT_GROUP,
    WHITELIST_FRIEND_TIERS,
    WHITELIST_TAG_PREFIX,
    WHITELIST_SFTP_REMOTE_PATH,
    SFTP_HOST,
    SFTP_PORT,
    SFTP_USER,
    SFTP_PASSWORD,
)
from bot import client
from utils.sftp import sftp_write_content

# -- database bootstrap ----------------------------------------------------

_DB = WHITELIST_DB_PATH


def _connect():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_whitelist_db():
    conn = _connect()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS linked_ids (
            discord_id   TEXT PRIMARY KEY,
            steam_id     TEXT,
            eos_id       TEXT,
            display_name TEXT,
            linked_by    TEXT NOT NULL,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS whitelist_entries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            steam_or_eos TEXT NOT NULL,
            name         TEXT NOT NULL,
            group_name   TEXT NOT NULL,
            added_by     TEXT NOT NULL,
            expires_at   TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS friend_entries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id     TEXT NOT NULL,
            steam_or_eos TEXT NOT NULL,
            label        TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(owner_id, steam_or_eos)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS whitelist_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            action    TEXT NOT NULL,
            actor     TEXT NOT NULL,
            target    TEXT NOT NULL,
            detail    TEXT,
            ts        TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    logger.info("Whitelist database initialised")


init_whitelist_db()

# -- audit helper -----------------------------------------------------------


def _log_action(conn, action: str, actor: str, target: str, detail: str = ""):
    conn.execute(
        "INSERT INTO whitelist_log (action, actor, target, detail) VALUES (?,?,?,?)",
        (action, actor, target, detail),
    )


# -- linked_ids CRUD --------------------------------------------------------


def get_linked_id(discord_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM linked_ids WHERE discord_id = ?", (discord_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_linked_id(
    discord_id: str,
    steam_id: str | None,
    eos_id: str | None,
    display_name: str,
    linked_by: str,
    *,
    overwrite: bool = False,
) -> bool:
    """Insert or (if *overwrite*) update the linked ID for a user.

    Returns True on success, False if the row already exists and
    *overwrite* is False.
    """
    conn = _connect()
    existing = conn.execute(
        "SELECT 1 FROM linked_ids WHERE discord_id = ?", (discord_id,)
    ).fetchone()

    if existing and not overwrite:
        conn.close()
        return False

    now = datetime.now(timezone.utc).isoformat()
    if existing:
        conn.execute(
            """UPDATE linked_ids
               SET steam_id=?, eos_id=?, display_name=?, linked_by=?, updated_at=?
               WHERE discord_id=?""",
            (steam_id, eos_id, display_name, linked_by, now, discord_id),
        )
        _log_action(
            conn, "overwrite_link", linked_by, discord_id,
            f"steam={steam_id} eos={eos_id}",
        )
    else:
        conn.execute(
            """INSERT INTO linked_ids
               (discord_id, steam_id, eos_id, display_name, linked_by,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (discord_id, steam_id, eos_id, display_name, linked_by, now, now),
        )
        _log_action(
            conn, "link", linked_by, discord_id,
            f"steam={steam_id} eos={eos_id}",
        )

    conn.commit()
    conn.close()
    return True


def delete_linked_id(discord_id: str, removed_by: str) -> bool:
    conn = _connect()
    cur = conn.execute(
        "DELETE FROM linked_ids WHERE discord_id = ?", (discord_id,)
    )
    deleted = cur.rowcount > 0
    if deleted:
        _log_action(conn, "unlink", removed_by, discord_id)
    conn.commit()
    conn.close()
    return deleted


# -- whitelist_entries CRUD -------------------------------------------------


def add_whitelist_entry(
    steam_or_eos: str,
    name: str,
    group_name: str,
    added_by: str,
    expires_at: str | None = None,
) -> int:
    conn = _connect()
    cur = conn.execute(
        """INSERT INTO whitelist_entries
           (steam_or_eos, name, group_name, added_by, expires_at)
           VALUES (?,?,?,?,?)""",
        (steam_or_eos, name, group_name, added_by, expires_at),
    )
    row_id = cur.lastrowid
    _log_action(
        conn, "whitelist_add", added_by, steam_or_eos,
        f"name={name} group={group_name} expires={expires_at}",
    )
    conn.commit()
    conn.close()
    return row_id


def remove_expired_whitelist_entries() -> int:
    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "DELETE FROM whitelist_entries "
        "WHERE expires_at IS NOT NULL AND expires_at <= ?",
        (now,),
    )
    removed = cur.rowcount
    if removed:
        _log_action(conn, "prune_expired", "system", str(removed), f"before={now}")
    conn.commit()
    conn.close()
    return removed


def list_whitelist_entries() -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM whitelist_entries ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# -- friend_entries CRUD ----------------------------------------------------


def get_friend_count(owner_id: str) -> int:
    conn = _connect()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM friend_entries WHERE owner_id = ?",
        (owner_id,),
    ).fetchone()
    conn.close()
    return row["cnt"]


def get_friend_entries(owner_id: str) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM friend_entries WHERE owner_id = ? ORDER BY created_at",
        (owner_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_friend_entry(owner_id: str, steam_or_eos: str, label: str = "") -> bool:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO friend_entries (owner_id, steam_or_eos, label) "
            "VALUES (?,?,?)",
            (owner_id, steam_or_eos, label),
        )
        _log_action(conn, "friend_add", owner_id, steam_or_eos, label)
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def remove_friend_entry(owner_id: str, steam_or_eos: str) -> bool:
    conn = _connect()
    cur = conn.execute(
        "DELETE FROM friend_entries WHERE owner_id = ? AND steam_or_eos = ?",
        (owner_id, steam_or_eos),
    )
    deleted = cur.rowcount > 0
    if deleted:
        _log_action(conn, "friend_remove", owner_id, steam_or_eos)
    conn.commit()
    conn.close()
    return deleted


def remove_all_friends(owner_id: str) -> int:
    conn = _connect()
    cur = conn.execute(
        "DELETE FROM friend_entries WHERE owner_id = ?", (owner_id,)
    )
    removed = cur.rowcount
    if removed:
        _log_action(conn, "friend_purge", "system", owner_id, f"removed={removed}")
    conn.commit()
    conn.close()
    return removed


# -- max-friends helper -----------------------------------------------------


def max_friends_for_member(member: discord.Member) -> int:
    """Return the highest friend-slot allowance for *member* based on roles."""
    best = 0
    member_role_ids = {str(r.id) for r in member.roles}
    for role_id_str, limit in WHITELIST_FRIEND_TIERS.items():
        if role_id_str in member_role_ids:
            best = max(best, int(limit))
    return best


# -- Admins.cfg generation (organized by group) -----------------------------


def _group_header_lines() -> list[str]:
    lines = []
    for group_name, perms in WHITELIST_GROUP_PERMS.items():
        lines.append(f"Group={group_name}:{perms}")
    return lines


async def _resolve_member(guild: discord.Guild, discord_id: str):
    try:
        return await guild.fetch_member(int(discord_id))
    except (discord.NotFound, discord.HTTPException, ValueError):
        return None


def _best_group_for_member(member: discord.Member) -> str | None:
    """Return the first matching group from WHITELIST_ROLE_GROUP_MAP."""
    member_role_ids = {str(r.id) for r in member.roles}
    for role_id_str, group_name in WHITELIST_ROLE_GROUP_MAP.items():
        if role_id_str in member_role_ids:
            return group_name
    return None


def _discord_tag(member: discord.Member) -> str:
    if member.discriminator and member.discriminator != "0":
        return f"@{member.name}#{member.discriminator}"
    return f"@{member.name}"


async def generate_admins_cfg() -> str:
    """Build the full Admins.cfg content, entries organized by group."""
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        logger.error("Cannot generate Admins.cfg: guild not found")
        return ""

    # Collect all entries keyed by group name
    grouped: dict[str, list[str]] = defaultdict(list)

    tag = f"[{WHITELIST_TAG_PREFIX}] " if WHITELIST_TAG_PREFIX else ""

    # 1) Role-based entries from linked IDs
    conn = _connect()
    linked_rows = conn.execute("SELECT * FROM linked_ids").fetchall()
    conn.close()

    for row in linked_rows:
        member = await _resolve_member(guild, row["discord_id"])
        if member is None:
            continue
        group = _best_group_for_member(member)
        if group is None:
            continue
        game_id = row["steam_id"] or row["eos_id"]
        if not game_id:
            continue
        display = row["display_name"] or member.display_name
        dtag = _discord_tag(member)
        comment = f"{tag}{display} {dtag}"
        grouped[group].append(f"Admin={game_id}:{group} // {comment}")

    # 2) Manual /whitelist entries
    for entry in list_whitelist_entries():
        comment = f"{entry['name']} (added by {entry['added_by']})"
        grouped[entry["group_name"]].append(
            f"Admin={entry['steam_or_eos']}:{entry['group_name']} // {comment}"
        )

    # 3) Friend entries -> default whitelist group
    conn = _connect()
    friend_rows = conn.execute("SELECT * FROM friend_entries").fetchall()
    conn.close()

    checked_owners: dict[str, bool] = {}
    for frow in friend_rows:
        owner_id = frow["owner_id"]
        if owner_id not in checked_owners:
            owner = await _resolve_member(guild, owner_id)
            if owner is None or max_friends_for_member(owner) == 0:
                remove_all_friends(owner_id)
                checked_owners[owner_id] = False
                continue
            checked_owners[owner_id] = True

        if not checked_owners[owner_id]:
            continue

        owner = await _resolve_member(guild, owner_id)
        owner_name = owner.display_name if owner else owner_id
        label = frow["label"] or frow["steam_or_eos"]
        comment = f"{label} (friend of {owner_name})"
        grouped[WHITELIST_DEFAULT_GROUP].append(
            f"Admin={frow['steam_or_eos']}:{WHITELIST_DEFAULT_GROUP} // {comment}"
        )

    # Build final output: group headers, then entries organized by group
    lines: list[str] = _group_header_lines()
    lines.append("")

    # Output in the order groups appear in WHITELIST_GROUP_PERMS config,
    # then any extra groups that only appear in data
    ordered_groups = list(WHITELIST_GROUP_PERMS.keys())
    extra_groups = [g for g in grouped if g not in ordered_groups]
    for group_name in ordered_groups + extra_groups:
        entries = grouped.get(group_name)
        if not entries:
            continue
        for entry_line in entries:
            lines.append(entry_line)

    return "\n".join(lines) + "\n"


async def write_admins_cfg() -> bool:
    """Generate and write the Admins.cfg file. Optionally push via SFTP."""
    content = await generate_admins_cfg()
    if not content:
        return False

    path = Path(WHITELIST_OUTPUT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info("Wrote Admins.cfg (%d bytes) to %s", len(content), path)

    if WHITELIST_SFTP_REMOTE_PATH and SFTP_HOST:
        ok = await sftp_write_content(
            SFTP_HOST, SFTP_PORT, SFTP_USER, SFTP_PASSWORD,
            WHITELIST_SFTP_REMOTE_PATH, content,
        )
        if ok:
            logger.info("Pushed Admins.cfg to SFTP %s", WHITELIST_SFTP_REMOTE_PATH)
        else:
            logger.error("Failed to push Admins.cfg to SFTP")

    return True


# -- background loop -------------------------------------------------------


async def whitelist_background_task():
    """Periodically regenerate Admins.cfg and prune expired entries."""
    await client.wait_until_ready()
    logger.info(
        "Whitelist background task started (interval=%ds)",
        WHITELIST_REFRESH_INTERVAL,
    )
    while not client.is_closed():
        try:
            removed = remove_expired_whitelist_entries()
            if removed:
                logger.info("Pruned %d expired whitelist entries", removed)
            await write_admins_cfg()
        except Exception:
            logger.exception("Error in whitelist background task")
        await asyncio.sleep(WHITELIST_REFRESH_INTERVAL)
