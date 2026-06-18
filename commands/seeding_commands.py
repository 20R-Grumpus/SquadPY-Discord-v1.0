"""Seedbot slash commands."""

from datetime import datetime

import pytz
import discord
from discord import app_commands

from config import (
    logger,
    AUTO_TRIGGER_MIN,
    AUTO_TRIGGER_MAX,
    AUTO_SEED_START_HOUR_LONDON,
    AUTO_SEED_END_HOUR_LONDON,
)
from state import state
from bot import tree
from utils.discord_helpers import requires_roles, delete_milestone_messages
from utils.retry import fetch_player_count
from features.seeding import (
    trigger_seeding,
    schedule_seeding_at,
    reset_scheduled_seeding,
    reset_bot,
    is_within_auto_seed_window,
)
from features.rotation import send_rotation_update


@tree.command(
    name="seedbotstart",
    description="Start seeding manually or schedule at a specific time (24h UK time).",
)
@requires_roles()
@discord.app_commands.describe(time_str="Optional HH:MM time to schedule seeding")
async def seedbotstart(interaction: discord.Interaction, time_str: str = None):

    # SAFEST POSSIBLE DEFER – stops Unknown Interaction errors
    if not interaction.response.is_done():
        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to defer interaction: {e}")
            return

    # -------------------------
    #  SCHEDULING BRANCH
    # -------------------------
    if time_str:
        previous_time = (
            state.scheduled_seeding_time.strftime("%H:%M")
            if state.scheduled_seeding_time
            else None
        )

        scheduled_time = await schedule_seeding_at(time_str)
        if scheduled_time:
            london_tz = pytz.timezone("Europe/London")
            now = datetime.now(london_tz)
            day_label = "today" if scheduled_time.date() == now.date() else "tomorrow"

            if previous_time:
                await interaction.followup.send(
                    f"Previous scheduled seeding at {previous_time} was canceled.",
                    ephemeral=True,
                )

            unix_timestamp = int(scheduled_time.timestamp())
            await interaction.followup.send(
                f"Seeding scheduled for {scheduled_time.strftime('%H:%M')} London time {day_label}. <t:{unix_timestamp}:R>",
                ephemeral=False,
            )

        else:
            await interaction.followup.send(
                "Invalid time format. Use HH:MM (24h).", ephemeral=True
            )
        return

    # -------------------------
    #  MANUAL TRIGGER BRANCH
    # -------------------------
    if not state.seeding_started:
        if state.scheduled_seeding_time:
            reset_scheduled_seeding()
            await interaction.followup.send(
                "Previous scheduled seeding time cancelled.", ephemeral=True
            )

        logger.info(f"Seed start triggered by {interaction.user}")

        player_count = await fetch_player_count()
        await trigger_seeding(player_count)

        await interaction.followup.send("Seeding manually triggered.", ephemeral=False)

    else:
        await interaction.followup.send("Seeding is already active.", ephemeral=True)


@tree.command(
    name="seedbotstop",
    description="Stop seeding immediately or cancel scheduled seeding.",
)
@requires_roles()
async def seedbotstop(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if state.scheduled_seeding_time:
        reset_scheduled_seeding()
        await interaction.followup.send(
            "Scheduled seeding time cancelled.", ephemeral=False
        )
    elif state.seeding_started:
        await delete_milestone_messages()
        await reset_bot()
        await interaction.followup.send("Seeding manually stopped.", ephemeral=False)
    else:
        await interaction.followup.send(
            "Seeding is not currently active or scheduled.", ephemeral=True
        )


@tree.command(name="seedbotstatus", description="Show seeding status and player count.")
@requires_roles()
async def seedbotstatus(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    player_count = await fetch_player_count()

    # Auto-seed armed check
    if is_within_auto_seed_window() and not state.seeding_started:
        auto_seed_status = (
            f"ARMED (will trigger at {AUTO_TRIGGER_MIN}-{AUTO_TRIGGER_MAX} players, "
            f"{AUTO_SEED_START_HOUR_LONDON}:00–{AUTO_SEED_END_HOUR_LONDON}:00 London time)"
        )
    else:
        auto_seed_status = (
            f"DISARMED (auto-seeding only triggers between "
            f"{AUTO_SEED_START_HOUR_LONDON}:00–{AUTO_SEED_END_HOUR_LONDON}:00 London time)"
        )

    # Compose status message
    if state.seeding_started:
        status_msg = (
            f"Seeding is currently active.\n"
            f"Current player count: {player_count}\n"
            f"Auto-seed status: {auto_seed_status}"
        )
    elif state.scheduled_seeding_time:
        status_msg = (
            f"Seeding is scheduled for {state.scheduled_seeding_time.strftime('%H:%M')} London time.\n"
            f"Current player count: {player_count}\n"
            f"Auto-seed status: {auto_seed_status}"
        )
    else:
        status_msg = (
            f"Seeding is not active.\n"
            f"Current player count: {player_count}\n"
            f"Auto-seed status: {auto_seed_status}"
        )

    await interaction.followup.send(status_msg, ephemeral=True)


@tree.command(name="seedbothelp", description="Show SeedBot command help.")
@requires_roles()
async def seedbothelp(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    help_message = (
        "**SeedBot Command List:**\n"
        "`/seedbotstart` - Manually starts seeding if not already active.\n"
        "`/seedbotstart HH:MM` - Schedules seeding to start at a specific time (24h format, UK time).\n"
        "`/seedbotstop` - Stops active or scheduled seeding.\n"
        "`/seedbotstatus` - Shows seeding status with current player count.\n"
        "`/seedbotreload` - Reloads map rotation from google sheet.\n"
        "`/seedbothelp` - Displays this help message.\n"
        "`/killfeed` - Upload or pull todays log and parse killfeed from Steam ID or EOS ID.\n"
        "`/seederboard` - Embeds the current seeding leaderboard.\n"
    )
    await interaction.followup.send(help_message, ephemeral=True)


@tree.command(name="seedbotreload", description="Reload rotation from Google Sheets.")
@requires_roles()
@app_commands.describe(publish="Should the message be published?")
@app_commands.choices(
    publish=[
        app_commands.Choice(name="Yes", value="yes"),
        app_commands.Choice(name="No", value="no"),
    ]
)
async def seedbotreload(interaction: discord.Interaction, publish: str = "yes"):

    try:
        # ⚠️ MUST be first interaction response
        await interaction.response.defer(ephemeral=False)

        logger.info(
            f"Manual rotation reload triggered by {interaction.user} (publish={publish})"
        )

        # run heavy work AFTER defer
        result = await send_rotation_update(publish=(publish == "yes"))

        await interaction.followup.send(content=result, ephemeral=False)

    except Exception as e:
        logger.error(f"seedbotreload failed: {e}", exc_info=True)

        # safe fallback if interaction still valid
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"❌ Reload failed: {e}", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"❌ Reload failed: {e}", ephemeral=True
                )
        except Exception:
            pass
