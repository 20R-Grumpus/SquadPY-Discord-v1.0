"""Prospect slash commands."""

import discord

from config import logger
from bot import tree
from utils.discord_helpers import requires_roles
from features.prospects import get_applicant_by_steamid, parse_prospect, ProspectView


@tree.command(name="addprospect", description="Review and add a prospect")
@requires_roles()
async def addprospect(
    interaction: discord.Interaction,
    name: str = None,
    steamid: str = None,
    primary_kit: str = None,
    backup_kit: str = None,
):
    await interaction.response.defer()

    logger.info(f"/addprospect triggered by {interaction.user}")

    applicant, detected_steam, msgs = await get_applicant_by_steamid(
        interaction.channel, logger
    )

    if not applicant:
        await interaction.followup.send("❌ No SteamID found.")
        return

    combined = "\n".join(m.content for m in msgs)
    parsed_steam, parsed_primary, parsed_backup = parse_prospect(combined)

    data = {
        "name": name or applicant.display_name,
        "steamid": steamid or detected_steam or parsed_steam,
        "primary": primary_kit or parsed_primary,
        "backup": backup_kit or parsed_backup,
    }

    logger.info(f"Parsed data: {data}")

    view = ProspectView(interaction.user, data, applicant=applicant)

    await interaction.followup.send(content=view.build_message(), view=view)
