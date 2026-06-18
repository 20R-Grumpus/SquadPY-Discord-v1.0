"""Discord event listeners: forum ban evidence and sticky messages."""

import re
import asyncio
import sqlite3

import discord

from config import (
    logger,
    FORUM_CHANNEL_ID,
    STICKY_CHANNEL_ID,
    STICKY_PING_ROLE_ID,
    STICKY_TICKET_CHANNEL_ID,
    BANNED_PLAYERS_DB_PATH,
)
from state import state, get_state, update_state
from bot import client
from database import (
    load_banned_players,
    find_player_by_steamid,
    push_banned_players_to_sftp,
)

# --- Sticky Info Message Feature ---


STICKY_EMBED = discord.Embed(
    description=(
        "**As a reminder this channel is for reports only, no discussion allowed.**\n\n"
        f"Ping <@&{STICKY_PING_ROLE_ID}> for urgent matters.\n\n"
        f"Slowmode is enabled; if you need to add details, edit your message or open a ticket in <#{STICKY_TICKET_CHANNEL_ID}>."
    ),
    color=0xFF0000,
)


def load_sticky_state():
    try:
        return get_state("sticky", {}).get("last_sticky_id")
    except Exception as e:
        logger.warning(f"Couldn't load sticky state: {e}")
        return None


def save_sticky_state(message_id):
    try:
        update_state("sticky", last_sticky_id=message_id)
    except Exception as e:
        logger.warning(f"Couldn't save sticky state: {e}")


def extract_steamid_from_title(title: str) -> str | None:
    """Extract SteamID from forum post title in format 'Name / SteamID'"""
    match = re.search(r"(\d{17})", title)
    if match:
        return match.group(1)
    return None


def extract_ban_info_from_embed(embed: discord.Embed) -> dict | None:
    """Extract player info (Name, SteamID, EOSID) from ban information embed"""
    try:
        player_info = {}

        if not embed.fields:
            return None

        # Parse embed fields to extract needed information
        for field in embed.fields:
            field_name = field.name.strip()
            field_value = field.value.strip()

            # Extract Name
            if "Player" in field_name or "Player / RCON" in field_name:
                player_info["name"] = field_value

            # Extract SteamID
            elif "SteamID" in field_name or "SteamID / Steam Profile" in field_name:
                # Extract just the ID part if there's a link
                match = re.search(r"(\d{17})", field_value)
                if match:
                    player_info["steamid"] = match.group(1)

            # Extract EOS ID
            elif "EOS ID" in field_name or "EOS" in field_name:
                # Remove any markdown formatting and clean up
                eos_id = (
                    field_value.replace("[", "")
                    .replace("]", "")
                    .replace("(", "")
                    .replace(")", "")
                    .strip()
                )
                match = re.search(r"([a-f0-9]{32})", eos_id)
                if match:
                    player_info["eosid"] = match.group(1)

        # Require at least steamid
        if "steamid" not in player_info:
            return None

        logger.info(f"Extracted player info: {player_info}")
        return player_info
    except Exception as e:
        logger.warning(f"Failed to extract ban info from embed: {e}")
        return None


state.LAST_STICKY_MESSAGE_ID = load_sticky_state()


@client.event
async def on_thread_create(thread: discord.Thread):
    """Listen for new forum posts in the ban evidence forum channel"""
    try:
        # Only listen to the specified forum channel
        if thread.parent.id != FORUM_CHANNEL_ID:
            return

        logger.info(f"New forum post detected: '{thread.name}' in {thread.parent.name}")

        # Extract SteamID from thread title
        steamid = extract_steamid_from_title(thread.name)
        if not steamid:
            logger.warning(f"No SteamID found in thread title: {thread.name}")
            return

        logger.info(f"Extracted SteamID from title: {steamid}")

        # Wait a moment for the ban information embed to be posted
        await asyncio.sleep(1)

        # Load current banned players list
        banned_list = load_banned_players()

        # Try to extract detailed info from embeds in the thread
        player_info = {"steamid": steamid}  # Start with steamid from title

        try:
            # Fetch recent messages in the thread to find the ban embed
            async for message in thread.history(limit=10):
                if message.embeds and len(message.embeds) > 0:
                    for embed in message.embeds:
                        extracted = extract_ban_info_from_embed(embed)
                        if extracted:
                            player_info.update(extracted)
                            break
                    if len(player_info) > 1:  # Found more than just steamid
                        break
        except Exception as e:
            logger.warning(f"Could not fetch embed details: {e}")
            # Continue with just steamid if embed parsing fails

        # Check if player already exists
        player_lookup = find_player_by_steamid(banned_list, steamid)
        updated = False

        if player_lookup:
            # Player exists, merge new data
            existing_player = player_lookup
            new_fields = []

            try:
                conn = sqlite3.connect(BANNED_PLAYERS_DB_PATH)
                cursor = conn.cursor()

                # Update with new fields from player_info
                for key, value in player_info.items():
                    if key not in existing_player and value:  # Only add if not present
                        if key == "eosid":
                            cursor.execute(
                                "UPDATE banned_players SET eosid = ? WHERE steamid = ?",
                                (value, steamid),
                            )
                            new_fields.append(f"{key}: {value}")
                            updated = True
                        elif key == "name":
                            cursor.execute(
                                "UPDATE banned_players SET name = ? WHERE steamid = ?",
                                (value, steamid),
                            )
                            new_fields.append(f"{key}: {value}")
                            updated = True

                if updated:
                    conn.commit()
                    logger.info(
                        f"Updated SteamID {steamid} with new fields: {new_fields}"
                    )
                    # Push if data was updated
                    await push_banned_players_to_sftp()

                    response_msg = f"✅ Updated ban evidence database with new info:"
                    for field in new_fields:
                        response_msg += f"\n• **{field}**"
                    await thread.send(response_msg)
                else:
                    logger.info(
                        f"SteamID {steamid} already in database with all current data"
                    )
                    await thread.send(
                        f"ℹ️ SteamID `{steamid}` already in database with all available info"
                    )
                conn.close()
            except Exception as e:
                logger.error(f"Error updating player in database: {e}")
                await thread.send(f"❌ Error updating database: {str(e)}")
        else:
            # New player, add to database
            try:
                conn = sqlite3.connect(BANNED_PLAYERS_DB_PATH)
                cursor = conn.cursor()

                name = player_info.get("name", "Unknown")
                eosid = player_info.get("eosid")

                cursor.execute(
                    "INSERT INTO banned_players (name, steamid, eosid) VALUES (?, ?, ?)",
                    (name, steamid, eosid),
                )
                conn.commit()
                conn.close()

                logger.info(f"Added player to banned players database: {player_info}")

                # Push to SFTP
                await push_banned_players_to_sftp()

                # Format response message
                response_msg = f"✅ Added to banned player database:"
                if "name" in player_info:
                    response_msg += f"\n• **Name**: {player_info['name']}"
                response_msg += f"\n• **SteamID**: `{player_info['steamid']}`"
                if "eosid" in player_info:
                    response_msg += f"\n• **EOSID**: `{player_info['eosid']}`"

                await thread.send(response_msg)
            except sqlite3.IntegrityError:
                logger.warning(f"Player with SteamID {steamid} already exists")
                await thread.send(
                    f"⚠️ Player with SteamID `{steamid}` already in database"
                )
            except Exception as e:
                logger.error(f"Error adding player to database: {e}")
                await thread.send(f"❌ Error adding to database: {str(e)}")

    except Exception as e:
        logger.error(f"Error processing forum post: {e}", exc_info=True)


@client.event
async def on_message(message: discord.Message):

    # Ignore bot messages
    if message.author.bot:
        return

    content = message.content.strip().lower()

    # ----------------------------------
    # COMMAND HANDLING (NO EARLY RETURN)
    # ----------------------------------
    if content == "!practice":
        await message.channel.send("Im done with the practices.")
        logger.info(f"!practice triggered by {message.author}")
        # IMPORTANT: no return — allow sticky logic to run

    # ----------------------------------
    # STICKY CHANNEL ONLY
    # ----------------------------------
    if message.channel.id != STICKY_CHANNEL_ID:
        return

    logger.info(f"New message in sticky channel from {message.author}")

    await asyncio.sleep(2)  # small delay to avoid race conditions

    try:
        # -------------------------------
        # DELETE OLD STICKY
        # -------------------------------
        if state.LAST_STICKY_MESSAGE_ID:
            try:
                old_msg = await message.channel.fetch_message(
                    state.LAST_STICKY_MESSAGE_ID
                )

                # Safety: do not delete the user message if IDs collide
                if old_msg.id != message.id:
                    await old_msg.delete()
                    logger.info(
                        f"Deleted old sticky message: {state.LAST_STICKY_MESSAGE_ID}"
                    )

            except discord.NotFound:
                logger.info("Old sticky message not found, will post new one.")
            except Exception as e:
                logger.warning(f"Couldn't delete old sticky: {e}", exc_info=True)

        # -------------------------------
        # POST NEW STICKY
        # -------------------------------
        new_msg = await message.channel.send(embed=STICKY_EMBED)
        state.LAST_STICKY_MESSAGE_ID = new_msg.id
        save_sticky_state(state.LAST_STICKY_MESSAGE_ID)

        logger.info(f"New sticky message posted: {state.LAST_STICKY_MESSAGE_ID}")

    except Exception as e:
        logger.error("Error maintaining sticky message", exc_info=True)
