"""BattleMetrics ban synchronization and Discord forum integration."""

import asyncio
import re
import unicodedata

import discord

from bot import client

from config import (
    logger,
    FORUM_CHANNEL_ID,
    BATTLEMETRICS_BAN_CHECK_INTERVAL,
    BATTLEMETRICS_BAN_LIST_ID,
)

from database import (
    get_ban_by_id,
    get_sync_state,
    set_sync_state,
    upsert_ban,
    mark_all_bans_not_seen,
    mark_ban_discord_posted,
    mark_ban_discord_skipped,
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
        player_object = included_lookup.get(("players", str(player_id)))

    player_name = None
    steamid = None
    eosid = None

    if player_object:
        player_attributes = player_object.get(
            "attributes",
            {},
        )

        player_name = player_attributes.get("name")

        steamid, eosid = _extract_player_identifiers(player_object)

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

            if not steamid and identifier_type in ("steamid", "steam"):
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
        "timestamp": attributes.get("timestamp"),
        "expires": attributes.get("expires"),
        "reason": attributes.get("reason"),
        "note": attributes.get("note"),
        "org_wide": (1 if attributes.get("orgWide") else 0),
        "auto_add_enabled": (1 if attributes.get("autoAddEnabled") else 0),
        "native_enabled": (
            1
            if attributes.get("nativeEnabled")
            else 0 if attributes.get("nativeEnabled") is not None else None
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


def build_ban_message(ban):
    reason = ban.get("reason")
    note = ban.get("note")

    parts = []

    if reason:
        parts.append(f"Reason: {reason}")

    if note:
        parts.append(f"Note: {note}")

    if not parts:
        return "No ban message supplied by BattleMetrics."

    return truncate_discord_text(
        "\n\n".join(parts),
        2000,
        "No ban message supplied by BattleMetrics.",
    )


def build_ban_embed(ban):
    player_name = ban.get("player_name") or "Unknown Player"
    player_id = ban.get("player_id")
    steamid = ban.get("steamid")
    eosid = ban.get("eosid")

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
        steam_value = f"{steamid}\n" f"https://steamcommunity.com/profiles/{steamid}"
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
            eosid,
            1024,
        ),
        inline=True,
    )

    embed.add_field(
        name="Ban ID",
        value=truncate_discord_text(
            ban.get("ban_id"),
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

    embed.add_field(
        name="Ban Message",
        value=truncate_discord_text(
            build_ban_message(ban),
            1024,
        ),
        inline=False,
    )

    if steamid:
        embed.add_field(
            name="CBL Profile",
            value=(f"https://communitybanlist.com/search/" f"{steamid}"),
            inline=True,
        )

    embed.set_footer(text=f"BattleMetrics Ban ID: {ban.get('ban_id')}")

    return embed


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

    from utils.retry import fetch_battlemetrics_bans

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

    initialized = get_sync_state("battlemetrics_bans_initialized") == "true"

    logger.info(
        "Processing %s BattleMetrics bans " "(initial_sync=%s)",
        len(raw_bans),
        not initialized,
    )

    # The API request and all pagination completed successfully,
    # so it is now safe to mark everything as not currently present.
    #
    # Bans returned by this sync will be changed back to bm_present=1
    # by upsert_ban().
    mark_all_bans_not_seen()

    for raw_ban in raw_bans:

        ban = normalize_ban(
            raw_ban,
            included_lookup,
        )

        if ban is None:
            logger.warning("Skipping BattleMetrics ban with no ID")
            continue

        # ---------------------------------------------------------
        # Save/update the ban FIRST.
        #
        # This is important because a Discord failure must never
        # cause us to lose the BattleMetrics ban.
        # ---------------------------------------------------------

        was_inserted = upsert_ban(ban)

        # ---------------------------------------------------------
        # FIRST EVER SYNC
        #
        # Import all existing/historical bans but do NOT create
        # Discord forum threads for them.
        # ---------------------------------------------------------

        if not initialized:

            mark_ban_discord_skipped(ban["ban_id"])

            continue

        # ---------------------------------------------------------
        # SUBSEQUENT SYNCS
        #
        # Check the database after the upsert so we get the current
        # discord_posted state.
        #
        # If discord_posted == 0, this is either:
        #
        #   1. A brand-new ban
        #   2. A previous forum-post attempt failed
        #
        # In either case, try to create the forum post.
        # ---------------------------------------------------------

        current = get_ban_by_id(ban["ban_id"])

        if current is None:
            logger.error(
                "Ban %s was just upserted but could not "
                "be retrieved from the database.",
                ban["ban_id"],
            )
            continue

        if not current["discord_posted"]:

            thread = await create_ban_forum_post(ban)

            if thread is not None:

                mark_ban_discord_posted(
                    ban["ban_id"],
                    thread.id,
                )

            else:

                logger.warning(
                    "Discord forum post failed for BM ban %s. "
                    "Ban remains stored and will be retried "
                    "on the next sync.",
                    ban["ban_id"],
                )

    # -------------------------------------------------------------
    # The first COMPLETE sync is now finished.
    #
    # This flag is stored in the database so that even an empty
    # BattleMetrics ban list will not cause every future sync to
    # be treated as the initial sync.
    # -------------------------------------------------------------

    if not initialized:

        set_sync_state(
            "battlemetrics_bans_initialized",
            "true",
        )

        logger.info(
            "BattleMetrics ban database baseline initialization "
            "complete. Historical bans will not create Discord "
            "forum threads."
        )

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
