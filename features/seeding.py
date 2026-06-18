"""Seeding logic: auto-seed window, triggers, scheduling, monitor loop."""

import asyncio
from datetime import datetime, timedelta, time

import pytz

from config import (
    logger,
    london_tz,
    COOLDOWN_HOURS,
    milestones,
    AUTO_TRIGGER_MIN,
    AUTO_TRIGGER_MAX,
    AUTO_SEED_START_HOUR_LONDON,
    AUTO_SEED_END_HOUR_LONDON,
    LIVE_PLAYER_COUNT,
    SEEDING_MESSAGE,
    LIVE_MESSAGE,
    PLAYER_COUNT_MESSAGE,
    CHECK_INTERVAL_SECONDS,
)
from state import state
from utils.retry import fetch_player_count
from utils.discord_helpers import (
    post_message,
    post_command_message,
    post_command_response,
    delete_milestone_messages,
)


def is_within_auto_seed_window():
    now = datetime.now(london_tz).time()
    start = time(AUTO_SEED_START_HOUR_LONDON, 0)
    end = time(AUTO_SEED_END_HOUR_LONDON, 0)
    return start <= now < end


async def trigger_seeding(player_count=None):
    state.seeding_started = True

    # Always fetch the latest player count for accurate logging
    latest_count = await fetch_player_count()
    state.seeding_triggered_at = (
        latest_count if latest_count is not None else (player_count or 0)
    )

    await post_message(SEEDING_MESSAGE)
    logger.info(f"Seeding triggered at {state.seeding_triggered_at} players")


async def reset_bot():
    logger.info(
        f"Resetting bot state | was_active={state.seeding_started}, posted_milestones_count={len(state.posted_milestones)}"
    )
    state.seeding_started = False
    state.posted_milestones.clear()
    state.milestone_messages.clear()
    state.seeding_triggered_at = 0
    logger.debug("Bot state reset complete")


async def check_server():

    player_count = await fetch_player_count()
    if player_count is None:
        return

    now = datetime.now(london_tz)

    # ---------------------------------------
    # TIME-AND-RANGE BASED AUTO-SEEDING WITH COOLDOWN
    # ---------------------------------------
    if (
        not state.seeding_started
        and AUTO_TRIGGER_MIN <= player_count <= AUTO_TRIGGER_MAX
        and is_within_auto_seed_window()
    ):
        # Check cooldown
        if (
            state.last_seed_trigger_time
            and now - state.last_seed_trigger_time < timedelta(hours=COOLDOWN_HOURS)
        ):
            logger.info(
                f"Auto-trigger skipped: last seeding was {now - state.last_seed_trigger_time} ago (cooldown {COOLDOWN_HOURS}h)."
            )
        else:
            logger.info(
                f"Auto-triggering seeding at {player_count} players "
                f"(within range {AUTO_TRIGGER_MIN}-{AUTO_TRIGGER_MAX} and 11:00–18:00 London time.)"
            )

            # Cancel any scheduled seeding
            if state.scheduled_seeding_time:
                reset_scheduled_seeding()

            await post_command_message(
                f"Player count reached {player_count} between {AUTO_SEED_START_HOUR_LONDON}00-{AUTO_SEED_END_HOUR_LONDON}00 London time. Auto-triggering seeding."
            )

            await trigger_seeding(player_count)
            state.last_seed_trigger_time = now
        return

    # ---------------------------------------
    # NORMAL SEEDING MILESTONE LOGIC
    # ---------------------------------------
    if not state.seeding_started:
        return

    for milestone in milestones:
        if (
            milestone >= state.seeding_triggered_at
            and player_count >= milestone
            and milestone not in state.posted_milestones
        ):
            if milestone == LIVE_PLAYER_COUNT:
                await post_message(LIVE_MESSAGE)
                await delete_milestone_messages()
                await reset_bot()
                return
            else:
                await post_message(PLAYER_COUNT_MESSAGE.format(milestone=milestone))
                state.posted_milestones.add(milestone)


async def schedule_seeding_at(time_str):

    london_tz = pytz.timezone("Europe/London")
    now = datetime.now(london_tz)

    try:
        # Step 1: Parse time as naive datetime
        naive_time = datetime.strptime(time_str, "%H:%M")
        naive_datetime = datetime(
            year=now.year,
            month=now.month,
            day=now.day,
            hour=naive_time.hour,
            minute=naive_time.minute,
        )

        # Step 2: Localize properly
        scheduled_time = london_tz.localize(naive_datetime, is_dst=None)

        # Step 3: Adjust to next day if time has passed
        if scheduled_time < now:
            scheduled_time += timedelta(days=1)

        wait_seconds = (scheduled_time - now).total_seconds()

        # Cancel previous task if it exists
        if state.scheduled_task:
            try:
                state.scheduled_task.cancel()
                logger.info(
                    f"Canceled previous scheduled seeding at {state.scheduled_seeding_time.strftime('%H:%M')}"
                )
            except asyncio.CancelledError:
                logger.warning("Scheduled task cancelation raised CancelledError")

        state.scheduled_seeding_time = scheduled_time

        async def delayed_trigger():
            logger.info(f"Waiting {wait_seconds} seconds to trigger scheduled seeding")
            await asyncio.sleep(wait_seconds)
            player_count = await fetch_player_count()
            await trigger_seeding(player_count)
            await post_command_response(
                f"Scheduled seeding triggered at {scheduled_time.strftime('%H:%M')}"
            )

            # Reset schedule tracking
            reset_scheduled_seeding()

        state.scheduled_task = asyncio.create_task(delayed_trigger())
        return scheduled_time

    except ValueError:
        return None


def reset_scheduled_seeding():
    if state.scheduled_task:
        logger.info(
            f"Canceling scheduled seeding task that was set for {state.scheduled_seeding_time.strftime('%H:%M') if state.scheduled_seeding_time else 'unknown time'}"
        )
        state.scheduled_task.cancel()
    state.scheduled_seeding_time = None
    state.scheduled_task = None


async def background_task():
    while True:
        try:
            logger.debug("Running server check cycle")
            await check_server()
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        except Exception as e:
            logger.error(f"Error in background_task: {e}", exc_info=True)
            await asyncio.sleep(5)
