"""BattleMetrics ban synchronization and Discord forum integration."""

import asyncio
import html
import json
import re
import unicodedata
from datetime import datetime

import discord

from bot import client

from config import (
    logger,
    FORUM_CHANNEL_ID,
    BATTLEMETRICS_BAN_CHECK_INTERVAL,
    BATTLEMETRICS_BAN_LIST_ID,
)

from database import (
    get_all_bans,
    get_sync_state,
    set_sync_state,
    bulk_upsert_battlemetrics_bans,
    bulk_import_battlemetrics_bans,
    mark_all_bans_not_seen,
    mark_ban_discord_posted,
    mark_ban_discord_deleted_posted,
    mark_ban_discord_snapshot_posted,
    push_active_bans_to_sftp,
)

from utils.retry import fetch_battlemetrics_bans


def _build_included_lookup(included):
    lookup = {}

    for item in included or []:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        item_id = item.get("id")

        if not item_type or not item_id:
            continue

        lookup[(item_type, str(item_id))] = item

    return lookup


def _parse_identifier(identifier):
    if not isinstance(identifier, dict):
        return None, None

    identifier_type = identifier.get("type")
    value = identifier.get("identifier")

    if not identifier_type or not value:
        return None, None

    return identifier_type.lower(), str(value)


def _extract_player_identifiers(player_object):
    steamid = None
    eosid = None

    if not player_object:
        return steamid, eosid

    attributes = player_object.get("attributes", {})

    identifiers = attributes.get("identifiers", [])

    if not isinstance(identifiers, list):
        return steamid, eosid

    for identifier in identifiers:

        identifier_type, value = _parse_identifier(identifier)

        if not identifier_type:
            continue

        if identifier_type in (
            "steamid",
            "steam",
        ):
            steamid = value

        elif identifier_type in (
            "eosid",
            "eos",
            "uniqueid",
        ):
            eosid = value

    return steamid, eosid


def normalize_ban(raw_ban, included_lookup):
    attributes = raw_ban.get("attributes", {})
    relationships = raw_ban.get("relationships", {})

    ban_id = raw_ban.get("id")

    if not ban_id:
        return None

    ban_id = str(ban_id)

    player_relationship = relationships.get("player", {})
    player_data = player_relationship.get("data") or {}

    player_id = player_data.get("id")

    player_object = None

    if player_id:
        player_object = included_lookup.get(("player", str(player_id)))

    player_name = None
    steamid = None
    eosid = None

    if player_object:
        player_attributes = player_object.get("attributes", {})
        player_name = player_attributes.get("name")
        steamid, eosid = _extract_player_identifiers(player_object)

    issued_by = None

    user_relationship = relationships.get("user", {})
    user_data = user_relationship.get("data") or {}
    user_id = user_data.get("id")

    if user_id:
        user_object = included_lookup.get(("user", str(user_id)))

        if user_object:
            user_attributes = user_object.get("attributes", {})
            issued_by = user_attributes.get("nickname")

    # Fallback in case BattleMetrics places identifiers
    # directly on the ban record.
    if not steamid or not eosid:

        for identifier in attributes.get(
            "identifiers",
            [],
        ):

            identifier_type, value = _parse_identifier(identifier)

            if not identifier_type:
                continue

            if not steamid and identifier_type in (
                "steamid",
                "steam",
            ):
                steamid = value

            elif not eosid and identifier_type in (
                "eosid",
                "eos",
                "uniqueid",
            ):
                eosid = value

    def relationship_id(name):
        relationship = relationships.get(name, {})
        data = relationship.get("data")

        if isinstance(data, dict):
            return data.get("id")

        return None

    ban_list_id = relationship_id("banList")

    if not ban_list_id:
        ban_list_id = BATTLEMETRICS_BAN_LIST_ID

    return {
        "ban_id": ban_id,
        "uid": attributes.get("uid"),
        "ban_list_id": str(ban_list_id),
        "player_id": (str(player_id) if player_id is not None else None),
        "player_name": player_name,
        "steamid": steamid,
        "eosid": eosid,
        "server_id": relationship_id("server"),
        "organization_id": relationship_id("organization"),
        "user_id": relationship_id("user"),
        "issued_by": issued_by,
        "timestamp": attributes.get("timestamp"),
        "expires": attributes.get("expires"),
        "reason": attributes.get("reason"),
        "note": attributes.get("note"),
        "org_wide": (1 if attributes.get("orgWide") else 0),
        "auto_add_enabled": (1 if attributes.get("autoAddEnabled") else 0),
        "native_enabled": (
            1
            if attributes.get("nativeEnabled")
            else (0 if attributes.get("nativeEnabled") is not None else None)
        ),
    }


def sanitize_forum_thread_name(
    name,
    steamid=None,
    ban_id=None,
):
    """
    Make a safe Discord forum thread name.

    Normal Unicode and punctuation are preserved.
    Control/format/surrogate characters are removed.
    """

    name = str(name or "").strip()

    cleaned = "".join(
        char for char in name if not unicodedata.category(char).startswith("C")
    )

    cleaned = re.sub(
        r"\s+",
        " ",
        cleaned,
    ).strip()

    if not cleaned:
        cleaned = "Unknown Player"

    if steamid:
        suffix = f" / {steamid}"
    else:
        suffix = ""

    max_name_length = 100 - len(suffix)

    if max_name_length > 0:
        cleaned = cleaned[:max_name_length].rstrip()

    thread_name = f"{cleaned}{suffix}"

    # Final safety check.
    thread_name = thread_name[:100].strip()

    if not thread_name:
        if ban_id:
            return f"Ban / {str(ban_id)[:94]}"

        return "Ban"

    return thread_name


def truncate_discord_text(
    value,
    max_length,
    fallback="Unavailable",
):
    if value is None:
        return fallback

    value = str(value)

    if not value:
        return fallback

    if len(value) <= max_length:
        return value

    return value[: max_length - 3] + "..."


def _normalize_note(note):
    if note is None:
        return None

    note_text = html.unescape(re.sub(r"<[^>]*>", "", str(note))).strip()

    return note if note_text else None


def build_ban_message(ban):
    reason = ban.get("reason")
    note = _normalize_note(ban.get("note"))
    issued_by = ban.get("issued_by") or "Unknown"

    if reason:
        duration = _format_ban_duration(
            ban.get("expires"),
            ban.get("timestamp"),
        )

        time_left = "Permanent"

        expires = _parse_ban_expiration(ban.get("expires"))

        if expires:
            now = datetime.now(expires.tzinfo)
            remaining_seconds = int((expires - now).total_seconds())

            if remaining_seconds > 0:
                days, remainder = divmod(remaining_seconds, 86400)
                hours, remainder = divmod(remainder, 3600)
                minutes, _ = divmod(remainder, 60)

                time_parts = []

                if days:
                    time_parts.append(f"{days}d")

                if hours:
                    time_parts.append(f"{hours}h")

                if minutes:
                    time_parts.append(f"{minutes}m")

                time_left = " ".join(time_parts) if time_parts else "<1m"
            else:
                time_left = "Expired"

        reason = reason.replace(
            "{{duration}}",
            duration,
        ).replace(
            "{{timeLeft}}",
            time_left,
        )

        message = f"{issued_by} added BattleMetrics Ban " f"({reason})"

    else:
        message = f"{issued_by} added BattleMetrics Ban"

    if note:
        message += f"\n\nNote: {note}"

    return truncate_discord_text(
        message,
        2000,
        "No ban message supplied by BattleMetrics.",
    )


def build_ban_embed(ban):
    player_name = ban.get("player_name") or "Unknown Player"
    player_id = ban.get("player_id")
    steamid = ban.get("steamid")
    eosid = ban.get("eosid")
    ban_id = ban.get("ban_id")

    embed = discord.Embed(
        title="Ban Information",
        color=8388736,
    )

    # Deliberately don't put the player name inside
    # Markdown links. Names containing ], (, ), etc.
    # can otherwise produce malformed Markdown.

    if player_id:
        player_value = (
            f"{truncate_discord_text(player_name, 900)}\n"
            f"https://www.battlemetrics.com/rcon/players/{player_id}"
        )
    else:
        player_value = truncate_discord_text(
            player_name,
            1024,
        )

    embed.add_field(
        name="Player / RCON Page",
        value=truncate_discord_text(
            player_value,
            1024,
        ),
        inline=True,
    )

    if steamid:
        steam_value = (
            f"```{steamid}```\n"
            f"https://steamcommunity.com/profiles/{steamid}"
        )
    else:
        steam_value = "Unavailable"

    embed.add_field(
        name="SteamID / Steam Profile",
        value=truncate_discord_text(
            steam_value,
            1024,
        ),
        inline=True,
    )

    embed.add_field(
        name="EOS ID",
        value=truncate_discord_text(
            f"```{eosid}```" if eosid else "Unavailable",
            1024,
        ),
        inline=True,
    )

    embed.add_field(
        name="Ban ID",
        value=truncate_discord_text(
            (
                f"[{ban_id}]"
                f"(https://www.battlemetrics.com/rcon/bans/edit/{ban_id})"
                if ban_id
                else "Unavailable"
            ),
            1024,
        ),
        inline=True,
    )

    embed.add_field(
        name="Created",
        value=truncate_discord_text(
            ban.get("timestamp"),
            1024,
        ),
        inline=True,
    )

    expires = ban.get("expires")

    embed.add_field(
        name="Expires",
        value=(
            truncate_discord_text(
                expires,
                1024,
            )
            if expires
            else "Permanent"
        ),
        inline=True,
    )

    if steamid:
        embed.add_field(
            name="CBL Profile",
            value=(f"https://communitybanlist.com/search/" f"{steamid}"),
            inline=True,
        )

    embed.set_footer(text=f"Ban Time: {ban.get('timestamp') or 'Unknown'}")

    return embed


def _parse_ban_expiration(value):
    """
    Convert a BattleMetrics expiration timestamp to a datetime
    that can be compared with another expiration timestamp.

    Returns None for permanent/unknown expirations.
    """

    if not value:
        return None

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    except (ValueError, TypeError):
        return None


def _format_ban_duration(expires, timestamp):
    """
    Calculate a human-readable ban duration from the
    BattleMetrics creation and expiration timestamps.

    Returns "Perm" for permanent bans and "Unknown" when
    the timestamps cannot be calculated.
    """

    if not expires:
        return "Perm"

    start = _parse_ban_expiration(timestamp)
    end = _parse_ban_expiration(expires)

    if not start or not end:
        return "Unknown"

    total_seconds = int((end - start).total_seconds())

    if total_seconds <= 0:
        return "Unknown"

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []

    if days:
        parts.append(f"{days}d")

    if hours:
        parts.append(f"{hours}h")

    if minutes:
        parts.append(f"{minutes}m")

    if not parts:
        return "<1m"

    return " ".join(parts)


def _build_discord_snapshot(ban):
    """
    Build a stable representation of the BattleMetrics data
    that is reflected in the Discord evidence thread.
    """

    return json.dumps(
        {
            "expires": ban.get("expires"),
            "reason": ban.get("reason"),
            "note": ban.get("note"),
            "player_name": ban.get("player_name"),
            "server_id": ban.get("server_id"),
            "user_id": ban.get("user_id"),
            "issued_by": ban.get("issued_by"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _ban_from_discord_snapshot(snapshot):
    """
    Convert a saved Discord snapshot back into a dictionary.

    Returns None if the snapshot is missing or invalid.
    """

    if not snapshot:
        return None

    try:
        data = json.loads(snapshot)

        if not isinstance(data, dict):
            return None

        return data

    except (json.JSONDecodeError, TypeError):
        return None


def _get_ban_changes(old_ban, new_ban):
    """
    Compare the previous database version of a ban against the
    newly fetched BattleMetrics version.

    Reaching the expiration date is intentionally NOT treated as
    a change. A Discord message is only generated when
    BattleMetrics changes the stored ban data.
    """

    if old_ban is None:
        return []

    changes = []

    old_expires = old_ban.get("expires")
    new_expires = new_ban.get("expires")

    if old_expires != new_expires:

        old_expiration = _parse_ban_expiration(old_expires)

        new_expiration = _parse_ban_expiration(new_expires)

        if old_expiration and new_expiration:

            if new_expiration > old_expiration:
                change_type = "extended"

            elif new_expiration < old_expiration:
                change_type = "reduced"

            else:
                change_type = "updated"

        elif not old_expiration and new_expiration:
            change_type = "expiration_added"

        elif old_expiration and not new_expiration:
            change_type = "permanent"

        else:
            change_type = "updated"

        changes.append(
            {
                "type": change_type,
                "old": (
                    _format_ban_duration(old_expires, new_ban.get("timestamp"))
                    if old_expires
                    else "Permanent"
                ),
                "new": (
                    _format_ban_duration(new_expires, new_ban.get("timestamp"))
                    if new_expires
                    else "Permanent"
                ),
            }
        )

    if old_ban.get("reason") != new_ban.get("reason"):
        changes.append(
            {
                "type": "reason",
                "old": old_ban.get("reason") or "None",
                "new": new_ban.get("reason") or "None",
            }
        )

    old_note = _normalize_note(old_ban.get("note"))
    new_note = _normalize_note(new_ban.get("note"))

    if old_note != new_note:
        changes.append(
            {
                "type": "note",
                "old": old_note or "None",
                "new": new_note or "None",
            }
        )

    if old_ban.get("server_id") != new_ban.get("server_id"):
        changes.append(
            {
                "type": "server",
                "old": (old_ban.get("server_id") or "None"),
                "new": (new_ban.get("server_id") or "None"),
            }
        )

    if old_ban.get("user_id") != new_ban.get("user_id"):
        changes.append(
            {
                "type": "moderator",
                "old": (old_ban.get("user_id") or "Unknown"),
                "new": (new_ban.get("user_id") or "Unknown"),
            }
        )

    if old_ban.get("issued_by") != new_ban.get("issued_by"):
        changes.append(
            {
                "type": "issuer",
                "old": (old_ban.get("issued_by") or "Unknown"),
                "new": (new_ban.get("issued_by") or "Unknown"),
            }
        )

    return changes


def _format_ban_change(change):
    change_type = change["type"]

    if change_type == "extended":
        return (
            "**BattleMetrics Ban Extended**\n"
            f"Expiration: `{change['old']}` → "
            f"`{change['new']}`"
        )

    if change_type == "reduced":
        return (
            "**BattleMetrics Ban Reduced**\n"
            f"Expiration: `{change['old']}` → "
            f"`{change['new']}`"
        )

    if change_type == "expiration_added":
        return (
            "**BattleMetrics Ban Updated**\n"
            f"Expiration: `Permanent` → "
            f"`{change['new']}`"
        )

    if change_type == "permanent":
        return (
            "**BattleMetrics Ban Updated**\n"
            f"Expiration: `{change['old']}` → "
            f"`Permanent`"
        )

    if change_type == "reason":
        return (
            "**BattleMetrics Ban Updated**\n"
            f"Reason: `{change['old']}` → "
            f"`{change['new']}`"
        )

    if change_type == "note":
        return (
            "**BattleMetrics Ban Updated**\n"
            f"Note: `{change['old']}` → "
            f"`{change['new']}`"
        )

    if change_type == "player_name":
        return (
            "**BattleMetrics Ban Updated**\n"
            f"Player name: `{change['old']}` → "
            f"`{change['new']}`"
        )

    if change_type == "server":
        return (
            "**BattleMetrics Ban Updated**\n"
            f"Server: `{change['old']}` → "
            f"`{change['new']}`"
        )

    if change_type == "moderator":
        return (
            "**BattleMetrics Ban Updated**\n"
            f"Moderator: `{change['old']}` → "
            f"`{change['new']}`"
        )

    if change_type == "issuer":
        return (
            "**BattleMetrics Ban Updated**\n"
            f"Issued by: `{change['old']}` → "
            f"`{change['new']}`"
        )

    return "**BattleMetrics Ban Updated**\n" f"`{change['old']}` → `{change['new']}`"


async def post_ban_update(thread_id, changes):
    """
    Post BattleMetrics changes to the existing Discord ban
    evidence thread.

    Returns True on success, False on failure.
    """

    if not thread_id or not changes:
        return False

    try:
        thread_id = int(thread_id)
    except (ValueError, TypeError):
        logger.error(
            "Invalid Discord ban evidence thread ID: %s",
            thread_id,
        )
        return False

    thread = client.get_channel(thread_id)

    if thread is None:
        try:
            thread = await client.fetch_channel(thread_id)

        except Exception:
            logger.exception(
                "Could not find Discord ban evidence thread %s",
                thread_id,
            )

            return False

    try:
        messages = [_format_ban_change(change) for change in changes]

        await thread.send("\n\n".join(messages))

        logger.info(
            "Posted %s BattleMetrics change(s) to Discord " "thread %s",
            len(changes),
            thread_id,
        )

        return True

    except discord.HTTPException as e:
        logger.error(
            "Failed to post BattleMetrics update to Discord " "thread %s. HTTP %s: %s",
            thread_id,
            e.status,
            e,
        )

        return False

    except Exception:
        logger.exception(
            "Unexpected error posting BattleMetrics update " "to Discord thread %s",
            thread_id,
        )

        return False


async def post_ban_deleted(thread_id):
    """
    Post a deletion notice to the existing Discord ban evidence
    thread.

    Returns True on success, False on failure.
    """

    if not thread_id:
        return False

    try:
        thread_id = int(thread_id)
    except (ValueError, TypeError):
        logger.error(
            "Invalid Discord ban evidence thread ID for deletion: %s",
            thread_id,
        )
        return False

    thread = client.get_channel(thread_id)

    if thread is None:
        try:
            thread = await client.fetch_channel(thread_id)

        except Exception:
            logger.exception(
                "Could not find Discord ban evidence thread %s " "for deletion notice",
                thread_id,
            )

            return False

    try:
        await thread.send(
            "**BattleMetrics Ban Deleted**\n\n"
            "This ban was removed from BattleMetrics."
        )

        logger.info(
            "Posted BattleMetrics ban deletion notice to " "Discord thread %s",
            thread_id,
        )

        return True

    except discord.HTTPException as e:
        logger.error(
            "Failed to post BattleMetrics deletion notice "
            "to Discord thread %s. HTTP %s: %s",
            thread_id,
            e.status,
            e,
        )

        return False

    except Exception:
        logger.exception(
            "Unexpected error posting BattleMetrics deletion "
            "notice to Discord thread %s",
            thread_id,
        )

        return False


async def create_ban_forum_post(ban):
    """
    Create the Discord forum thread for a ban.

    Returns:
        discord.Thread on success
        None on failure

    Failure does NOT remove the ban from the database.
    """

    channel = client.get_channel(FORUM_CHANNEL_ID)

    if channel is None:
        logger.error(
            "BattleMetrics ban forum channel %s could not be found",
            FORUM_CHANNEL_ID,
        )

        return None

    if not isinstance(
        channel,
        discord.ForumChannel,
    ):
        logger.error(
            "FORUM_CHANNEL_ID %s is not a Discord forum channel",
            FORUM_CHANNEL_ID,
        )

        return None

    thread_name = sanitize_forum_thread_name(
        ban.get("player_name"),
        ban.get("steamid"),
        ban.get("ban_id"),
    )

    content = build_ban_message(ban)

    try:
        thread_with_message = await channel.create_thread(
            name=thread_name,
            content=content,
            embed=build_ban_embed(ban),
        )

        thread = thread_with_message.thread

        logger.info(
            "Created BattleMetrics ban forum thread " "%s for ban %s (%s)",
            thread.id,
            ban.get("ban_id"),
            thread_name,
        )

        return thread

    except discord.HTTPException as e:
        logger.error(
            "Failed to create forum thread for BM ban %s. " "HTTP %s: %s",
            ban.get("ban_id"),
            e.status,
            e,
        )

        return None

    except Exception:
        logger.exception(
            "Unexpected error creating forum thread for BM ban %s",
            ban.get("ban_id"),
        )

        return None


async def sync_battlemetrics_bans():
    """
    Perform one complete BattleMetrics ban synchronization.

    Returns:
        True  = complete BattleMetrics synchronization succeeded
        False = synchronization failed
    """

    payload = await fetch_battlemetrics_bans()

    if payload is None:
        logger.warning(
            "BattleMetrics ban sync failed; "
            "existing database state will not be modified."
        )
        return False

    raw_bans = payload.get("data", [])
    included = payload.get("included", [])

    included_lookup = _build_included_lookup(included)

    # Keep the database state from BEFORE this sync so we can
    # determine what changed and which bans disappeared.
    previous_bans = {ban["ban_id"]: ban for ban in get_all_bans()}

    initialized = get_sync_state("battlemetrics_bans_initialized") == "true"

    # ---------------------------------------------------------
    # Normalize the complete BattleMetrics response first.
    #
    # No database writes happen during this loop.
    # ---------------------------------------------------------

    normalized_bans = []

    for raw_ban in raw_bans:

        ban = normalize_ban(
            raw_ban,
            included_lookup,
        )

        if ban is None:
            logger.warning("Skipping BattleMetrics ban with no ID")
            continue

        normalized_bans.append(ban)

    # ---------------------------------------------------------
    # FIRST EVER SYNC
    #
    # Import all existing/historical bans but do NOT create
    # Discord forum threads for them.
    # ---------------------------------------------------------

    if not initialized:

        logger.info(
            "Performing BattleMetrics baseline import of %s bans",
            len(normalized_bans),
        )

        bulk_import_battlemetrics_bans(normalized_bans)

        mark_all_bans_not_seen()

        set_sync_state(
            "battlemetrics_bans_initialized",
            "true",
        )

        logger.info(
            "BattleMetrics ban database baseline initialization "
            "complete. Imported %s historical bans without "
            "creating Discord forum threads.",
            len(normalized_bans),
        )

        return True

    logger.info(
        "Processing %s BattleMetrics bans " "(initial_sync=False)",
        len(normalized_bans),
    )

    # ---------------------------------------------------------
    # Prepare the set of IDs returned by BattleMetrics.
    # ---------------------------------------------------------

    seen_ban_ids = {ban["ban_id"] for ban in normalized_bans}

    # ---------------------------------------------------------
    # Legacy snapshot migration.
    #
    # Existing Discord threads created before snapshot tracking
    # was added need their OLD database state saved before the
    # bulk update overwrites the BattleMetrics fields.
    # ---------------------------------------------------------

    for ban in normalized_bans:

        previous = previous_bans.get(ban["ban_id"])

        if (
            previous is not None
            and previous.get("discord_thread_id")
            and not previous.get("discord_last_posted_snapshot")
        ):
            snapshot = _build_discord_snapshot(previous)

            mark_ban_discord_snapshot_posted(
                ban["ban_id"],
                snapshot,
            )

            previous["discord_last_posted_snapshot"] = snapshot

    # ---------------------------------------------------------
    # BULK DATABASE UPDATE
    #
    # This replaces thousands of individual SQLite
    # connections/commits with ONE transaction.
    #
    # Discord state is preserved by the database function.
    # ---------------------------------------------------------

    mark_all_bans_not_seen()

    bulk_upsert_battlemetrics_bans(normalized_bans)

    # ---------------------------------------------------------
    # Process Discord changes AFTER the database update.
    #
    # previous_bans still contains the OLD state, so all
    # comparisons remain old -> new.
    # ---------------------------------------------------------

    for ban in normalized_bans:

        previous = previous_bans.get(ban["ban_id"])

        # -----------------------------------------------------
        # EXISTING BAN WITH CHANGES
        #
        # Send changes to the existing evidence thread.
        #
        # Simply reaching the expiration time does NOT appear
        # here because _get_ban_changes() only compares the
        # stored BattleMetrics data.
        # -----------------------------------------------------

        if previous is not None and previous.get("discord_thread_id"):

            snapshot = previous.get("discord_last_posted_snapshot")

            discord_baseline = _ban_from_discord_snapshot(snapshot)

            if discord_baseline is not None:

                changes = _get_ban_changes(
                    discord_baseline,
                    ban,
                )

                if changes:

                    success = await post_ban_update(
                        previous["discord_thread_id"],
                        changes,
                    )

                    if success:

                        mark_ban_discord_snapshot_posted(
                            ban["ban_id"],
                            _build_discord_snapshot(ban),
                        )

        # -----------------------------------------------------
        # NEW BAN / PREVIOUSLY FAILED DISCORD POST
        #
        # If there is no Discord post yet, create the evidence
        # thread.
        # -----------------------------------------------------

        if previous is None or not previous["discord_posted"]:

            thread = await create_ban_forum_post(ban)

            if thread is not None:

                mark_ban_discord_posted(
                    ban["ban_id"],
                    thread.id,
                )

                mark_ban_discord_snapshot_posted(
                    ban["ban_id"],
                    _build_discord_snapshot(ban),
                )

            else:

                logger.warning(
                    "Discord forum post failed for BM ban %s. "
                    "Ban remains stored and will be retried "
                    "on the next sync.",
                    ban["ban_id"],
                )

    # ---------------------------------------------------------
    # The API request AND all pagination completed successfully.
    #
    # Only NOW is it safe to mark records not returned by the
    # API as no longer present in BattleMetrics.
    # ---------------------------------------------------------

    # ---------------------------------------------------------
    # Detect bans that disappeared from BattleMetrics.
    #
    # A deletion notice is only sent once successfully.
    # If Discord fails, the flag remains 0 and the next sync
    # retries the notification.
    # ---------------------------------------------------------

    for ban_id, previous in previous_bans.items():

        if ban_id in seen_ban_ids:
            continue

        # Already absent during a previous sync.
        if not previous.get("bm_present"):
            continue

        # No Discord evidence thread exists.
        if not previous.get("discord_thread_id"):
            continue

        # Deletion notice already successfully posted.
        if previous.get("discord_deleted_posted"):
            continue

        success = await post_ban_deleted(previous["discord_thread_id"])

        if success:
            mark_ban_discord_deleted_posted(ban_id)

    return True


async def battlemetrics_ban_sync_task():
    logger.info(
        "BattleMetrics ban sync task running every %s seconds",
        BATTLEMETRICS_BAN_CHECK_INTERVAL,
    )

    # Give Discord a moment to finish startup.
    await asyncio.sleep(5)

    while True:

        try:

            successful = await sync_battlemetrics_bans()

            if successful:

                # This runs every interval, even when there
                # are no new bans. That means expiration is
                # automatically reflected in SquadJS.
                await push_active_bans_to_sftp()

        except asyncio.CancelledError:

            logger.info("BattleMetrics ban sync task cancelled")

            raise

        except Exception:

            logger.exception("Unhandled error in BattleMetrics ban sync task")

        await asyncio.sleep(BATTLEMETRICS_BAN_CHECK_INTERVAL)
