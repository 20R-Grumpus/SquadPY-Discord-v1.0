"""Prospect management: kit parsing, Google Sheet roster, review UI."""

import re
import asyncio

import discord
from discord.ui import View, Button
from google.oauth2 import service_account
from googleapiclient.discovery import build

from config import (
    logger,
    GOOGLE_CREDENTIALS_FILE,
    COMP_ROSTER_SHEET_ID,
    PROSPECT_ROLE_ID,
    PROSPECT_EXCLUDE_ROLE_IDS,
    PROSPECT_ANNOUNCEMENT_CHANNEL_ID,
    PROSPECT_QUESTIONS_CHANNEL_ID,
    PROSPECT_FORM_CHANNEL_ID,
    PROSPECT_RULES_CHANNEL_ID,
    PROSPECT_INFO_CHANNEL_ID,
    PROSPECT_NICK_PREFIX,
    COMMUNITY_NAME,
)
from bot import client

KIT_OPTIONS = [
    "Commander",
    "SL",
    "Lead Crewman",
    "Lead Pilot",
    "Medic",
    "Crewman",
    "Pilot",
    "Mortar",
    "Rifleman",
    "Raider/Ambush",
    "Automatic Rifle",
    "Grenadier",
    "Light AT",
    "Marksman",
    "Infiltrator",
    "Sniper",
    "Machine Gun",
    "Heavy AT",
    "Engineer",
    "Scout",
]

# Canonical mapping
KIT_CANONICAL = {k.lower(): k for k in KIT_OPTIONS}

# Aliases → canonical
KIT_ALIASES = {
    "lat": "Light AT",
    "hat": "Heavy AT",
    "light at": "Light AT",
    "heavy at": "Heavy AT",
    "at": "Light AT",  # common shorthand, defaults to Light AT
    "marksman": "Marksman",
    "sniper": "Sniper",
    "mg": "Machine Gun",
    "machine gun": "Machine Gun",
    "ar": "Automatic Rifle",
    "automatic rifle": "Automatic Rifle",
    "gl": "Grenadier",
    "grenade launcher": "Grenadier",
    "ce": "Engineer",
    "squad leader": "SL",
    "sl": "SL",
    "crew": "Crewman",
    "rifle": "Rifleman",
    "rifleman": "Rifleman",
    "ambush": "Raider/Ambush",
    "raider": "Raider/Ambush",
}

# Unified lookup
KIT_LOOKUP = {}
KIT_LOOKUP.update(KIT_CANONICAL)
KIT_LOOKUP.update({k.lower(): v for k, v in KIT_ALIASES.items()})


def get_sheets_service():
    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_FILE, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds)


def get_sheet_tab_id(service, spreadsheet_id, tab_name="Team Roster"):
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in meta["sheets"]:
        if sheet["properties"]["title"] == tab_name:
            return sheet["properties"]["sheetId"]
    raise ValueError("Team Roster tab not found")


def find_first_empty_row(service, sheet_id):
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range="Team Roster!A:A")
        .execute()
    )

    return len(result.get("values", [])) + 1


async def get_applicant_by_steamid(channel, logger):
    messages = [m async for m in channel.history(limit=100, oldest_first=True)]

    steamid_author = None
    steamid_messages = []

    for msg in messages:
        if msg.author.bot:
            continue

        # Skip users with staff/admin roles (they cannot be prospects)
        user_role_ids = {role.id for role in msg.author.roles}
        if user_role_ids & PROSPECT_EXCLUDE_ROLE_IDS:
            logger.info(
                f"Skipping {msg.author} - has excluded role(s): {user_role_ids & PROSPECT_EXCLUDE_ROLE_IDS}"
            )
            continue

        # Clean message content
        clean_content = msg.content.replace("\u200b", "").strip()

        # Log what we see
        logger.info(f"Reading message from {msg.author}: {repr(clean_content)}")

        # Search for any 17-digit SteamID (simplified pattern since we've already filtered to only applicant messages)
        match = re.search(r"\b(\d{17})\b", clean_content)

        if match and steamid_author is None:
            # Only set author if not already found (keep first valid SteamID)
            steamid = match.group(1)
            steamid_author = msg.author  # ✅ This is the prospect
            steamid_messages.append(msg)
            logger.info(f"Found SteamID {steamid} from {msg.author}")
        elif not match:
            logger.debug(f"No SteamID in message from {msg.author}")

    if steamid_author and steamid_messages:
        logger.info(
            f"Identified prospect as {steamid_author} (author of SteamID message)"
        )
        return steamid_author, steamid_messages[0].content, steamid_messages

    logger.info("No SteamID found in the last 100 messages.")
    return None, None, []


def parse_prospect(message: str):
    steam_match = re.search(r"\b\d{17}\b", message)
    steamid = steam_match.group(0) if steam_match else "Unknown"

    tokens = re.findall(r"\b[a-zA-Z./]+\b", message.lower())

    found = []
    for token in tokens:
        role = KIT_LOOKUP.get(token)
        if role and role not in found:
            found.append(role)
        if len(found) >= 2:
            break

    primary = found[0] if len(found) >= 1 else "Unknown"
    backup = found[1] if len(found) >= 2 else "Unknown"

    return steamid, primary, backup


class RoleButton(Button):
    def __init__(self, role):
        super().__init__(label=role, style=discord.ButtonStyle.secondary)
        self.role = role

    async def callback(self, interaction: discord.Interaction):
        view: ProspectView = self.view

        if interaction.user != view.owner:
            await interaction.response.send_message(
                "❌ Not your interaction.", ephemeral=True
            )
            return

        if view.step == "primary":
            view.data["primary"] = self.role
            view.step = "backup"
            logger.info(f"Primary set to {self.role}")

        elif view.step == "backup":
            if self.role == view.data["primary"]:
                await interaction.response.send_message(
                    "❌ Cannot select same role twice.", ephemeral=True
                )
                return

            view.data["backup"] = self.role
            view.step = "confirm"
            logger.info(f"Backup set to {self.role}")

        view.update_buttons()

        await interaction.response.edit_message(content=view.build_message(), view=view)


def get_prospect_value(service):
    """Find an existing 'Prospect' cell in column H and return its exact value"""
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=COMP_ROSTER_SHEET_ID,
            range="Team Roster!H2:H100",  # check first 100 rows
        )
        .execute()
    )
    values = result.get("values", [])
    for row in values:
        if row and row[0].strip().lower() == "prospect":
            return row[0]  # exact string from sheet
    return "Prospect"  # fallback if none found


def copy_prospect_cell(service, target_row):
    """
    Find a cell in column H with text 'Prospect' and copy it to target_row
    preserving formatting and dropdown if possible.
    """
    sheet_id = get_sheet_tab_id(service, COMP_ROSTER_SHEET_ID)

    # --- Step 1: Find the source row by text ---
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=COMP_ROSTER_SHEET_ID, range="Team Roster!H2:H100")
        .execute()
    )
    values = result.get("values", [])

    source_row = None
    for i, row in enumerate(values, start=2):  # H2 = row 2
        if row and row[0].strip().lower() == "prospect":
            source_row = i
            break

    if source_row is None:
        # fallback: write plain text
        service.spreadsheets().values().update(
            spreadsheetId=COMP_ROSTER_SHEET_ID,
            range=f"Team Roster!H{target_row}",
            valueInputOption="RAW",
            body={"values": [["Prospect"]]},
        ).execute()
        logger.warning(
            f"No existing 'Prospect' cell found. Wrote plain text to H{target_row}."
        )
        return

    # --- Step 2: Copy the cell properly ---
    requests = [
        {
            "copyPaste": {
                "source": {
                    "sheetId": sheet_id,
                    "startRowIndex": source_row - 1,
                    "endRowIndex": source_row,
                    "startColumnIndex": 7,  # H = 7 (0-indexed)
                    "endColumnIndex": 8,
                },
                "destination": {
                    "sheetId": sheet_id,
                    "startRowIndex": target_row - 1,
                    "endRowIndex": target_row,
                    "startColumnIndex": 7,
                    "endColumnIndex": 8,
                },
                "pasteType": "PASTE_NORMAL",
                "pasteOrientation": "NORMAL",
            }
        }
    ]

    service.spreadsheets().batchUpdate(
        spreadsheetId=COMP_ROSTER_SHEET_ID, body={"requests": requests}
    ).execute()
    logger.info(f"Copied 'Prospect' cell from H{source_row} to H{target_row}.")


class ProspectView(View):
    def __init__(self, owner, data, applicant=None):
        super().__init__(timeout=300)
        self.owner = owner
        self.data = data
        self.applicant = applicant
        self.step = self.get_initial_step()

        for role in KIT_OPTIONS:
            self.add_item(RoleButton(role))

        self.add_item(self.ConfirmButton())
        self.add_item(self.CancelButton())

        self.update_buttons()

    def get_initial_step(self):
        if self.data["primary"] == "Unknown":
            return "primary"
        elif self.data["backup"] == "Unknown":
            return "backup"
        return "confirm"

    def build_message(self):
        prompt = {
            "primary": "Select PRIMARY role",
            "backup": "Select BACKUP role",
            "confirm": "Ready to confirm",
        }[self.step]

        return (
            f"📋 **Review Prospect**\n"
            f"Name: {self.data['name']}\n"
            f"SteamID: {self.data['steamid']}\n"
            f"Primary: {self.data['primary']}\n"
            f"Backup: {self.data['backup']}\n\n"
            f"{prompt}"
        )

    def update_buttons(self):
        for item in self.children:
            if isinstance(item, Button) and item.label == "Confirm":
                item.disabled = self.step != "confirm"

    async def write_to_sheet(self):
        service = get_sheets_service()

        # --- Step 1: Get current roster to find row ---
        result = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=COMP_ROSTER_SHEET_ID,
                range="Team Roster!A:D",  # adjust to include Name + SteamID
            )
            .execute()
        )

        values = result.get("values", [])

        # --- Step 2: Find row by SteamID if it exists ---
        row_index = None
        for i, row in enumerate(values, start=1):  # Google Sheets is 1-indexed
            if len(row) >= 4 and row[3] == self.data["steamid"]:  # Column D = SteamID
                row_index = i
                break

        # If not found, append at bottom
        if row_index is None:
            row_index = len(values) + 1

        # --- Step 3: Write basic values for name, SteamID, roles ---
        body = {
            "valueInputOption": "RAW",
            "data": [
                {"range": f"Team Roster!A{row_index}", "values": [[self.data["name"]]]},
                {
                    "range": f"Team Roster!D{row_index}",
                    "values": [[self.data["steamid"]]],
                },
                {
                    "range": f"Team Roster!L{row_index}",
                    "values": [[self.data["primary"]]],
                },
                {
                    "range": f"Team Roster!M{row_index}",
                    "values": [[self.data["backup"]]],
                },
            ],
        }

        service.spreadsheets().values().batchUpdate(
            spreadsheetId=COMP_ROSTER_SHEET_ID, body=body
        ).execute()

        # --- Step 4: Copy the "Prospect" cell to column H ---
        copy_prospect_cell(service, target_row=row_index)

        logger.info(f"Sheet write complete (row {row_index})")

    class ConfirmButton(Button):
        def __init__(self):
            super().__init__(label="Confirm", style=discord.ButtonStyle.green)

        async def callback(self, interaction: discord.Interaction):
            view: ProspectView = self.view

            if interaction.user != view.owner:
                await interaction.response.send_message(
                    "❌ Not your interaction.", ephemeral=True
                )
                return

            # Defer interaction (required for long processing)
            await interaction.response.defer()

            # Disable buttons immediately
            for item in view.children:
                item.disabled = True

            await interaction.message.edit(content="⏳ Adding prospect...", view=view)

            # Write to Google Sheet
            await view.write_to_sheet()

            # --- Role + nickname assignment ---
            if view.applicant:
                try:
                    guild = interaction.guild
                    role = guild.get_role(PROSPECT_ROLE_ID)

                    if not role:
                        logger.warning("Prospect role not found")
                    else:
                        member = guild.get_member(view.applicant.id)

                        # Fallback to API fetch if not cached
                        if member is None:
                            try:
                                member = await guild.fetch_member(view.applicant.id)
                                logger.info(f"Fetched member from API: {member}")
                            except discord.NotFound:
                                logger.error(
                                    f"Member {view.applicant} not found in guild"
                                )
                                member = None
                            except Exception as e:
                                logger.error(f"Failed to fetch member: {e}")
                                member = None

                        if member:
                            # Assign role
                            try:
                                await member.add_roles(role)
                                logger.info(f"Assigned role {role.name} to {member}")
                            except discord.Forbidden:
                                logger.error(
                                    f"No permission to assign role to {member}"
                                )
                            except Exception as e:
                                logger.error(f"Failed to assign role: {e}")

                            # Nickname update
                            try:
                                prefix = PROSPECT_NICK_PREFIX
                                base_name = member.display_name

                                # Skip when no prefix configured or already applied
                                if prefix and not base_name.startswith(prefix):
                                    new_nick = f"{prefix}{base_name}"

                                    # Enforce Discord limit
                                    if len(new_nick) > 32:
                                        new_nick = new_nick[:32]

                                    await member.edit(nick=new_nick)
                                    logger.info(
                                        f"Updated nickname for {member} -> {new_nick}"
                                    )

                            except discord.Forbidden:
                                logger.warning(
                                    f"No permission / hierarchy issue for nickname change: {member}"
                                )
                            except Exception as e:
                                logger.error(
                                    f"Failed to change nickname for {member}: {e}"
                                )

                    # Give Discord a moment before sending message
                    await asyncio.sleep(1.5)

                    # Welcome message
                    welcome_msg = (
                        f"Welcome to {COMMUNITY_NAME} {view.applicant.mention}! If you have questions, "
                        f"feel free to ask in <#{PROSPECT_QUESTIONS_CHANNEL_ID}>. When you get a chance, fill out "
                        f"<#{PROSPECT_FORM_CHANNEL_ID}> please! and please read <#{PROSPECT_RULES_CHANNEL_ID}> and "
                        f"<#{PROSPECT_INFO_CHANNEL_ID}> when you get a second! Let us know when you have read "
                        f"this ticket so we can close it!"
                    )
                    await interaction.channel.send(welcome_msg)

                    logger.info(f"Posted welcome message for {view.applicant}")

                    # Post announcement to prospect announcement channel
                    try:
                        announcement_channel = client.get_channel(
                            PROSPECT_ANNOUNCEMENT_CHANNEL_ID
                        )
                        if announcement_channel:
                            prospect_mention = f"<@{view.applicant.id}>"
                            announcement_msg = f"Everyone welcome {prospect_mention} as our newest prospect!"
                            await announcement_channel.send(announcement_msg)
                            logger.info(
                                f"Posted prospect announcement for {view.applicant} (ID: {view.applicant.id}) in channel {PROSPECT_ANNOUNCEMENT_CHANNEL_ID}"
                            )
                        else:
                            logger.warning(
                                f"Prospect announcement channel {PROSPECT_ANNOUNCEMENT_CHANNEL_ID} not found"
                            )
                    except Exception as e:
                        logger.error(f"Failed to post prospect announcement: {e}")

                except Exception as e:
                    logger.error(f"Error assigning role or posting message: {e}")

            # Final update
            await interaction.message.edit(content="✅ Prospect added.", view=None)

    class CancelButton(Button):
        def __init__(self):
            super().__init__(label="Cancel", style=discord.ButtonStyle.red)

        async def callback(self, interaction: discord.Interaction):
            view: ProspectView = self.view

            if interaction.user != view.owner:
                await interaction.response.send_message(
                    "❌ Not your interaction.", ephemeral=True
                )
                return

            await interaction.response.defer()

            for item in view.children:
                item.disabled = True

            await interaction.message.edit(content="❌ Cancelled.", view=view)
