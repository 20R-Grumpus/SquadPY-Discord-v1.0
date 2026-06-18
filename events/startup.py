"""Startup logic: on_ready handler and background task bootstrap."""

import asyncio

from config import logger, LOG_CHANNEL_ID, GUILD_ID
from state import state
from bot import client, tree
from utils.discord_helpers import DiscordLogHandler
from utils.retry import init_http_session
from features.seeding import background_task
from features.rotation import schedule_daily_rotation_update, rotation_join_link_updater

BOT_USERNAME = "OOR v3.1"  # Set to None to disable


@client.event
async def on_ready():

    # ---- Initialize shared HTTP session ----
    await init_http_session()

    # ---- Discord log handler (START FIRST) ----
    log_handler = DiscordLogHandler(client, LOG_CHANNEL_ID)

    if not any(isinstance(h, DiscordLogHandler) for h in logger.handlers):
        logger.addHandler(log_handler)

    if not state.log_sender_task_ref or state.log_sender_task_ref.done():
        state.log_sender_task_ref = asyncio.create_task(log_handler.log_sender())
        await asyncio.sleep(0)
        logger.info("Discord log handler started")
    else:
        logger.info("Discord log handler already running — skipping")

    # ---- Normal startup logging ----
    logger.info(f"Logged in as {client.user}")

    # ---- 🔥 GLOBAL USERNAME CONTROL ----
    try:
        if BOT_USERNAME and client.user.name != BOT_USERNAME:
            await client.user.edit(username=BOT_USERNAME)
            logger.info(f"Updated bot username to: {BOT_USERNAME}")
    except Exception as e:
        logger.error(f"Failed to update bot username: {e}")

    # ---- Slash command sync (only once) ----
    if not getattr(client, "commands_synced", False):
        logger.info(f"Syncing commands to guild ID {GUILD_ID}")
        await tree.sync()
        client.commands_synced = True
        logger.info("Slash commands synced.")

    # ---- Background server monitor ----
    if not state.background_task_ref or state.background_task_ref.done():
        state.background_task_ref = asyncio.create_task(background_task())
        logger.info("Background task started")
    else:
        logger.info("Background task already running — skipping")

    # ---- Daily rotation scheduler ----
    if not state.rotation_task_ref or state.rotation_task_ref.done():
        state.rotation_task_ref = asyncio.create_task(schedule_daily_rotation_update())
        logger.info("Daily rotation scheduler started")
    else:
        logger.info("Daily rotation scheduler already running — skipping")

    # ---- Join link updater (retries failed initial joins) ----
    if not state.join_link_updater_task_ref or state.join_link_updater_task_ref.done():
        state.join_link_updater_task_ref = asyncio.create_task(
            rotation_join_link_updater()
        )
        logger.info("Join link updater started")
    else:
        logger.info("Join link updater already running — skipping")

    commands = await tree.fetch_commands()

    if commands:
        lines = []
        for cmd in sorted(commands, key=lambda c: c.name):
            cmd_type = getattr(cmd.type, "name", str(cmd.type))
            lines.append(f"• /{cmd.name:<16} | id={cmd.id} | type={cmd_type}")

        logger.info(
            "Synced application commands:\n%s",
            "\n".join(lines),
        )
    else:
        logger.info("No application commands are currently synced.")
