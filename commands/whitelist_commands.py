"""Discord commands for managing Whitelister entries."""

import json

import discord
from discord import app_commands

from bot import tree
from config import ALLOWED_ROLE_IDS, logger
from features.whitelist import (
    WhitelistAPIError,
    add_whitelist_entry,
    get_available_entries,
    get_available_groups,
    get_main_list_id,
    refresh_available_groups,
    refresh_available_entries,
    remove_whitelist_entry,
    send_whitelist_audit,
)
from utils.discord_helpers import requires_roles
from utils.validation import is_valid_eosid, is_valid_steamid

IDENTIFIER_CHOICES = [
    app_commands.Choice(name="SteamID", value="steamid64"),
    app_commands.Choice(name="EOSID", value="eosID"),
]
DURATION_CHOICES = [
    app_commands.Choice(name="Permanent", value="permanent"),
    app_commands.Choice(name="Days", value="days"),
    app_commands.Choice(name="Months", value="months"),
]
DISCORD_MESSAGE_LIMIT = 1900


def _has_allowed_role(interaction: discord.Interaction) -> bool:
    roles = getattr(interaction.user, "roles", ())
    return any(role.id in ALLOWED_ROLE_IDS for role in roles)


def _duration_hours(duration_type, amount):
    if duration_type == "permanent":
        if amount is not None:
            raise ValueError("Permanent whitelists do not take a duration amount.")
        return 0, "Permanent"
    if amount is None or amount < 1:
        raise ValueError("Enter a duration amount greater than zero.")
    if duration_type == "days":
        return amount * 24, f"{amount} day(s)"
    if duration_type == "months":
        return amount * 30 * 24, f"{amount} month(s) (30 days each)"
    raise ValueError("Choose Permanent, Days, or Months.")


def _format_api_result(status, payload):
    if isinstance(payload, str):
        result = payload
    else:
        result = json.dumps(payload, indent=2, ensure_ascii=False)
    return f"Whitelister API response (HTTP {status}):\n{result}"


async def _send_public_result(interaction, message):
    for offset in range(0, len(message), DISCORD_MESSAGE_LIMIT):
        await interaction.followup.send(
            message[offset : offset + DISCORD_MESSAGE_LIMIT],
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def group_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None or not _has_allowed_role(interaction):
        return []
    groups = get_available_groups()
    if not groups:
        await refresh_available_groups()
        groups = get_available_groups()
    query = current.casefold()
    return [
        app_commands.Choice(name=group["name"], value=group["name"])
        for group in groups
        if query in group["name"].casefold()
    ][:25]


@tree.command(name="addwl", description="Add a player to the Main whitelist")
@app_commands.guild_only()
@requires_roles()
@app_commands.choices(
    id_type=IDENTIFIER_CHOICES,
    duration_type=DURATION_CHOICES,
)
@app_commands.autocomplete(group=group_autocomplete)
@app_commands.describe(
    username="Player username",
    id_type="Whether the identifier is a SteamID or EOSID",
    identifier="The selected SteamID or EOSID",
    group="Whitelist group (groups with the ban permission are excluded)",
    duration_type="Permanent, Days, or Months",
    amount="Required for Days/Months; leave empty for Permanent",
)
async def addwl(
    interaction: discord.Interaction,
    username: str,
    id_type: app_commands.Choice[str],
    identifier: str,
    group: str,
    duration_type: app_commands.Choice[str],
    amount: int | None = None,
):
    await interaction.response.defer()
    actor = f"{interaction.user} ({interaction.user.id})"
    username = username.strip()
    identifier = identifier.strip()
    if not username:
        await interaction.followup.send("Username cannot be empty.")
        return

    validator = (
        is_valid_steamid
        if id_type.value == "steamid64"
        else is_valid_eosid
    )
    if not validator(identifier):
        expected = "17-digit SteamID" if id_type.value == "steamid64" else "32-character EOSID"
        message = f"Invalid identifier: expected a {expected}."
        await send_whitelist_audit(
            f"Failed /addwl attempt by {actor}: username={username!r}; "
            f"type={id_type.name}; reason={message}"
        )
        await interaction.followup.send(message)
        return

    try:
        duration_hours, duration_label = _duration_hours(
            duration_type.value, amount
        )
    except ValueError as exc:
        await send_whitelist_audit(
            f"Failed /addwl attempt by {actor}: username={username!r}; "
            f"reason={exc}"
        )
        await interaction.followup.send(str(exc))
        return

    if not await refresh_available_groups():
        message = "Could not refresh whitelist groups; no change was made. Try again shortly."
        await send_whitelist_audit(
            f"Failed /addwl attempt by {actor}: username={username!r}; "
            "reason=group refresh failed"
        )
        await interaction.followup.send(message)
        return

    selected_group = next(
        (
            item
            for item in get_available_groups()
            if item["name"] == group
        ),
        None,
    )
    if selected_group is None:
        message = "That group is unavailable or has the ban permission; choose another group."
        await send_whitelist_audit(
            f"Failed /addwl attempt by {actor}: username={username!r}; "
            f"group={group!r}; reason=group unavailable"
        )
        await interaction.followup.send(message)
        return

    try:
        list_id = await get_main_list_id()
        status, payload = await add_whitelist_entry(
            username=username,
            identifier=identifier,
            identifier_type=id_type.value,
            group_id=selected_group["id"],
            duration_hours=duration_hours,
            discord_username=interaction.user.name,
            list_id=list_id,
        )
    except WhitelistAPIError as exc:
        message = f"Whitelist API request failed: {exc}"
        logger.error("/addwl failed for user %s: %s", actor, exc)
        await send_whitelist_audit(
            f"Failed /addwl by {actor}: username={username!r}; "
            f"type={id_type.name}; identifier={identifier}; "
            f"group={group}; duration={duration_label}; error={exc}"
        )
        await interaction.followup.send(message)
        return

    success = 200 <= status < 300
    await send_whitelist_audit(
        f"{'Successful' if success else 'Failed'} /addwl by {actor}: "
        f"username={username!r}; type={id_type.name}; identifier={identifier}; "
        f"group={group}; duration={duration_label}; HTTP {status}"
    )
    await _send_public_result(interaction, _format_api_result(status, payload))


@tree.command(
    name="removewl",
    description="Remove a player's reserve-only whitelist entries",
)
@app_commands.guild_only()
@requires_roles()
@app_commands.choices(id_type=IDENTIFIER_CHOICES)
@app_commands.describe(
    id_type="Whether the identifier is a SteamID or EOSID",
    identifier="The SteamID or EOSID to remove from reserve-only groups",
)
async def removewl(
    interaction: discord.Interaction,
    id_type: app_commands.Choice[str],
    identifier: str,
):
    await interaction.response.defer()
    actor = f"{interaction.user} ({interaction.user.id})"
    identifier = identifier.strip()
    validator = (
        is_valid_steamid
        if id_type.value == "steamid64"
        else is_valid_eosid
    )
    if not validator(identifier):
        expected = (
            "17-digit SteamID"
            if id_type.value == "steamid64"
            else "32-character EOSID"
        )
        message = f"Invalid identifier: expected a {expected}."
        await send_whitelist_audit(
            f"Failed /removewl attempt by {actor}: type={id_type.name}; "
            f"reason={message}"
        )
        await interaction.followup.send(message)
        return

    groups_refreshed = await refresh_available_groups()
    entries_refreshed = await refresh_available_entries()
    if not groups_refreshed or not entries_refreshed:
        message = (
            "Could not refresh whitelist groups or entries; no change was made. "
            "Try again shortly."
        )
        await send_whitelist_audit(
            f"Failed /removewl attempt by {actor}: type={id_type.name}; "
            f"identifier={identifier}; reason=refresh failed"
        )
        await interaction.followup.send(message)
        return

    eligible_group_ids = {
        group["id"] for group in get_available_groups()
    }
    matching_entries = [
        entry
        for entry in get_available_entries()
        if entry.get("id_group") in eligible_group_ids
        and str(entry.get(id_type.value) or "").casefold()
        == identifier.casefold()
    ]
    if not matching_entries:
        message = (
            "No matching reserve-only whitelist entries were found in the Main list. "
            "Entries in other groups, including admin groups, were not considered."
        )
        await send_whitelist_audit(
            f"No eligible entries for /removewl by {actor}: "
            f"type={id_type.name}; identifier={identifier}"
        )
        await interaction.followup.send(message)
        return

    group_names = {
        group["id"]: group["name"] for group in get_available_groups()
    }
    results = []
    audit_results = []
    for entry in matching_entries:
        entry_id = entry["_id"]
        group_name = group_names[entry["id_group"]]
        try:
            status, payload = await remove_whitelist_entry(entry_id)
            success = 200 <= status < 300
            audit_results.append(
                f"{'Successful' if success else 'Failed'} group={group_name}; "
                f"entry_id={entry_id}; HTTP {status}"
            )
            result = _format_api_result(status, payload)
            results.append(f"Group: {group_name}\n{result}")
        except WhitelistAPIError as exc:
            logger.error(
                "/removewl failed for user %s, entry %s: %s",
                actor,
                entry_id,
                exc,
            )
            audit_results.append(
                f"Failed group={group_name}; entry_id={entry_id}; error={exc}"
            )
            results.append(
                f"Group: {group_name}\nWhitelist API request failed: {exc}"
            )

    await send_whitelist_audit(
        f"/removewl by {actor}: type={id_type.name}; identifier={identifier}; "
        f"matched {len(matching_entries)} eligible entr"
        f"{'y' if len(matching_entries) == 1 else 'ies'}\n"
        + "\n".join(audit_results)
    )
    await _send_public_result(
        interaction,
        f"Removed whitelist entries for {identifier}:\n\n"
        + "\n\n".join(results),
    )
