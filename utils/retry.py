"""HTTP session management and player-count fetching with retry logic."""

import asyncio

import aiohttp

from config import logger, BATTLEMETRICS_API_KEY, BATTLEMETRICS_SERVER_ID
from state import state


def _battlemetrics_headers() -> dict[str, str]:
    if not BATTLEMETRICS_API_KEY:
        return {}

    return {"Authorization": f"Bearer {BATTLEMETRICS_API_KEY}"}


async def fetch_player_count():
    await init_http_session()

    url = f"https://api.battlemetrics.com/servers/{BATTLEMETRICS_SERVER_ID}"
    logger.debug(f"Fetching player count from: {url}")

    try:
        async with state.http_session.get(
            url, headers=_battlemetrics_headers()
        ) as response:
            if response.status != 200:
                body = await response.text()
                logger.warning(
                    "BattleMetrics returned %s\nURL: %s\nBody: %s",
                    response.status,
                    url,
                    body[:1000],
                )
                return None

            data = await response.json()
            players = data.get("data", {}).get("attributes", {}).get("players", 0)

            logger.debug(f"Player count retrieved: {players}")
            return players

    except asyncio.TimeoutError:
        logger.warning("fetch_player_count | BattleMetrics request timed out")
        await close_http_session()
        return None

    except aiohttp.ClientError as e:
        logger.error(f"BattleMetrics API connection error: {e}")
        await close_http_session()
        return None


async def robust_fetch_player_count(server_id: str, retries=2):
    await init_http_session()

    url = f"https://api.battlemetrics.com/servers/{server_id}"
    logger.debug(f"Fetching player count for server {server_id} (retries={retries})")

    for attempt in range(1, retries + 1):
        try:
            async with state.http_session.get(
                url, headers=_battlemetrics_headers()
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.debug(
                        "BattleMetrics returned %s (attempt %s/%s)\nURL: %s\nBody: %s",
                        resp.status,
                        attempt,
                        retries,
                        url,
                        body[:1000],
                    )
                    continue

                data = await resp.json()
                attrs = data["data"]["attributes"]

                players = attrs["players"]
                max_players = attrs.get("maxPlayers", 0)
                queue = attrs.get("details", {}).get("squad_publicQueue", 0)

                return players, max_players, queue

        except asyncio.TimeoutError:
            logger.debug(
                f"Timeout fetching {server_id} " f"(attempt {attempt}/{retries})"
            )
            if attempt == retries:
                await close_http_session()

        except aiohttp.ClientError as e:
            logger.debug(f"Connection error for {server_id}: {e}")
            if attempt == retries:
                await close_http_session()

        await asyncio.sleep(0.3)

    logger.warning(
        f"Failed to fetch player count for server {server_id} "
        f"after {retries} attempts"
    )
    return None, None, None


async def init_http_session():

    if state.http_session is None or state.http_session.closed:
        timeout = aiohttp.ClientTimeout(
            total=10,
            connect=5,
            sock_read=5,
        )

        state.http_session = aiohttp.ClientSession(timeout=timeout)
        logger.info("HTTP session initialized")


async def close_http_session():

    if state.http_session and not state.http_session.closed:
        await state.http_session.close()
        logger.info("HTTP session closed")


async def fetch_battlemetrics_bans():
    from config import BATTLEMETRICS_BAN_LIST_ID

    if not BATTLEMETRICS_BAN_LIST_ID:
        logger.error("BATTLEMETRICS_BAN_LIST_ID is not configured")
        return None

    await init_http_session()

    url = "https://api.battlemetrics.com/bans"

    params = {
        "filter[banList]": BATTLEMETRICS_BAN_LIST_ID,
        "sort": "-timestamp",
        "page[size]": "100",
        "include": "player,server,user",
    }

    all_bans = []
    all_included = []

    page_number = 0

    while url:
        page_number += 1

        try:
            async with state.http_session.get(
                url,
                params=params,
                headers=_battlemetrics_headers(),
            ) as response:

                if response.status != 200:
                    body = await response.text()

                    logger.error(
                        "BattleMetrics bans request failed: %s\n"
                        "URL: %s\n"
                        "Body: %s",
                        response.status,
                        str(response.url),
                        body[:2000],
                    )

                    return None

                payload = await response.json()

        except asyncio.TimeoutError:

            logger.error(
                "BattleMetrics bans request timed out on page %s",
                page_number,
            )

            return None

        except aiohttp.ClientError as e:

            logger.error(
                "BattleMetrics bans connection error on page %s: %s",
                page_number,
                e,
            )

            return None

        page_data = payload.get("data", [])
        included = payload.get("included", [])

        if not isinstance(page_data, list):

            logger.error(
                "BattleMetrics returned invalid ban data on page %s",
                page_number,
            )

            return None

        all_bans.extend(page_data)

        if isinstance(included, list):
            all_included.extend(included)

        logger.debug(
            "BattleMetrics ban sync page %s: %s bans",
            page_number,
            len(page_data),
        )

        url = payload.get("links", {}).get("next")

        # `next` already contains the pagination parameters.
        params = None

    logger.info(
        "BattleMetrics ban fetch completed: %s bans, %s included records",
        len(all_bans),
        len(all_included),
    )

    return {
        "data": all_bans,
        "included": all_included,
    }
