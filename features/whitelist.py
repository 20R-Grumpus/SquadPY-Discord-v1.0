"""Whitelister API access, cached group/list data, and audit logging."""

import asyncio
import json
from urllib.parse import urlsplit, urlunsplit

import aiohttp
import discord

from bot import client
from config import (
    WHITELIST_API_BASE_URL,
    WHITELIST_API_KEY,
    WHITELIST_LOG_CHANNEL_ID,
    WHITELIST_MAIN_CLAN_NAME,
    WHITELIST_REFRESH_INTERVAL_SECONDS,
    logger,
)
from state import get_state, state, update_state
from utils.retry import init_http_session

API_PATHS = {
    "clans": "/api/clans/getAllClans",
    "groups": "/api/gameGroups/read/getAllGroups",
    "lists": "/api/lists/read/getAll",
    "entries": "/api/whitelist/read/getAll",
    "add": "/api/whitelist/write/addPlayer",
    "remove": "/api/whitelist/write/removePlayer",
}
MAIN_LIST_TITLE = "Main"
_available_groups = []
_available_entries = []
_group_refresh_lock = asyncio.Lock()
_entry_refresh_lock = asyncio.Lock()


class WhitelistAPIError(Exception):
    """Raised when a Whitelister API request cannot be completed."""


def _validated_api_base_url():
    parts = urlsplit(WHITELIST_API_BASE_URL)
    if (
        parts.scheme != "https"
        or not parts.netloc
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise WhitelistAPIError("WHITELIST_API_BASE_URL must be a valid HTTPS URL.")
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path.rstrip("/"), "", "")
    )


async def _api_request(method, path, *, params=None, body=None):
    if not WHITELIST_API_KEY:
        raise WhitelistAPIError("WHITELIST_API_KEY is not configured.")

    url = f"{_validated_api_base_url()}{path}"
    query = {"apiKey": WHITELIST_API_KEY}
    if params:
        query.update(params)
    request_body = None
    if body is not None:
        request_body = {**body, "apiKey": WHITELIST_API_KEY}

    await init_http_session()
    try:
        async with state.http_session.request(
            method,
            url,
            params=query,
            json=request_body,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            try:
                payload = await response.json(content_type=None)
            except (aiohttp.ContentTypeError, json.JSONDecodeError):
                payload = await response.text()
            return response.status, payload
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise WhitelistAPIError(
            f"Whitelister API request failed ({type(exc).__name__})."
        ) from exc


def _response_rows(payload, endpoint):
    rows = payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        rows = payload["data"]
    if not isinstance(rows, list):
        raise WhitelistAPIError(f"Whitelister returned invalid {endpoint} data.")
    return rows


async def refresh_main_list_id():
    """Discover the API list named Main and persist its ID."""
    try:
        status, payload = await _api_request("GET", API_PATHS["lists"])
    except WhitelistAPIError as exc:
        logger.warning("Could not refresh Whitelister lists: %s", exc)
        return False

    if status != 200:
        logger.warning("Whitelister list refresh returned HTTP %s.", status)
        return False

    try:
        rows = _response_rows(payload, "list")
    except WhitelistAPIError as exc:
        logger.error("%s", exc)
        return False

    matching = [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("title"), str)
        and row["title"].casefold() == MAIN_LIST_TITLE.casefold()
        and isinstance(row.get("_id"), str)
        and row["_id"]
    ]
    if len(matching) != 1:
        logger.error(
            "Expected exactly one Whitelister list named %s; found %s.",
            MAIN_LIST_TITLE,
            len(matching),
        )
        return False

    list_id = matching[0]["_id"]
    stored_state = get_state("whitelist", {})
    if (
        not isinstance(stored_state, dict)
        or stored_state.get("main_list_id") != list_id
    ):
        try:
            update_state("whitelist", main_list_id=list_id)
        except OSError:
            logger.exception("Could not persist the Whitelister Main list ID.")
            return False
    logger.info("Whitelister Main list ID refreshed and persisted.")
    return True


async def get_main_list_id():
    stored_state = get_state("whitelist", {})
    saved_list_id = (
        stored_state.get("main_list_id")
        if isinstance(stored_state, dict)
        else None
    )
    if isinstance(saved_list_id, str) and saved_list_id:
        return saved_list_id
    if await refresh_main_list_id():
        stored_state = get_state("whitelist", {})
        saved_list_id = (
            stored_state.get("main_list_id")
            if isinstance(stored_state, dict)
            else None
        )
        if isinstance(saved_list_id, str) and saved_list_id:
            return saved_list_id
    raise WhitelistAPIError(
        "The Whitelister list named Main could not be found or initialized."
    )


async def refresh_main_clan_id():
    """Discover the configured main clan and persist its ID."""
    if not WHITELIST_MAIN_CLAN_NAME:
        logger.error("WHITELIST_MAIN_CLAN_NAME is not configured.")
        return False

    try:
        status, payload = await _api_request("GET", API_PATHS["clans"])
    except WhitelistAPIError as exc:
        logger.warning("Could not refresh Whitelister clans: %s", exc)
        return False

    if status != 200:
        logger.warning("Whitelister clan refresh returned HTTP %s.", status)
        return False

    try:
        rows = _response_rows(payload, "clan")
    except WhitelistAPIError as exc:
        logger.error("%s", exc)
        return False

    matching = [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("full_name"), str)
        and row["full_name"].casefold() == WHITELIST_MAIN_CLAN_NAME.casefold()
        and isinstance(row.get("_id"), str)
        and row["_id"]
    ]
    if len(matching) != 1:
        logger.error(
            "Expected exactly one Whitelister clan named %s; found %s.",
            WHITELIST_MAIN_CLAN_NAME,
            len(matching),
        )
        return False

    clan_id = matching[0]["_id"]
    stored_state = get_state("whitelist", {})
    if (
        not isinstance(stored_state, dict)
        or stored_state.get("main_clan_name") != WHITELIST_MAIN_CLAN_NAME
        or stored_state.get("main_clan_id") != clan_id
    ):
        try:
            update_state(
                "whitelist",
                main_clan_name=WHITELIST_MAIN_CLAN_NAME,
                main_clan_id=clan_id,
            )
        except OSError:
            logger.exception("Could not persist the Whitelister main clan ID.")
            return False
    logger.info(
        "Whitelister main clan ID refreshed for %s.",
        WHITELIST_MAIN_CLAN_NAME,
    )
    return True


async def get_main_clan_id():
    stored_state = get_state("whitelist", {})
    saved_clan_id = (
        stored_state.get("main_clan_id")
        if isinstance(stored_state, dict)
        and stored_state.get("main_clan_name") == WHITELIST_MAIN_CLAN_NAME
        else None
    )
    if isinstance(saved_clan_id, str) and saved_clan_id:
        return saved_clan_id
    if await refresh_main_clan_id():
        stored_state = get_state("whitelist", {})
        saved_clan_id = (
            stored_state.get("main_clan_id")
            if isinstance(stored_state, dict)
            and stored_state.get("main_clan_name") == WHITELIST_MAIN_CLAN_NAME
            else None
        )
        if isinstance(saved_clan_id, str) and saved_clan_id:
            return saved_clan_id
    raise WhitelistAPIError(
        "The configured Whitelister main clan could not be found or initialized."
    )


async def refresh_available_groups():
    """Refresh groups, excluding any group whose permissions include ban."""
    global _available_groups

    async with _group_refresh_lock:
        try:
            status, payload = await _api_request("GET", API_PATHS["groups"])
        except WhitelistAPIError as exc:
            logger.warning("Could not refresh Whitelister game groups: %s", exc)
            return False

        if status != 200:
            logger.warning("Whitelister group refresh returned HTTP %s.", status)
            return False

        try:
            rows = _response_rows(payload, "game group")
        except WhitelistAPIError as exc:
            logger.error("%s", exc)
            return False

        groups = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            group_id = row.get("_id")
            group_name = row.get("group_name")
            permissions = row.get("group_permissions")
            if (
                not isinstance(group_id, str)
                or not group_id
                or not isinstance(group_name, str)
                or not group_name
                or not isinstance(permissions, list)
            ):
                continue
            normalized_permissions = {
                permission.casefold()
                for permission in permissions
                if isinstance(permission, str)
            }
            if normalized_permissions != {"reserve"}:
                continue
            groups.append({"id": group_id, "name": group_name})

        _available_groups = groups
        logger.info(
            "Refreshed %s eligible Whitelister game group(s).", len(groups)
        )
        return True


async def refresh_available_entries():
    """Refresh entries from the persisted Main list for identifier lookup."""
    global _available_entries

    async with _entry_refresh_lock:
        try:
            list_id = await get_main_list_id()
            status, payload = await _api_request(
                "GET",
                API_PATHS["entries"],
                params={"sel_clan_id": "", "sel_list_id": list_id},
            )
        except WhitelistAPIError as exc:
            logger.warning("Could not refresh Main whitelist entries: %s", exc)
            return False

        if status != 200:
            logger.warning(
                "Main whitelist entry refresh returned HTTP %s.", status
            )
            return False

        try:
            rows = _response_rows(payload, "whitelist entry")
        except WhitelistAPIError as exc:
            logger.error("%s", exc)
            return False

        _available_entries = [
            row
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("_id"), str)
            and row["_id"]
            and row.get("id_list") == list_id
        ]
        logger.info(
            "Refreshed %s Main whitelist entry/entries.", len(_available_entries)
        )
        return True


def get_available_groups():
    return list(_available_groups)


def get_available_entries():
    return list(_available_entries)


async def refresh_whitelist_data():
    await refresh_main_list_id()
    await refresh_main_clan_id()
    await asyncio.gather(
        refresh_available_groups(),
        refresh_available_entries(),
    )


async def whitelist_cache_task():
    while True:
        await refresh_whitelist_data()
        await asyncio.sleep(WHITELIST_REFRESH_INTERVAL_SECONDS)


async def add_whitelist_entry(
    *,
    username,
    identifier,
    identifier_type,
    group_id,
    duration_hours,
    discord_username,
    clan_id,
    list_id,
):
    body = {
        "username": username,
        "steamid64": identifier if identifier_type == "steamid64" else "",
        "eosID": identifier if identifier_type == "eosID" else "",
        "discordUsername": discord_username,
        "sel_clan_id": clan_id,
        "group": group_id,
        "durationHours": duration_hours,
        "sel_list_id": list_id,
    }
    return await _api_request("POST", API_PATHS["add"], body=body)


async def remove_whitelist_entry(entry_id):
    return await _api_request(
        "POST", API_PATHS["remove"], body={"_id": entry_id}
    )


async def send_whitelist_audit(message):
    if not WHITELIST_LOG_CHANNEL_ID:
        logger.info("Whitelist audit channel is disabled; event: %s", message)
        return

    channel = client.get_channel(WHITELIST_LOG_CHANNEL_ID)
    try:
        if channel is None:
            channel = await client.fetch_channel(WHITELIST_LOG_CHANNEL_ID)
        await channel.send(
            message,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.Forbidden, discord.HTTPException) as exc:
        logger.error(
            "Could not send whitelist audit log to channel %s: %s",
            WHITELIST_LOG_CHANNEL_ID,
            type(exc).__name__,
        )
