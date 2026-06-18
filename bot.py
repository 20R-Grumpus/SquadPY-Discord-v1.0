"""Discord client and command tree setup.

This module owns the shared `client` and `tree` objects. It intentionally has no
project-level imports so that every other module can depend on it without
creating import cycles.
"""

import discord

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)
