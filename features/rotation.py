"""Map rotation, Google Sheet parsing, and squad-browser join links."""

import json
import asyncio
from datetime import datetime, timedelta

import pytz
import aiohttp
import aiomcrcon
import discord
from google.oauth2 import service_account
from googleapiclient.discovery import build

from config import (
    logger,
    SCOPES,
    ROTATION_EMOJI,
    JOIN_REDIRECT_URL_TEMPLATE,
    WEEKLY_SHEET_IDS,
    CYCLE_START_DATE,
    GOOGLE_CREDENTIALS_FILE,
    MESSAGE_CHANNEL_ID,
    SFTP_HOST,
    SFTP_PORT,
    SFTP_USER,
    SFTP_PASSWORD,
    SFTP_REMOTE_PATH,
    SQUADJS_SFTP_HOST,
    SQUADJS_SFTP_PORT,
    SQUADJS_SFTP_USER,
    SQUADJS_SFTP_PASSWORD,
    SQUADJS_SFTP_FOG_PATH,
    FOG_JSON_PATH,
    JOIN_LINK_API_KEY,
    JOIN_LINK_API_URL,
    JOIN_LINK_REFRESH_INTERVAL,
    JOIN_LINK_RCON_HOST,
    JOIN_LINK_RCON_PORT,
    JOIN_LINK_RCON_PASSWORD,
    RESTART_HOUR,
    RESTART_MINUTE,
)
from state import state, save_rotation_state, load_rotation_state
from bot import client
from utils.sftp import sftp_write_content
from utils.retry import close_http_session
from utils.discord_helpers import post_command_response


def get_row_values_from_google_sheet(
    sheet_id, sheet_name, credentials_file, column_range="A1:G15"
):
    try:
        creds = service_account.Credentials.from_service_account_file(
            credentials_file, scopes=SCOPES
        )
        service = build("sheets", "v4", credentials=creds)
        range_name = f"{sheet_name}!{column_range}"
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=sheet_id, range=range_name).execute()
        values = result.get("values", [])
        return values[1:] if values else []
    except Exception as e:
        logger.error(f"Error reading Google Sheet '{sheet_name}': {e}", exc_info=True)
        return []


ignore_list = []  # add as many map base names as needed


def process_rotation_sheet(rows, ignore_prefixes=ignore_list):
    logger.info(f"Processing {len(rows)} rows from rotation sheet")
    processed_rows = []
    fog_off_rows = []
    fog_off_flags = []

    if ignore_prefixes is None:
        ignore_prefixes = []
        logger.debug("No ignore prefixes configured")

    skipped_count = 0
    fog_off_count = 0

    for i, row in enumerate(rows):
        # Skip empty rows safely
        if not row or len(row) == 0:
            logger.debug(f"Row {i}: Skipped (empty)")
            skipped_count += 1
            continue

        col_a = row[0].strip() if row[0] else ""

        # Skip row if column A contains any ignored base name
        if any(substr.lower() in col_a.lower() for substr in ignore_prefixes):
            logger.debug(f"Row {i}: Skipped (ignored prefix: {col_a})")
            skipped_count += 1
            continue

        # Ensure at least 7 columns
        while len(row) < 7:
            row.append("")

        formatted_row = row[:6]

        if formatted_row[3]:
            formatted_row[3] = f"+{formatted_row[3]}"
        if formatted_row[5]:
            formatted_row[5] = f"+{formatted_row[5]}"
        if formatted_row[2]:
            formatted_row[2] = f" {formatted_row[2]}"
        if formatted_row[4]:
            formatted_row[4] = f" {formatted_row[4]}"

        # Build final formatted string
        map_line = (
            f"{formatted_row[0]}{formatted_row[1]}{formatted_row[2]}"
            f"{formatted_row[3]}{formatted_row[4]}{formatted_row[5]}"
        ).strip()
        processed_rows.append(map_line)
        logger.debug(f"Row {i}: Added map {map_line}")

        # Handle fog-of-war column
        g_val = row[6].strip().lower() if len(row) > 6 else ""
        is_fog_off = g_val == "true"
        fog_off_flags.append(is_fog_off)

        if is_fog_off:
            fog_id = f"{row[0]}{row[1]}"
            fog_off_rows.append(fog_id)
            fog_off_count += 1
            logger.debug(f"Row {i}: Fog-off enabled for {fog_id}")

    logger.info(
        f"Processing complete: {len(processed_rows)} maps, {fog_off_count} fog-off, {skipped_count} skipped"
    )
    return processed_rows, fog_off_rows, fog_off_flags


# --- Main Rotation Update Function ---


async def send_rotation_update(publish: bool = True):

    london_tz = pytz.timezone("Europe/London")
    now = datetime.now(london_tz)
    day_of_week = now.strftime("%A")

    days_since_start = (now - CYCLE_START_DATE).days
    cycle_day = days_since_start % 14
    week_of_cycle = (cycle_day // 7) + 1

    sheet_id = WEEKLY_SHEET_IDS.get(week_of_cycle)

    if not sheet_id:
        logger.error(f"No Google Sheet ID configured for week {week_of_cycle}.")
        return f"No sheet ID found for {day_of_week} week {week_of_cycle}"

    logger.info(
        f"Today is {now.strftime('%A %Y-%m-%d')} "
        f"(Cycle Day: {cycle_day + 1}) → Week {week_of_cycle}"
    )

    logger.info(f"Using Google Sheet ID for Week {week_of_cycle}: {sheet_id}")

    # ----------------------------
    # FETCH SERVER NAME ONCE
    # ----------------------------
    if state.cached_server_name is None:
        logger.info("Fetching server name from RCON")
        state.cached_server_name = await get_server_name_rcon()

        if not state.cached_server_name:
            logger.error("Failed to fetch server name from RCON")
            return "Failed to fetch server name"
    else:
        logger.info(f"Using cached server name: {state.cached_server_name}")

    sftp_rotation_ok = False
    sftp_fog_ok = False

    try:
        rotation_rows = get_row_values_from_google_sheet(
            sheet_id,
            day_of_week,
            GOOGLE_CREDENTIALS_FILE,
        )

        if not rotation_rows:
            logger.warning(f"No rotation data found for {day_of_week}")
            return "No rotation data found"

        processed_rows, fog_off_rows, fog_off_flags = process_rotation_sheet(
            rotation_rows
        )

        processed_rows_for_upload = processed_rows.copy()
        last_two = []

        for i in reversed(range(len(processed_rows_for_upload))):
            if processed_rows_for_upload[i].strip():
                last_two.insert(0, processed_rows_for_upload.pop(i))

                if len(last_two) == 2:
                    break

        processed_rows_for_upload = last_two + processed_rows_for_upload

        processed_rotation_data = "\n".join(processed_rows_for_upload)

        logger.info(f"Processed rotation data:\n{processed_rotation_data}")

        # ----------------------------
        # STEP 1: Rotation upload
        # ----------------------------
        logger.info("Starting SFTP upload: rotation data → main host")

        try:
            sftp_rotation_ok = await sftp_write_content(
                SFTP_HOST,
                SFTP_PORT,
                SFTP_USER,
                SFTP_PASSWORD,
                SFTP_REMOTE_PATH,
                processed_rotation_data,
            )

            if sftp_rotation_ok:
                logger.info("✅ SFTP upload successful: main rotation file")
            else:
                logger.error("❌ SFTP upload failed: main rotation file")

            await asyncio.sleep(1)

        except Exception as sftp_err:
            logger.error(
                f"❌ SFTP upload crashed (main rotation): {sftp_err}",
                exc_info=True,
            )

        # ----------------------------
        # STEP 2: Fog upload
        # ----------------------------
        fog_json_data = json.dumps(
            {"layers": fog_off_rows},
            indent=2,
        )

        logger.info("Starting SFTP upload: fog_off_maps.json → SquadJS host")

        try:
            with open(FOG_JSON_PATH, "w") as f:
                f.write(fog_json_data)

            logger.info(
                f"fog_off_maps.json written locally " f"({len(fog_off_rows)} entries)"
            )

            sftp_fog_ok = await sftp_write_content(
                SQUADJS_SFTP_HOST,
                SQUADJS_SFTP_PORT,
                SQUADJS_SFTP_USER,
                SQUADJS_SFTP_PASSWORD,
                SQUADJS_SFTP_FOG_PATH,
                fog_json_data,
            )

            if sftp_fog_ok:
                logger.info("✅ SFTP upload successful: fog_off_maps.json")
            else:
                logger.error("❌ SFTP upload failed: fog_off_maps.json")

        except Exception as sftp_err:
            logger.error(
                f"❌ SFTP upload crashed (fog_off_maps.json): {sftp_err}",
                exc_info=True,
            )

        # ----------------------------
        # STATUS MESSAGE
        # ----------------------------
        if sftp_rotation_ok and sftp_fog_ok:
            status_msg = "✅ Rotation and fog status successfully uploaded."

        elif sftp_rotation_ok and not sftp_fog_ok:
            status_msg = "⚠️ Rotation uploaded but fog status failed to update."

        elif not sftp_rotation_ok and sftp_fog_ok:
            status_msg = "⚠️ Fog status uploaded but rotation failed to update."

        else:
            status_msg = "❌ Both rotation and fog status uploads failed."

        logger.info(status_msg)

        # ----------------------------
        # STEP 3: Discord embed
        # ----------------------------
        if not sftp_rotation_ok:
            logger.error("Rotation upload failed — skipping Discord embed send.")

            return "❌ Rotation upload failed — nothing posted to Discord."

        channel = client.get_channel(MESSAGE_CHANNEL_ID)

        if not channel:
            logger.error("Rotation update: Message channel not found")

            return "Message channel not found"

        custom_emoji = ROTATION_EMOJI

        embed_lines = []

        for line, fog_flag in zip(processed_rows, fog_off_flags):
            if not line.strip():
                continue

            display_line = line if len(line) <= 47 else line[:47] + "…"

            fog_status = "OFF" if fog_flag else ""

            embed_lines.append(f"{display_line:<49}{fog_status}")

        header_line = f"{'Layer':<49}Fog" if any(fog_off_flags) else "Layer"

        formatted_text = (
            f"```apache\n{header_line}\n\n" + "\n\n".join(embed_lines) + "\n```"
            if embed_lines
            else "No rotation data available."
        )

        embed = discord.Embed(
            title=(
                f"{custom_emoji}"
                f"{day_of_week} #{week_of_cycle} Rotation Updated"
                f"{custom_emoji}"
            ),
            description=formatted_text[:4000],
            color=9247733,
        )

        # ----------------------------
        # JOIN LINK BUTTON
        # ----------------------------
        logger.info("Fetching initial join link for rotation message")

        join_url = await fetch_join_link_squadbrowser(state.cached_server_name)
        join_link = format_join_link(join_url)

        join_link_found = False
        if join_link:
            logger.info(f"Initial join link fetched: {join_link}")
            join_link_found = True
        else:
            logger.warning("Initial join link unavailable - updater will retry")

        view = RotationJoinView(join_link)

        sent_message = await channel.send(
            embed=embed,
            view=view,
        )

        # ----------------------------
        # TRACK ACTIVE ROTATION MESSAGE
        # ----------------------------
        state.rotation_message_id = sent_message.id
        state.rotation_channel_id = channel.id

        logger.info(
            f"Tracking rotation message "
            f"(channel_id={state.rotation_channel_id}, "
            f"message_id={state.rotation_message_id}, "
            f"join_link_found={join_link_found})"
        )

        # Save rotation state for persistence across restarts
        save_rotation_state(
            {
                "message_id": state.rotation_message_id,
                "channel_id": state.rotation_channel_id,
                "join_link_found": join_link_found,
            }
        )
        logger.info("Rotation state saved for restart recovery")

        logger.info("Rotation update sent to Discord")

        # ----------------------------
        # publish
        # ----------------------------
        if publish:
            try:
                await sent_message.publish()

                logger.info("Rotation update published to announcement channel")

            except discord.Forbidden:
                logger.warning("Bot lacks permission to publish rotation message")

            except discord.HTTPException as e:
                logger.error(
                    f"Failed to publish rotation message: {e}",
                    exc_info=True,
                )

        else:
            logger.info("Skipping publish (testing mode)")

        return f"{status_msg} (Week #{week_of_cycle}, publish={publish})"

    except Exception as e:
        logger.error(
            f"Error in send_rotation_update: {e}",
            exc_info=True,
        )

        return f"Error sending rotation update: {e}"


class RotationJoinView(discord.ui.View):
    def __init__(self, join_link: str | None):
        super().__init__(timeout=None)

        # valid join link
        if join_link:
            self.add_item(
                discord.ui.Button(
                    label="Join Server",
                    url=join_link,
                    style=discord.ButtonStyle.link,
                )
            )

        # unavailable button
        else:
            unavailable_button = discord.ui.Button(
                label="Join Link Unavailable",
                style=discord.ButtonStyle.danger,
                disabled=True,
            )

            self.add_item(unavailable_button)


def format_join_link(join_url: str) -> str | None:
    if not join_url or join_url == "not found":
        return None

    lobby_id = join_url.split("/")[-1]

    if JOIN_REDIRECT_URL_TEMPLATE:
        return JOIN_REDIRECT_URL_TEMPLATE.format(lobby_id=lobby_id)
    return join_url


async def get_server_name_rcon() -> str | None:
    """Fetch server name from RCON connection (or return cached value)"""

    # Return cached server name if available
    if state.cached_server_name is not None:
        logger.info(f"Using cached server name: {state.cached_server_name}")
        return state.cached_server_name

    if not JOIN_LINK_RCON_HOST or not JOIN_LINK_RCON_PASSWORD:
        logger.warning("RCON credentials not set")
        return None
    try:
        logger.info(
            f"Connecting to RCON " f"{JOIN_LINK_RCON_HOST}:{JOIN_LINK_RCON_PORT}"
        )

        async with aiomcrcon.Client(
            host=JOIN_LINK_RCON_HOST,
            password=JOIN_LINK_RCON_PASSWORD,
            port=JOIN_LINK_RCON_PORT,
        ) as rcon_client:
            response = await rcon_client.command("ShowServerInfo")
            server_name = json.loads(response)["ServerName_s"]

        logger.info(f"Server name = {repr(server_name)}")
        return server_name

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return None


async def fetch_join_link_squadbrowser(server_name: str) -> str:
    """Fetch join link from SquadBrowser API using cached server name"""
    if not JOIN_LINK_API_KEY:
        logger.warning("JOIN_LINK_API_KEY is not set")
        return "not found"
    if not server_name:
        logger.warning("Server_name is None")
        return "not found"
    try:
        logger.info(f"Requesting join link for '{server_name}'")
        async with state.http_session.post(
            JOIN_LINK_API_URL,
            headers={
                "Content-Type": "application/json",
                "x-api-key": JOIN_LINK_API_KEY,
            },
            json={"serverName": server_name},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            logger.info(f"API response HTTP {resp.status}")
            data = await resp.json()
            logger.info(f"API response body = {data}")
            if resp.status in (401, 403):
                logger.warning(f"Invalid API key: " f"{data.get('message')}")
                return "not found"
            if resp.status == 404:
                logger.warning(f"Server not found: " f"{data.get('message')}")
                return "not found"
            if not resp.ok:
                logger.warning(f"API error {resp.status}: " f"{data.get('message')}")
                return "not found"
            join_url = data.get("joinUrl")
            if not join_url:
                logger.warning("JoinUrl missing in response")
                return "not found"
            logger.info(f"Join URL = {join_url}")
            return join_url
    except asyncio.TimeoutError:
        logger.warning("Request timed out")
        await close_http_session()
        return "not found"
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode error: {e}")
        await close_http_session()
        return "not found"
    except aiohttp.ClientError as e:
        logger.error(f"HTTP client error: {e}")
        await close_http_session()
        return "not found"
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        await close_http_session()
        return "not found"


async def rotation_join_link_updater():
    """
    Retries fetching and updating the join link if it wasn't found initially.
    Stops retrying once the link is found.
    Only resumes when send_rotation_update posts a new message ID.
    """

    logger.info(
        f"rotation_join_link_updater started "
        f"(retry interval={JOIN_LINK_REFRESH_INTERVAL}s)"
    )

    # Load persisted rotation state from previous session
    saved_state = load_rotation_state()
    if saved_state:
        state.rotation_message_id = saved_state.get("message_id")
        state.rotation_channel_id = saved_state.get("channel_id")
        join_link_found = saved_state.get("join_link_found", False)
        logger.info(
            f"Loaded rotation state from disk: "
            f"message_id={state.rotation_message_id}, "
            f"channel_id={state.rotation_channel_id}, "
            f"join_link_found={join_link_found}"
        )

    while True:
        try:
            # Reload state from disk each iteration to pick up changes from send_rotation_update
            saved_state = load_rotation_state()
            if not saved_state:
                logger.debug("No rotation state found, sleeping...")
                await asyncio.sleep(JOIN_LINK_REFRESH_INTERVAL)
                continue

            state.rotation_message_id = saved_state.get("message_id")
            state.rotation_channel_id = saved_state.get("channel_id")
            join_link_found = saved_state.get("join_link_found", False)

            # ----------------------------
            # If join link already found, idle
            # ----------------------------
            if join_link_found:
                logger.debug(
                    f"Join link already found for message {state.rotation_message_id}. Idling..."
                )
                await asyncio.sleep(JOIN_LINK_REFRESH_INTERVAL)
                continue

            # ----------------------------
            # No join link yet - attempt to fetch it
            # ----------------------------
            logger.info(
                f"Attempting to find join link for message {state.rotation_message_id}"
            )

            # Fetch channel and message
            channel = client.get_channel(state.rotation_channel_id)
            if not channel:
                logger.warning("Tracked channel not found")
                await asyncio.sleep(JOIN_LINK_REFRESH_INTERVAL)
                continue

            try:
                message = await channel.fetch_message(state.rotation_message_id)
            except discord.NotFound:
                logger.warning("Tracked message no longer exists")
                save_rotation_state({})  # Clear saved state
                await asyncio.sleep(JOIN_LINK_REFRESH_INTERVAL)
                continue

            # Fetch join link
            join_url = await fetch_join_link_squadbrowser(state.cached_server_name)
            join_link = format_join_link(join_url)

            if not join_link:
                logger.warning(
                    f"Join link unavailable, will retry in {JOIN_LINK_REFRESH_INTERVAL}s"
                )
                await asyncio.sleep(JOIN_LINK_REFRESH_INTERVAL)
                continue

            logger.info(f"Join link found: {join_link}")

            # Update message with new view
            new_view = RotationJoinView(join_link)
            await message.edit(view=new_view)
            logger.info("Discord message updated with join link")

            # Mark as found and save state
            join_link_found = True
            save_rotation_state(
                {
                    "message_id": state.rotation_message_id,
                    "channel_id": state.rotation_channel_id,
                    "join_link_found": True,
                }
            )
            logger.info("Join link found and saved. Updater will now idle.")

        except discord.HTTPException as e:
            logger.error(f"Discord HTTP error: {e}", exc_info=True)

        except Exception as e:
            logger.exception(f"Loop error: {e}")

        await asyncio.sleep(JOIN_LINK_REFRESH_INTERVAL)


async def schedule_daily_rotation_update():
    """Schedule a task to send rotation update every day at 09:55 London time"""
    london_tz = pytz.timezone("Europe/London")
    while True:
        now = datetime.now(london_tz)
        target = now.replace(
            hour=RESTART_HOUR, minute=RESTART_MINUTE, second=0, microsecond=0
        )
        if now > target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        hours = int(wait_seconds // 3600)
        minutes = int((wait_seconds % 3600) // 60)
        logger.info(
            f"Waiting {hours}h {minutes}m until next rotation update at {RESTART_HOUR}:{RESTART_MINUTE:02d} London time"
        )
        await asyncio.sleep(wait_seconds)
        result = await send_rotation_update()
        await post_command_response(result)
