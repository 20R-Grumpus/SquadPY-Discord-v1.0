"""Discord event listeners: forum ban evidence, sticky messages, and DM friend whitelist."""

import re
import asyncio
import sqlite3

import discord

from config import (
    logger,
    GUILD_ID,
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
from utils.validation import is_valid_steamid, is_valid_eosid
from features.whitelist import (
    max_friends_for_member,
    get_friend_count,
    get_friend_entries,
    add_friend_entry,
    remove_friend_entry,
    write_admins_cfg,
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


# -- DM friend whitelist helpers --------------------------------------------


async def _get_guild_member(user_id: int) -> discord.Member | None:
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        return None
    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.HTTPException):
        return None


_HELP_TEXT = (
    "**Friend Whitelist -- Available Commands**\n"
    "\n"
    "`<steam_id> [label]` -- Add a friend by Steam ID (17 digits)\n"
    "`<eos_id> [label]` -- Add a friend by EOS ID (32 characters)\n"
    "`!remove <steam_or_eos_id>` -- Remove a friend from your list\n"
    "`!friends` -- Show your current friends and remaining slots\n"
    "`!help` -- Show this help message\n"
    "\n"
    "**Examples:**\n"
    "`76561198140219287` -- add a friend by Steam ID\n"
    "`76561198140219287 MyBuddy` -- add with a label\n"
    "`!remove 76561198140219287` -- remove that friend\n"
)


async def _handle_dm(message: discord.Message):
    """Process a DM for the friend-whitelist system."""
    content = message.content.strip()
    if not content:
        return

    member = await _get_guild_member(message.author.id)
    if member is None:
        await message.channel.send(
            "I couldn't find you in the server. "
            "Make sure you're a member of the guild."
        )
        return

    max_slots = max_friends_for_member(member)
    if max_slots == 0:
        await message.channel.send(
            "You don't have a role that allows friend whitelist slots. "
            "Contact staff if you believe this is an error."
        )
        return

    lower = content.lower()

    # !help -- show available commands
    if lower in ("!help", "help", "!commands", "commands"):
        current = get_friend_count(str(message.author.id))
        await message.channel.send(
            f"{_HELP_TEXT}"
            f"You have **{max_slots - current}** of **{max_slots}** slots remaining."
        )
        return

    # !friends -- list current friends
    if lower == "!friends":
        entries = get_friend_entries(str(message.author.id))
        if not entries:
            await message.channel.send(
                f"You have no friends added. You can add up to **{max_slots}**.\n"
                "Send a **Steam ID** (17 digits) or **EOS ID** (32 chars) to add one.\n\n"
                "Type `!help` for a full list of commands."
            )
            return
        lines = [f"**Your friends** ({len(entries)}/{max_slots}):"]
        for i, e in enumerate(entries, 1):
            label = f" -- {e['label']}" if e.get("label") else ""
            lines.append(f"`{i}.` `{e['steam_or_eos']}`{label}")
        lines.append(f"\n**{max_slots - len(entries)}** slot(s) remaining. "
                      "Type `!help` for commands.")
        await message.channel.send("\n".join(lines))
        return

    # !remove <id> -- remove a friend
    if lower.startswith("!remove"):
        parts = content.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.channel.send(
                "You need to specify which ID to remove.\n\n"
                "**Usage:** `!remove <steam_or_eos_id>`\n"
                "**Example:** `!remove 76561198140219287`\n\n"
                "Type `!friends` to see your current list."
            )
            return
        target_id = parts[1].strip()
        if not (is_valid_steamid(target_id) or is_valid_eosid(target_id)):
            await message.channel.send(
                f"`{target_id}` doesn't look like a valid Steam ID (17 digits) "
                "or EOS ID (32 characters).\n\n"
                "**Usage:** `!remove <steam_or_eos_id>`\n"
                "Type `!friends` to see your current IDs."
            )
            return
        if remove_friend_entry(str(message.author.id), target_id):
            await write_admins_cfg()
            logger.info("Friend removed via DM by %s: %s", message.author, target_id)
            current = get_friend_count(str(message.author.id))
            await message.channel.send(
                f"Removed `{target_id}` from your friend list. "
                f"**{max_slots - current}** slot(s) now available."
            )
        else:
            await message.channel.send(
                f"`{target_id}` was not found in your friend list.\n"
                "Type `!friends` to see your current entries."
            )
        return

    # Catch other !commands that aren't recognized
    if lower.startswith("!"):
        await message.channel.send(
            f"Unknown command: `{content.split()[0]}`\n\n{_HELP_TEXT}"
        )
        return

    # add a friend (raw ID, optionally followed by a label)
    parts = content.split(maxsplit=1)
    candidate_id = parts[0]
    label = parts[1] if len(parts) > 1 else ""

    if not (is_valid_steamid(candidate_id) or is_valid_eosid(candidate_id)):
        # Try to give specific advice based on what they sent
        advice = ""
        stripped = re.sub(r"[^a-zA-Z0-9]", "", candidate_id)
        if stripped.isdigit() and len(stripped) != 17:
            advice = (
                f"Your input has **{len(stripped)}** digits, but a Steam ID "
                "must be exactly **17** digits.\n\n"
            )
        elif len(stripped) > 0 and not stripped.isdigit() and len(stripped) != 32:
            advice = (
                f"Your input is **{len(stripped)}** characters, but an EOS ID "
                "must be exactly **32** alphanumeric characters.\n\n"
            )

        await message.channel.send(
            f"I couldn't recognise that as a valid ID.\n\n"
            f"{advice}"
            "A **Steam ID** is exactly **17 digits** (e.g. `76561198140219287`).\n"
            "An **EOS ID** is exactly **32 alphanumeric characters**.\n\n"
            f"{_HELP_TEXT}"
        )
        return

    current_count = get_friend_count(str(message.author.id))
    if current_count >= max_slots:
        await message.channel.send(
            f"You've used all **{max_slots}** friend slots.\n"
            "Remove one first with `!remove <id>` to free up a slot.\n"
            "Type `!friends` to see your current list."
        )
        return

    if add_friend_entry(str(message.author.id), candidate_id, label):
        await write_admins_cfg()
        remaining = max_slots - current_count - 1
        logger.info(
            "Friend added via DM by %s: %s (label=%s)",
            message.author, candidate_id, label,
        )
        label_note = f" with label **{label}**" if label else ""
        await message.channel.send(
            f"Added `{candidate_id}`{label_note} to your friend whitelist.\n"
            f"**{remaining}** slot(s) remaining.\n\n"
            "Type `!friends` to see your list or `!help` for all commands."
        )
    else:
        await message.channel.send(
            f"`{candidate_id}` is already in your friend list.\n"
            "Type `!friends` to see your current entries."
        )


@client.event
async def on_message(message: discord.Message):

    # Ignore bot messages
    if message.author.bot:
        return

    # -- DM handling (friend whitelist) --
    if message.guild is None:
        await _handle_dm(message)
        return

    content = message.content.strip().lower()

    # ----------------------------------
    # COMMAND HANDLING (NO EARLY RETURN)
    # ----------------------------------
    if content == "!practice":
        await message.channel.send("Im done with the practices.")
        logger.info(f"!practice triggered by {message.author}")
        # IMPORTANT: no return -- allow sticky logic to run

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
