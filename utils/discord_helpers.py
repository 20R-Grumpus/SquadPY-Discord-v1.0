"""Discord-specific helpers: messaging, logging handler, role checks."""

import asyncio
import logging
from datetime import datetime

import pytz
import discord

from config import (
    logger,
    MESSAGE_CHANNEL_ID,
    TEST_CHANNEL_ID,
    COMMAND_CHANNEL_ID,
    SEEDING_MESSAGE,
    LIVE_MESSAGE,
    ALLOWED_ROLE_IDS,
)
from state import state
from bot import client


def get_dynamic_message_channel_id(interaction: discord.Interaction) -> int:
    # If the command was used in the test channel, use that channel for messages
    if interaction.channel.id == TEST_CHANNEL_ID:
        return TEST_CHANNEL_ID
    # Otherwise, use the default from env
    return MESSAGE_CHANNEL_ID


### Log Handler ###


class DiscordLogHandler(logging.Handler):
    def __init__(self, client, channel_id):
        super().__init__()
        self.client = client
        self.channel_id = channel_id
        self.queue = asyncio.Queue()
        self.formatter = logging.Formatter(
            fmt="%(asctime)s | %(funcName)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def emit(self, record):
        london_tz = pytz.timezone("Europe/London")
        record.asctime = datetime.now(london_tz).strftime("%Y-%m-%d %H:%M:%S")
        msg = self.format(record)
        try:
            asyncio.create_task(self.queue.put(msg))
        except RuntimeError:
            # silently drop if no loop yet
            pass

    async def log_sender(self):
        await self.client.wait_until_ready()
        channel = self.client.get_channel(self.channel_id)
        if not channel:
            print("Log channel not found.")
            return
        while True:
            msg = await self.queue.get()
            try:
                if len(msg) > 1900:
                    msg = f"```{msg[:1900]}```"
                await channel.send(f"```{msg}```")
            except Exception as e:
                print(f"Failed to send log to Discord: {e}")
            await asyncio.sleep(0.1)


async def post_command_message(message):
    await client.wait_until_ready()
    channel = client.get_channel(COMMAND_CHANNEL_ID)

    if channel:
        await channel.send(message)
        logger.info(f"Posted command message: {message}")
    else:
        logger.error("Command channel not found")


async def post_message(message, interaction=None):
    await client.wait_until_ready()
    channel_id = (
        get_dynamic_message_channel_id(interaction)
        if interaction
        else MESSAGE_CHANNEL_ID
    )
    channel = client.get_channel(channel_id)

    if channel:
        sent_message = await channel.send(message)
        logger.info(f"Posted message: {message}")

        # Publish announcement messages
        if message in [SEEDING_MESSAGE, LIVE_MESSAGE]:
            try:
                await sent_message.publish()
                logger.info("Published message to announcement channel")
            except discord.Forbidden:
                logger.warning("Bot lacks permission to publish messages")
            except discord.HTTPException as e:
                logger.error(f"Failed to publish message: {e}", exc_info=True)
        elif message.startswith("Currently"):
            state.milestone_messages.append(sent_message)
    else:
        logger.error("Message channel not found")


async def delete_milestone_messages():
    logger.info(f"Deleting {len(state.milestone_messages)} milestone messages")
    for msg in state.milestone_messages:
        try:
            await msg.delete()
            logger.info("Deleted milestone message")
        except discord.HTTPException as e:
            logger.warning(f"Failed to delete milestone message: {e}", exc_info=True)
    state.milestone_messages.clear()


async def post_command_response(message, interaction=None):
    await client.wait_until_ready()
    channel_id = (
        get_dynamic_message_channel_id(interaction)
        if interaction
        else COMMAND_CHANNEL_ID
    )
    channel = client.get_channel(channel_id)
    if channel:
        await channel.send(message)
        logger.info(f"Sent command response: {message}")
    else:
        logger.error("Command channel not found")


def requires_roles():
    async def predicate(interaction: discord.Interaction) -> bool:
        user_roles = {role.id for role in interaction.user.roles}
        # Returns True only if user has at least one allowed role
        return bool(user_roles.intersection(ALLOWED_ROLE_IDS))

    return discord.app_commands.check(predicate)


def format_results(header: str, results: list[str]) -> str:
    """Formats Discord output with a header and code block for clarity."""
    return f"{header}\n```\n" + "\n".join(results) + "\n```"


def get_population_emoji(players):
    if players < 20:
        return "🔴"
    elif players <= 60:
        return "🟡"
    else:
        return "🟢"
