"""Admin and banned-player database slash commands."""

import re
import sqlite3
from datetime import datetime

import discord
from discord import app_commands

from config import (
    logger,
    COMP_SFTP_SERVERS,
    SFTP_COMP_ADMIN_PATH,
    SFTP_COMP_REMOTEADMIN_PATH,
    BACKUP_DIR,
    BANNED_PLAYERS_DB_PATH,
)
from bot import tree
from utils.discord_helpers import requires_roles, format_results
from utils.sftp import sftp_modify_async
from utils.validation import extract_steamids, is_valid_steamid, is_valid_eosid
from features.admins import (
    REGION_CHOICES,
    REMOTE_LIST_URL,
    build_admins_cfg,
    get_latest_backup,
    fetch_remote_list,
)
from database import push_banned_players_to_sftp


@tree.command(name="matchconfig", description="Rewrite Admins.cfg for given SteamIDs")
@requires_roles()
@app_commands.describe(steamids="Comma or space separated SteamIDs")
@app_commands.choices(region=REGION_CHOICES)
async def matchconfig(
    interaction: "discord.Interaction", steamids: str, region: app_commands.Choice[str]
):
    await interaction.response.defer()

    ids = extract_steamids(steamids)
    if not ids:
        await interaction.followup.send("❌ No valid SteamIDs")
        return

    targets = ["NA", "EU"] if region.value == "Both" else [region.value]
    results = []

    timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    backup_name = f"match_{int(datetime.utcnow().timestamp())}"

    for key in targets:
        server = COMP_SFTP_SERVERS.get(key)
        if not server or not server["host"]:
            results.append(f"**{key}**: ❌ Config missing")
            continue

        def rewrite_admin(_):
            return build_admins_cfg(ids, role="Admin", comment="Match Config")

        # Clear remote list
        def wipe_remote(_):
            return ""

        status = await sftp_modify_async(
            server,
            {
                SFTP_COMP_ADMIN_PATH: rewrite_admin,
                SFTP_COMP_REMOTEADMIN_PATH: wipe_remote,
            },
            backup_name=backup_name,
        )

        results.append(
            f"**{key}**: {'✅ Updated' if status == 'ok' else '❌ Failed'}\n"
            f"Added IDs: {', '.join(ids)}\n"
            f"Skipped (already present): None\n"
            f"Backup created: `{backup_name}`\n"
            f"Time: {timestamp_str}"
        )

    await interaction.followup.send(format_results("📡 /matchconfig Results:", results))


@tree.command(name="resetconfig", description="Restore Admins.cfg from remote list")
@requires_roles()
@app_commands.choices(region=REGION_CHOICES)
async def resetconfig(
    interaction: "discord.Interaction", region: app_commands.Choice[str]
):
    await interaction.response.defer()

    remote_content = fetch_remote_list()
    backup_path = None

    if remote_content:
        # Save a local backup of the raw remote file
        backup_path = BACKUP_DIR / f"reset_{int(datetime.utcnow().timestamp())}.cfg"
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(remote_content)
        logger.info(f"Remote list backed up locally: {backup_path}")
    else:
        # fallback to latest local backup
        try:
            backup_path = get_latest_backup("NA")  # adjust region if needed
            with open(backup_path, "r", encoding="utf-8") as f:
                remote_content = f.read()
            logger.info(f"Using local backup: {backup_path}")
        except Exception as e:
            logger.error(f"No remote or backup available: {e}")
            await interaction.followup.send(
                "❌ Remote list unreachable and no backup available"
            )
            return

    targets = ["NA", "EU"] if region.value == "Both" else [region.value]
    results = []

    for key in targets:
        server = COMP_SFTP_SERVERS.get(key)
        if not server or not server["host"]:
            results.append(f"{key}: ❌ Config missing")
            continue

        # Write Admins.cfg exactly from the raw remote content or backup
        def write_admins(_):
            return remote_content

        # Write RemoteAdminListHosts.cfg with the URL only
        def write_remote(_):
            return REMOTE_LIST_URL

        status = await sftp_modify_async(
            server,
            {
                SFTP_COMP_ADMIN_PATH: write_admins,
                SFTP_COMP_REMOTEADMIN_PATH: write_remote,
            },
            backup_name=f"reset_{int(datetime.utcnow().timestamp())}",
        )

        results.append(
            f"{key}: {'✅ Successfully restored' if status == 'ok' else '❌ Failed to restore'}"
        )

    # Send concise summary
    await interaction.followup.send("\n".join(results))


@tree.command(name="addcameraman", description="Add Cameraman role for given SteamIDs")
@requires_roles()
@app_commands.describe(steamids="Comma or space separated SteamIDs")
@app_commands.choices(region=REGION_CHOICES)
async def addcameraman(
    interaction: "discord.Interaction", steamids: str, region: app_commands.Choice[str]
):
    await interaction.response.defer()

    ids = extract_steamids(steamids)
    if not ids:
        await interaction.followup.send("❌ No valid SteamIDs")
        return

    targets = ["NA", "EU"] if region.value == "Both" else [region.value]
    results = []

    timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    backup_name = f"cameraman_{int(datetime.utcnow().timestamp())}"

    for key in targets:
        server = COMP_SFTP_SERVERS.get(key)
        if not server or not server["host"]:
            results.append(f"**{key}**: ❌ Config missing")
            continue

        added_ids = []
        skipped_ids = []

        def modify_cameraman(current_content: str) -> str:
            lines = current_content.splitlines()
            # Add group line if missing
            if not any(line.startswith("Group=Cameraman") for line in lines):
                lines.insert(1, "Group=Cameraman:cameraman,")  # after Admin header

            # Find or create cameraman section
            try:
                start_idx = lines.index("===============CameraMan===============")
            except ValueError:
                lines.append("\n===============CameraMan===============")
                start_idx = len(lines) - 1

            existing_ids = {
                m.group(1)
                for line in lines[start_idx + 1 :]
                if (m := re.match(r"Admin=(\d{17}):Cameraman", line))
            }

            for sid in ids:
                if sid not in existing_ids:
                    lines.append(
                        f"Admin={sid}:Cameraman // Added {datetime.utcnow().strftime('%Y-%m-%d')}"
                    )
                    added_ids.append(sid)
                else:
                    skipped_ids.append(sid)

            return "\n".join(lines) + "\n"

        status = await sftp_modify_async(
            server, {SFTP_COMP_ADMIN_PATH: modify_cameraman}, backup_name=backup_name
        )

        results.append(
            f"**{key}**: {'✅ Updated' if status == 'ok' else '❌ Failed'}\n"
            f"Added IDs: {', '.join(added_ids) if added_ids else 'None'}\n"
            f"Skipped (already present): {', '.join(skipped_ids) if skipped_ids else 'None'}\n"
            f"Backup created: `{backup_name}`\n"
            f"Time: {timestamp_str}"
        )

    await interaction.followup.send(
        format_results("📡 /addcameraman Results:", results)
    )


@tree.command(
    name="removefromdb",
    description="Remove a player from the banned players database by SteamID or EOSID",
)
@requires_roles()
@app_commands.describe(player_id="SteamID (17 digits) or EOSID (32 characters)")
async def removefromdb(interaction: discord.Interaction, player_id: str):
    """Remove a player from banned players list by SteamID or EOSID"""
    await interaction.response.defer()

    try:
        # Check if player exists in database
        conn = sqlite3.connect(BANNED_PLAYERS_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) as count FROM banned_players")
        total_players = cursor.fetchone()["count"]

        if total_players == 0:
            conn.close()
            await interaction.followup.send("❌ Banned players database is empty")
            return

        # Find the player to remove
        cursor.execute(
            "SELECT * FROM banned_players WHERE steamid = ? OR eosid = ?",
            (player_id, player_id),
        )
        player_found = cursor.fetchone()

        if player_found is None:
            conn.close()
            await interaction.followup.send(
                f"❌ Player with ID `{player_id}` not found in database"
            )
            conn.close()
            return

        # Convert to dict for the response
        removed_player = dict(player_found)

        # Delete the player
        cursor.execute(
            "DELETE FROM banned_players WHERE steamid = ? OR eosid = ?",
            (player_id, player_id),
        )
        conn.commit()

        # Get remaining count
        cursor.execute("SELECT COUNT(*) as count FROM banned_players")
        remaining_count = cursor.fetchone()["count"]
        conn.close()

        # Push updated list to SFTP
        if not await push_banned_players_to_sftp():
            await interaction.followup.send(
                "⚠️ Removed from database but failed to push to SFTP"
            )
            return

        # Success response
        response = f"✅ Successfully removed player from banned players database:\n"
        if removed_player.get("name"):
            response += f"• **Name**: {removed_player['name']}\n"
        if removed_player.get("steamid"):
            response += f"• **SteamID**: `{removed_player['steamid']}`\n"
        if removed_player.get("eosid"):
            response += f"• **EOSID**: `{removed_player['eosid']}`\n"
        response += f"\n**Remaining banned players**: {remaining_count}"

        await interaction.followup.send(response)
        logger.info(
            f"Removed player {player_id} from banned database by {interaction.user}: {removed_player}"
        )

    except Exception as e:
        logger.error(f"Error removing player from database: {e}", exc_info=True)
        await interaction.followup.send(f"❌ Error: {str(e)}")


@tree.command(
    name="addtodb",
    description="Add a player to the banned players database",
)
@requires_roles()
@app_commands.describe(
    name="Player name",
    steamid="SteamID (17 digits)",
    eosid="EOSID (32 characters, optional)",
)
async def addtodb(
    interaction: discord.Interaction, name: str, steamid: str, eosid: str = None
):
    """Add a player to banned players list"""
    await interaction.response.defer()

    try:
        # Validate SteamID format (17 digits)
        if not is_valid_steamid(steamid):
            await interaction.followup.send(
                f"❌ Invalid SteamID format. Must be 17 digits, got: `{steamid}`"
            )
            return

        # Validate EOSID format if provided (32 characters)
        if eosid and not is_valid_eosid(eosid):
            await interaction.followup.send(
                f"❌ Invalid EOSID format. Must be 32 characters, got: `{eosid}`"
            )
            return

        # Create new player object
        new_player = {
            "name": name,
            "steamid": steamid,
        }

        # Add EOSID if provided
        if eosid:
            new_player["eosid"] = eosid

        # Add to database
        try:
            conn = sqlite3.connect(BANNED_PLAYERS_DB_PATH)
            cursor = conn.cursor()

            cursor.execute(
                "INSERT INTO banned_players (name, steamid, eosid) VALUES (?, ?, ?)",
                (name, steamid, eosid),
            )
            conn.commit()

            # Get total count
            cursor.execute("SELECT COUNT(*) as count FROM banned_players")
            total_count = cursor.fetchone()[0]
            conn.close()

            # Push to SFTP
            if not await push_banned_players_to_sftp():
                # Remove the player if SFTP push failed
                conn = sqlite3.connect(BANNED_PLAYERS_DB_PATH)
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM banned_players WHERE steamid = ?", (steamid,)
                )
                conn.commit()
                conn.close()
                await interaction.followup.send(
                    "❌ Failed to push to SFTP - player has been removed"
                )
                return

            # Success response
            response = f"✅ Successfully added player to banned players database:\n"
            response += f"• **Name**: {new_player['name']}\n"
            response += f"• **SteamID**: `{new_player['steamid']}`\n"
            if "eosid" in new_player:
                response += f"• **EOSID**: `{new_player['eosid']}`\n"
            response += f"\n**Total banned players**: {total_count}"

            await interaction.followup.send(response)
            logger.info(
                f"Added player to banned database by {interaction.user}: {new_player}"
            )
        except sqlite3.IntegrityError as e:
            if "steamid" in str(e):
                await interaction.followup.send(
                    f"❌ Player with SteamID `{steamid}` already in database"
                )
            elif "eosid" in str(e):
                await interaction.followup.send(
                    f"❌ Player with EOSID `{eosid}` already in database"
                )
            else:
                await interaction.followup.send(f"❌ Player already exists in database")
            logger.warning(f"Integrity error adding player: {e}")
        except Exception as e:
            logger.error(f"Error adding player to database: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    except Exception as e:
        logger.error(f"Error adding player to database: {e}", exc_info=True)
        await interaction.followup.send(f"❌ Error: {str(e)}")
