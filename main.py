"""Entry point for the seeding bot.

Importing the command and event modules registers the slash commands and event
listeners on the shared client/tree via import side effects.
"""

from config import DISCORD_TOKEN
from bot import client

import commands.seeding_commands  # noqa: F401
import commands.admin_commands  # noqa: F401
import commands.prospect_commands  # noqa: F401
import commands.util_commands  # noqa: F401
import events.handlers  # noqa: F401
import events.startup  # noqa: F401

if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
