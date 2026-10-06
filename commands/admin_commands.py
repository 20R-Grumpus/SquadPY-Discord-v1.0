"""Admin database slash commands."""

import re
from datetime import datetime

import discord
from discord import app_commands

from config import (
    logger,
    COMP_SFTP_SERVERS,
    SFTP_COMP_ADMIN_PATH,
    SFTP_COMP_REMOTEADMIN_PATH,
    BACKUP_DIR,
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
