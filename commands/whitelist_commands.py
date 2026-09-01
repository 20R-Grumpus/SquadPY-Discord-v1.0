"""Whitelist slash commands: /link, /overwritelink, /unlink, /whitelist, /whiteliststatus."""

import re
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands

from config import (
    logger,
    WHITELIST_ALLOWED_ROLE_IDS,
    WHITELIST_DEFAULT_GROUP,
    WHITELIST_GROUP_PERMS,
)
from bot import tree
from utils.validation import is_valid_steamid, is_valid_eosid
from features.whitelist import (
    get_linked_id,
    set_linked_id,
    delete_linked_id,
    add_whitelist_entry,
    list_whitelist_entries,
    write_admins_cfg,
)


# -- role check for whitelist-staff commands --------------------------------

def _has_whitelist_role(interaction: discord.Interaction) -> bool:
    user_roles = {role.id for role in interaction.user.roles}
    return bool(user_roles & WHITELIST_ALLOWED_ROLE_IDS)


def requires_whitelist_roles():
    async def predicate(interaction: discord.Interaction) -> bool:
        return _has_whitelist_role(interaction)
    return app_commands.check(predicate)


# -- helpers ----------------------------------------------------------------

_DURATION_RE = re.compile(
    r"(?:(\d+)\s*d(?:ays?)?)?\s*"
    r"(?:(\d+)\s*h(?:ours?)?)?\s*"
    r"(?:(\d+)\s*m(?:in(?:utes?)?)?)?",
    re.IGNORECASE,
)


def _parse_duration(text: str) -> timedelta | None:
    m = _DURATION_RE.fullmatch(text.strip())
    if not m or not any(m.groups()):
        return None
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    delta = timedelta(days=days, hours=hours, minutes=minutes)
    return delta if delta.total_seconds() > 0 else None


def _classify_id(value: str) -> str:
    if is_valid_steamid(value):
        return "steam"
    if is_valid_eosid(value):
        return "eos"
    return "invalid"


# -- /link ------------------------------------------------------------------

@tree.command(
    name="link",
    description="Link your Steam or EOS ID to your Discord account (one-time use)",
)
@app_commands.describe(game_id="Your 17-digit Steam ID or 32-character EOS ID")
async def link(interaction: discord.Interaction, game_id: str):
    await interaction.response.defer(ephemeral=True)

    id_type = _classify_id(game_id)
    if id_type == "invalid":
        await interaction.followup.send(
            "Invalid ID. Provide a 17-digit Steam ID or 32-character EOS ID.",
            ephemeral=True,
        )
        return

    existing = get_linked_id(str(interaction.user.id))
    if existing:
        await interaction.followup.send(
            "You already have a linked ID. Contact staff if you need it changed.",
            ephemeral=True,
        )
        return

    steam = game_id if id_type == "steam" else None
    eos = game_id if id_type == "eos" else None

    set_linked_id(
        discord_id=str(interaction.user.id),
        steam_id=steam,
        eos_id=eos,
        display_name=interaction.user.display_name,
        linked_by=str(interaction.user.id),
    )

    logger.info("/link by %s -- %s=%s", interaction.user, id_type, game_id)
    await interaction.followup.send(
        f"Linked your {id_type.upper()} ID `{game_id}` to your Discord account.",
        ephemeral=True,
    )


# -- /overwritelink ---------------------------------------------------------

@tree.command(
    name="overwritelink",
    description="[Staff] Overwrite a member's linked Steam or EOS ID",
)
@requires_whitelist_roles()
@app_commands.describe(
    member="The Discord member whose link to set",
    game_id="17-digit Steam ID or 32-character EOS ID",
)
async def overwritelink(
    interaction: discord.Interaction,
    member: discord.Member,
    game_id: str,
):
    await interaction.response.defer(ephemeral=True)

    id_type = _classify_id(game_id)
    if id_type == "invalid":
        await interaction.followup.send(
            "Invalid ID. Provide a 17-digit Steam ID or 32-character EOS ID.",
            ephemeral=True,
        )
        return

    steam = game_id if id_type == "steam" else None
    eos = game_id if id_type == "eos" else None

    set_linked_id(
        discord_id=str(member.id),
        steam_id=steam,
        eos_id=eos,
        display_name=member.display_name,
        linked_by=str(interaction.user.id),
        overwrite=True,
    )

    logger.info(
        "/overwritelink by %s -- target=%s %s=%s",
        interaction.user, member, id_type, game_id,
    )
    await interaction.followup.send(
        f"Set {member.mention}'s {id_type.upper()} ID to `{game_id}`.",
        ephemeral=True,
    )


# -- /unlink ----------------------------------------------------------------

@tree.command(
    name="unlink",
    description="[Staff] Remove a member's linked Steam/EOS ID",
)
@requires_whitelist_roles()
@app_commands.describe(member="The Discord member to unlink")
async def unlink(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)

    deleted = delete_linked_id(str(member.id), removed_by=str(interaction.user.id))
    if deleted:
        logger.info("/unlink by %s -- target=%s", interaction.user, member)
        await interaction.followup.send(
            f"Removed linked ID for {member.mention}.", ephemeral=True,
        )
    else:
        await interaction.followup.send(
            f"{member.mention} has no linked ID.", ephemeral=True,
        )


# -- /whitelist -------------------------------------------------------------

@tree.command(
    name="whitelist",
    description="[Staff] Whitelist a player by name + Steam/EOS ID",
)
@requires_whitelist_roles()
@app_commands.describe(
    name="In-game player name",
    game_id="17-digit Steam ID or 32-character EOS ID",
    duration='How long the entry lasts, e.g. "7d", "30d", "12h" (omit for permanent)',
    group="Permission group (defaults to config default)",
)
async def whitelist_cmd(
    interaction: discord.Interaction,
    name: str,
    game_id: str,
    duration: str = "",
    group: str = "",
):
    await interaction.response.defer(ephemeral=True)

    id_type = _classify_id(game_id)
    if id_type == "invalid":
        await interaction.followup.send(
            "Invalid ID. Provide a 17-digit Steam ID or 32-character EOS ID.",
            ephemeral=True,
        )
        return

    group_name = group or WHITELIST_DEFAULT_GROUP
    if group_name not in WHITELIST_GROUP_PERMS and WHITELIST_GROUP_PERMS:
        available = ", ".join(WHITELIST_GROUP_PERMS.keys())
        await interaction.followup.send(
            f"Unknown group `{group_name}`. Available: {available}",
            ephemeral=True,
        )
        return

    expires_at = None
    delta = None
    if duration:
        delta = _parse_duration(duration)
        if delta is None:
            await interaction.followup.send(
                "Invalid duration format. Examples: `7d`, `30d`, `12h`, `1d6h`.",
                ephemeral=True,
            )
            return
        expires_at = (datetime.now(timezone.utc) + delta).isoformat()

    add_whitelist_entry(
        steam_or_eos=game_id,
        name=name,
        group_name=group_name,
        added_by=str(interaction.user),
        expires_at=expires_at,
    )

    await write_admins_cfg()

    if delta:
        expire_ts = int((datetime.now(timezone.utc) + delta).timestamp())
        expire_text = f"expires <t:{expire_ts}:R>"
    else:
        expire_text = "permanent"

    logger.info(
        "/whitelist by %s -- name=%s id=%s group=%s %s",
        interaction.user, name, game_id, group_name, expire_text,
    )
    await interaction.followup.send(
        f"Whitelisted **{name}** (`{game_id}`) in group "
        f"**{group_name}** -- {expire_text}.",
        ephemeral=True,
    )


# -- /whiteliststatus -------------------------------------------------------

@tree.command(
    name="whiteliststatus",
    description="Show your linked ID or current whitelist entries",
)
async def whiteliststatus(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    linked = get_linked_id(str(interaction.user.id))
    parts: list[str] = []

    if linked:
        sid = linked.get("steam_id") or "--"
        eid = linked.get("eos_id") or "--"
        parts.append(f"**Your linked IDs**\nSteam: `{sid}`\nEOS: `{eid}`")
    else:
        parts.append("You have no linked ID. Use `/link` to set one.")

    if _has_whitelist_role(interaction):
        entries = list_whitelist_entries()
        parts.append(f"\n**Whitelist entries**: {len(entries)}")

    await interaction.followup.send("\n".join(parts), ephemeral=True)


# -- error handlers ---------------------------------------------------------

@overwritelink.error
@unlink.error
@whitelist_cmd.error
async def _wl_role_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.CheckFailure):
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "You do not have permission to use this command.", ephemeral=True,
            )
    else:
        logger.exception("Whitelist command error: %s", error)
