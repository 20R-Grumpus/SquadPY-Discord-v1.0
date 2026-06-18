"""Utility slash commands: fog, killfeed, leaderboard, player counts."""

import os
import re
import json
import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
import aiofiles
import dateutil.parser
from discord.ui import View, Button

from config import (
    logger,
    SERVER_LIST,
    FOG_JSON_PATH,
    SFTP_HOST,
    SFTP_PORT,
    SFTP_USER,
    SFTP_PASSWORD,
    SFTP_REMOTE_LOG_PATH,
    SQUADJS_SFTP_HOST,
    SQUADJS_SFTP_PORT,
    SQUADJS_SFTP_USER,
    SQUADJS_SFTP_PASSWORD,
    SQUADJS_SFTP_FOG_PATH,
    SEEDING_LEADERBOARD_FILE,
)
from bot import tree
from state import save_state, load_state
from utils.discord_helpers import requires_roles, get_population_emoji
from utils.retry import robust_fetch_player_count
from utils.sftp import sftp_write_content, sftp_download
from utils.validation import is_valid_layer_name


class SeedTrackRefreshView(View):
    def __init__(self, message_id):
        super().__init__(timeout=None)
        self.message_id = message_id
        self.add_item(SeedTrackRefreshButton(message_id=message_id))


class SeedTrackRefreshButton(Button):
    def __init__(self, message_id):
        super().__init__(label="Refresh Now", style=discord.ButtonStyle.blurple)
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        try:
            # Acknowledge interaction immediately so Discord knows it's received
            await interaction.response.defer()

            # Fetch the original message
            channel = interaction.channel
            message = await channel.fetch_message(self.message_id)

            logger.info(
                f"Manual refresh triggered by {interaction.user} "
                f"for message_id={self.message_id}"
            )

            # Build new embed
            embed = message.embeds[0]
            new_embed = discord.Embed(title=embed.title, color=embed.color)
            server_names = list(SERVER_LIST.keys())

            for i in range(0, len(server_names), 2):
                # Field 1
                players, max_players, queue = await robust_fetch_player_count(
                    SERVER_LIST[server_names[i]]
                )
                logger.info(
                    f"Manual fetch for {server_names[i]}: players={players}, queue={queue}"
                )
                value1 = (
                    f"{get_population_emoji(players)} **{players}/{max_players} +{queue}**"
                    if players is not None
                    else "❌ Failed"
                )
                new_embed.add_field(name=server_names[i], value=value1, inline=True)

                # Field 2 if exists
                if i + 1 < len(server_names):
                    players, max_players, queue = await robust_fetch_player_count(
                        SERVER_LIST[server_names[i + 1]]
                    )
                    logger.info(
                        f"Manual fetch for {server_names[i+1]}: players={players}, queue={queue}"
                    )
                    value2 = (
                        f"{get_population_emoji(players)} **{players}/{max_players} +{queue}**"
                        if players is not None
                        else "❌ Failed"
                    )
                    new_embed.add_field(
                        name=server_names[i + 1], value=value2, inline=True
                    )

            new_embed.set_footer(text="Manual refresh — last updated")
            new_embed.timestamp = datetime.utcnow()

            await message.edit(embed=new_embed)
            logger.info(
                f"Manual refresh for message_id={self.message_id} committed successfully."
            )

        except Exception as e:
            logger.exception(f"Failed during manual refresh: {e}")
            # Only send error message if deferred response was not sent
            try:
                await interaction.followup.send(
                    "Failed to refresh the embed ❌", ephemeral=True
                )
            except:
                pass


@tree.command(
    name="addfog",
    description="Add a layer to fog_off_maps.json",
)
@requires_roles()
@app_commands.describe(full_layer_name="Full Layer Name in format MapName_GameMode_vX")
async def add_fog(interaction: discord.Interaction, full_layer_name: str):
    await interaction.response.defer(ephemeral=True)
    logger.info(f"Triggered by {interaction.user} with layer: {full_layer_name}")

    # Validate layer format
    if not is_valid_layer_name(full_layer_name):
        await interaction.followup.send(
            "Invalid format! Must be MapName_GameMode_vX, e.g. Manicouagan_RAAS_v2",
            ephemeral=True,
        )
        return

    # Load existing layers
    if FOG_JSON_PATH.exists():
        with open(FOG_JSON_PATH, "r") as f:
            data = json.load(f)
        layers = data.get("layers", [])
    else:
        layers = []

    # Check if layer already exists
    if full_layer_name in layers:
        await interaction.followup.send(
            f"{full_layer_name} is already in fog_off_maps.json", ephemeral=True
        )
        return

    # Add layer locally
    layers.append(full_layer_name)
    fog_json_data = json.dumps({"layers": layers}, indent=2)
    with open(FOG_JSON_PATH, "w") as f:
        f.write(fog_json_data)

    # Prepare action message
    action_msg = f"Added `{full_layer_name}` to fog_off_maps.json."

    # Upload via SFTP with error handling
    try:
        sftp_write_content(
            SQUADJS_SFTP_HOST,
            SQUADJS_SFTP_PORT,
            SQUADJS_SFTP_USER,
            SQUADJS_SFTP_PASSWORD,
            SQUADJS_SFTP_FOG_PATH,
            fog_json_data,
        )
        # Only send response after successful upload
        await interaction.followup.send(action_msg, ephemeral=True)
        logger.info(
            f"Action by {interaction.user}: {action_msg} | Total layers: {len(layers)}"
        )
    except Exception as e:
        logger.error(f"SFTP upload failed: {e}", exc_info=True)
        await interaction.followup.send(
            f"{action_msg} — but failed to upload to SFTP: {e}", ephemeral=True
        )


@tree.command(
    name="showfog",
    description="Show all layers in fog_off_maps.json",
)
async def show_fog(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    # Check if file exists
    if not FOG_JSON_PATH.exists():
        await interaction.followup.send(
            "fog_off_maps.json does not exist.", ephemeral=True
        )
        return

    # Load JSON
    with open(FOG_JSON_PATH, "r") as f:
        data = json.load(f)

    layers = data.get("layers", [])

    if not layers:
        await interaction.followup.send(
            "No layers found in fog_off_maps.json.", ephemeral=True
        )
        return

    # Format layers as a string
    layers_list = "\n".join(f"- {layer}" for layer in layers)

    # Send as ephemeral message
    await interaction.followup.send(
        f"**Fog Off Layers:**\n{layers_list}", ephemeral=True
    )


@tree.command(
    name="removefog",
    description="Remove a layer from fog_off_maps.json or clear all layers",
)
@requires_roles()
@app_commands.describe(mode="Select a specific layer or clear all layers")
@app_commands.choices(
    mode=[
        discord.app_commands.Choice(
            name="Full Layer Name", value="layername"
        ),  # placeholder
        discord.app_commands.Choice(name="Clear All", value="clear_all"),
    ]
)
async def remove_fog(
    interaction: discord.Interaction,
    mode: discord.app_commands.Choice[str],
):
    await interaction.response.defer(ephemeral=True)
    logger.info(f"Triggered by {interaction.user} | mode={mode.value}")

    if not FOG_JSON_PATH.exists():
        await interaction.followup.send(
            "fog_off_maps.json does not exist.", ephemeral=True
        )
        return

    # Load current layers
    with open(FOG_JSON_PATH, "r") as f:
        data = json.load(f)
    layers = data.get("layers", [])

    # Handle Clear All
    if mode.value == "clear_all":
        layers.clear()
        action_msg = "All layers have been removed."
    else:
        full_layer_name = mode.value
        if not is_valid_layer_name(full_layer_name):
            await interaction.followup.send(
                "Invalid layer format! Must be MapName_GameMode_vX, e.g. Manicouagan_RAAS_v2",
                ephemeral=True,
            )
            return

        if full_layer_name not in layers:
            await interaction.followup.send(
                f"{full_layer_name} is not in fog_off_maps.json", ephemeral=True
            )
            return

        layers.remove(full_layer_name)
        action_msg = f"Removed `{full_layer_name}` from fog_off_maps.json."

    # Save locally
    fog_json_data = json.dumps({"layers": layers}, indent=2)
    with open(FOG_JSON_PATH, "w") as f:
        f.write(fog_json_data)

    # Upload via SFTP and catch errors
    try:
        sftp_write_content(
            SQUADJS_SFTP_HOST,
            SQUADJS_SFTP_PORT,
            SQUADJS_SFTP_USER,
            SQUADJS_SFTP_PASSWORD,
            SQUADJS_SFTP_FOG_PATH,
            fog_json_data,
        )
        # Only send response after successful upload
        await interaction.followup.send(action_msg, ephemeral=True)
        logger.info(
            f"Action by {interaction.user}: {action_msg} | Total layers: {len(layers)}"
        )
    except Exception as e:
        logger.error(f"SFTP upload failed: {e}", exc_info=True)
        await interaction.followup.send(
            f"{action_msg} — but failed to upload to SFTP: {e}", ephemeral=True
        )


@tree.command(
    name="killfeed",
    description="Parse the killfeed log from SFTP or upload your own file.",
)
@requires_roles()
@discord.app_commands.describe(
    player_id="The Steam ID or EOS ID to filter",
    id_type="Choose whether to search by Steam ID or EOS ID",
    mode="Choose whether to use today's log or upload your own",
    log_file="Upload your log file if mode is upload (optional)",
)
@discord.app_commands.choices(
    id_type=[
        discord.app_commands.Choice(name="Steam ID", value="steamid"),
        discord.app_commands.Choice(name="EOS ID", value="eosid"),
    ],
    mode=[
        discord.app_commands.Choice(name="todays", value="todays"),
        discord.app_commands.Choice(name="upload", value="upload"),
    ],
)
async def killfeed(
    interaction: discord.Interaction,
    player_id: str,
    id_type: discord.app_commands.Choice[str],
    mode: discord.app_commands.Choice[str],
    log_file: discord.Attachment = None,
):
    await interaction.response.defer(ephemeral=False)
    logger.info(
        f"Triggered by {interaction.user} | player_id={player_id}, id_type={id_type.value}, mode={mode.value}, has_file={log_file is not None}"
    )

    temp_filename = None
    try:
        if mode.value == "todays":
            # Download from SFTP using async executor
            temp_filename = "temp_sftp_log.txt"
            success = await sftp_download(
                SFTP_HOST,
                SFTP_PORT,
                SFTP_USER,
                SFTP_PASSWORD,
                SFTP_REMOTE_LOG_PATH,
                temp_filename,
            )
            if not success:
                await interaction.followup.send(
                    "Failed to download log file from SFTP.", ephemeral=True
                )
                return
        elif mode.value == "upload":
            if not log_file:
                await interaction.followup.send(
                    "You must upload a log file when using 'upload' mode.",
                    ephemeral=True,
                )
                return
            temp_filename = f"temp_uploaded_{log_file.filename}"
            await log_file.save(temp_filename)
        else:
            await interaction.followup.send("Invalid mode selected.", ephemeral=True)
            return

        # Regex pattern for Squad killfeed lines (matches JS logic)
        pattern = re.compile(
            r"\[([0-9.:-]+)]\[([ 0-9]*)]LogSquadTrace: \[DedicatedServer](?:ASQSoldier::)?Wound\(\): Player:(.+) KillingDamage=(?:-)*([0-9.]+) from ([A-z_0-9]+) \(Online IDs:([^)|]+)\| Controller ID: ([\w\d]+)\) caused by ([A-z_0-9-]+)_C"
        )

        output_lines = []
        timestamps = []

        # Build search pattern based on id_type
        if id_type.value == "steamid":
            search_pattern = f"steam: {player_id}"
        else:  # eosid
            search_pattern = f"EOS: {player_id}"

        async with aiofiles.open(
            temp_filename, "r", encoding="utf-8", errors="replace"
        ) as f:
            async for line in f:
                match = pattern.search(line)
                if match and search_pattern in line:
                    # Extract the relevant groups and format as: [timestamp][id] > victim_name > damage > weapon
                    timestamp = match.group(1)
                    timestamp_id = match.group(2)
                    victim_name = match.group(3).strip()  # Player name
                    damage = match.group(4)  # Damage value
                    weapon = match.group(8)  # Weapon/cause

                    # Clean up damage - remove trailing zeros
                    damage_float = float(damage)
                    damage_clean = str(damage_float).rstrip("0").rstrip(".")

                    # Pad victim name for alignment (24 chars)
                    victim_name_padded = victim_name.ljust(24)

                    # Pad damage for alignment (10 chars, left-aligned)
                    damage_padded = damage_clean.ljust(10)

                    # Format the output line
                    formatted_line = f"[{timestamp}][{timestamp_id}] > {victim_name_padded} > {damage_padded} > {weapon}"
                    output_lines.append(formatted_line)
                    # Extract timestamp for stats calculation
                    timestamps.append(timestamp)

        if output_lines:
            output_filename = f"parsed_log_{id_type.value}_{player_id}.txt"
            logger.info(f"Parsed {len(output_lines)} matching killfeed lines")
            # Calculate stats
            total_kills = len(output_lines)
            duration_str = "N/A"
            avg_per_min = "N/A"
            highest_minute = "N/A"
            if len(timestamps) >= 2:

                # Try to parse timestamps as best as possible (format: 2024.08.11-19.23.45:123)
                def parse_ts(ts):
                    try:
                        return datetime.strptime(ts.split(":")[0], "%Y.%m.%d-%H.%M.%S")
                    except Exception:
                        return None

                parsed = [parse_ts(ts) for ts in timestamps if parse_ts(ts)]
                parsed = [p for p in parsed if p]
                if len(parsed) >= 2:
                    first, last = parsed[0], parsed[-1]
                    duration = (last - first).total_seconds()
                    if duration > 0:
                        duration_str = f"{int(duration//60)}m {int(duration%60)}s"
                        avg_per_min = f"{total_kills/(duration/60):.2f}"
                    # Calculate highest kills in any one-minute window
                    parsed.sort()
                    max_in_minute = 0
                    left = 0
                    for right in range(len(parsed)):
                        while (parsed[right] - parsed[left]).total_seconds() > 60:
                            left += 1
                        max_in_minute = max(max_in_minute, right - left + 1)
                    highest_minute = str(max_in_minute)
            # Write stats and lines
            header = f"Total kills: {total_kills}\nDuration: {duration_str}\nAvg per minute: {avg_per_min}\nHighest in one minute: {highest_minute}\n\n"
            async with aiofiles.open(output_filename, "w", encoding="utf-8") as f:
                await f.write(header + "\n".join(output_lines))
            await interaction.followup.send(
                content="",
                file=discord.File(output_filename),
                ephemeral=False,
            )
            os.remove(output_filename)
        else:
            await interaction.followup.send(
                "No matching killfeed lines found in the log file.", ephemeral=True
            )
    except Exception as e:
        await interaction.followup.send(
            f"Error processing killfeed: {e}", ephemeral=True
        )
    finally:
        # Cleanup temp files
        if temp_filename and os.path.exists(temp_filename):
            os.remove(temp_filename)


@tree.command(name="test", description="Test command")
async def test(interaction: discord.Interaction):
    await interaction.response.send_message("Test successful")


@tree.command(
    name="seederboard",
    description="Display the top 30 seeding leaderboard.",
)
async def seederboard(interaction: discord.Interaction):
    await interaction.response.defer()

    file_path = SEEDING_LEADERBOARD_FILE

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            leaderboard = data.get("leaderboard", [])
            generated_at_str = data.get("generated_at")
    except Exception as e:
        await interaction.followup.send(
            f"❌ Failed to load leaderboard data: `{e}`", ephemeral=True
        )
        return

    # Build leaderboard display
    top_30 = leaderboard[:30]
    lines = []
    for entry in top_30:
        rank = entry.get("rank", "?")
        username = entry.get("username", "Unknown")
        points = entry.get("points", 0)
        steamid64 = entry.get("steamid64", "")
        steam_url = f"https://steamcommunity.com/profiles/{steamid64}"
        lines.append(f"**{rank}.** [{username}]({steam_url}) — {points} pts")

    description = "\n".join(lines)

    # Create purple embed
    embed = discord.Embed(
        title="❤️ Seeding Leaderboard (Top 30)",
        description=description,
        color=9247733,  # Discord blurple
    )

    # Add footer with generated_at time
    try:
        if generated_at_str:
            generated_at_dt = dateutil.parser.isoparse(generated_at_str)
            formatted_time = generated_at_dt.strftime("%Y-%m-%d %H:%M UTC")
            embed.set_footer(text=f"🕓 Updated hourly at: {formatted_time}")
        else:
            embed.set_footer(text="Last updated: unknown")
    except Exception:
        embed.set_footer(text="Last updated: error parsing time")

    await interaction.followup.send(embed=embed)


def get_start_time(timespan: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    if timespan == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif timespan == "week":
        start_of_week = now - timedelta(days=now.weekday())
        return start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
    elif timespan == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return None  # all time


@tree.command(
    name="seedtrack", description="Fetch player counts for all tracked servers."
)
@requires_roles()
async def playercounts(interaction: discord.Interaction):
    await interaction.response.defer()
    channel = interaction.channel

    logger.info(f"Command invoked by {interaction.user} in #{channel.id}")

    embed = discord.Embed(
        title="Server Player Counts",
        description="",
        color=discord.Color.blurple(),
    )

    server_names = list(SERVER_LIST.keys())

    # Fetch counts for all servers and populate fields (inline 2 per row)
    for i in range(0, len(server_names), 2):

        # Field 1
        players, max_players, queue = await robust_fetch_player_count(
            SERVER_LIST[server_names[i]]
        )
        logger.info(
            f"Initial fetch for {server_names[i]}: players={players}, queue={queue}"
        )

        if players is None:
            value1 = "❌ Failed"
        else:
            emoji = get_population_emoji(players)
            value1 = f"{emoji} **{players}/{max_players} +{queue}**"
        embed.add_field(name=server_names[i], value=value1, inline=True)

        # Field 2 if exists
        if i + 1 < len(server_names):
            players, max_players, queue = await robust_fetch_player_count(
                SERVER_LIST[server_names[i + 1]]
            )
            logger.info(
                f"Initial fetch for {server_names[i+1]}: players={players}, queue={queue}"
            )

            if players is None:
                value2 = "❌ Failed"
            else:
                emoji = get_population_emoji(players)
                value2 = f"{emoji} **{players}/{max_players} +{queue}**"
            embed.add_field(name=server_names[i + 1], value=value2, inline=True)

    # Send embed with temporary view placeholder
    message = await interaction.followup.send(
        embed=embed,
        view=SeedTrackRefreshView(message_id=0),  # will update after saving state
    )

    # Save state for updater
    expires = (datetime.utcnow() + timedelta(hours=2)).timestamp()
    save_state({"message_id": message.id, "channel_id": channel.id, "expires": expires})

    # Update view with correct message_id
    await message.edit(view=SeedTrackRefreshView(message.id))

    logger.info(
        f"Seed Tracking started (message_id={message.id}, channel_id={channel.id}, expires={expires})"
    )

    # Start update loop
    asyncio.create_task(seedtrack_update_loop(interaction.client))


async def seedtrack_update_loop(bot):
    INTERVAL = 15 * 60  # 15 minutes

    while True:
        await asyncio.sleep(INTERVAL)

        state = load_state()
        if not state:
            logger.info("No active tracking state — stopping loop.")
            return

        expired = datetime.utcnow().timestamp() > state["expires"]

        try:
            channel_id = int(state["channel_id"])
            message_id = int(state["message_id"])
            channel = bot.get_channel(channel_id)
            message = await channel.fetch_message(message_id)

            if expired:
                logger.info(
                    f"Tracking expired — sending FINAL UPDATE for message_id={message_id}"
                )
            else:
                logger.info(
                    f"Running update cycle for message_id={message_id} in channel_id={channel_id}"
                )

        except Exception as e:
            logger.exception(f"Failed to fetch message/channel — stopping: {e}")
            save_state({})
            return

        # Build new embed
        embed = message.embeds[0]
        new_embed = discord.Embed(title=embed.title, color=embed.color)
        server_names = list(SERVER_LIST.keys())

        for i in range(0, len(server_names), 2):

            # Field 1
            players, max_players, queue = await robust_fetch_player_count(
                SERVER_LIST[server_names[i]]
            )
            logger.info(
                f"Update fetch for {server_names[i]}: players={players}, queue={queue}"
            )

            if players is None:
                value1 = "❌ Failed"
            else:
                emoji = get_population_emoji(players)
                value1 = f"{emoji} **{players}/{max_players} +{queue}**"
            new_embed.add_field(name=server_names[i], value=value1, inline=True)

            # Field 2 if exists
            if i + 1 < len(server_names):
                players, max_players, queue = await robust_fetch_player_count(
                    SERVER_LIST[server_names[i + 1]]
                )
                logger.info(
                    f"Update fetch for {server_names[i+1]}: players={players}, queue={queue}"
                )

                if players is None:
                    value2 = "❌ Failed"
                else:
                    emoji = get_population_emoji(players)
                    value2 = f"{emoji} **{players}/{max_players} +{queue}**"
                new_embed.add_field(name=server_names[i + 1], value=value2, inline=True)

        # Footer text depending on final or normal update
        if expired:
            new_embed.set_footer(text="Final update — tracking expired")
        else:
            new_embed.set_footer(text="Last updated")

        new_embed.timestamp = datetime.utcnow()

        try:
            await message.edit(embed=new_embed)
            logger.info(
                "Embed update committed "
                + ("(FINAL UPDATE)." if expired else "successfully.")
            )
        except Exception as e:
            logger.exception(f"Failed to edit embed: {e}")

        # Stop after final update
        if expired:
            save_state({})
            return
