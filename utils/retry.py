"""HTTP session management and player-count fetching with retry logic."""

import asyncio

import aiohttp

from config import logger, BATTLEMETRICS_SERVER_ID
from state import state


async def fetch_player_count():
    await init_http_session()

    url = f"https://api.battlemetrics.com/servers/{BATTLEMETRICS_SERVER_ID}"
    logger.debug(f"Fetching player count from: {url}")

    try:
        async with state.http_session.get(url) as response:
            if response.status != 200:
                logger.warning(f"BattleMetrics API returned status {response.status}")
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
            async with state.http_session.get(url) as resp:
                if resp.status != 200:
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
