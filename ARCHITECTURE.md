# Architecture — SquadPY Discord

This document maps the project's file structure and lists every function and class
in each module, with a one-line description of what it does.

The bot was refactored from a single monolith (`Current Codebase/bot.py`) into the
modular package below. Modules are layered so imports flow in one direction:

```
config  ->  state  ->  utils  ->  database / features  ->  commands / events  ->  main
```

`bot.py` (the shared Discord `client` and command `tree`) sits at the bottom with no
project imports, so any module can import it without creating an import cycle.

## File tree

```
.
├── main.py                     # Entry point: loads config, registers commands/events, runs the client
├── config.py                   # All env/config loading and derived constants
├── state.py                    # Runtime BotState + unified JSON persistence store
├── bot.py                      # Shared discord.Client and CommandTree
├── database.py                 # SQLite banned-players storage
├── requirements.txt            # Python dependencies
├── env.txt.example             # Sample environment file (keys only, no secrets)
│
├── features/                   # Domain logic (no slash-command decorators)
│   ├── seeding.py              # Seeding triggers, auto-seed window, scheduling, monitor loop
│   ├── rotation.py             # Map rotation, Google Sheet parsing, join-link updates
│   ├── prospects.py            # Prospect review UI, roster sheet writes, role assignment
│   └── admins.py               # Admins.cfg generation, backups, remote admin list
│
├── commands/                   # Slash commands, grouped by domain
│   ├── seeding_commands.py     # /seedbotstart /seedbotstop /seedbotstatus /seedbothelp /seedbotreload
│   ├── admin_commands.py       # /matchconfig /resetconfig /addcameraman /removefromdb /addtodb
│   ├── prospect_commands.py    # /addprospect
│   └── util_commands.py        # /addfog /showfog /removefog /killfeed /test /seederboard /playercounts /seedtrack
│
├── utils/                      # Cross-cutting helpers
│   ├── sftp.py                 # SFTP read/write/download/modify helpers
│   ├── discord_helpers.py      # Messaging helpers, log handler, role checks
│   ├── retry.py                # HTTP session + player-count fetch with retries
│   └── validation.py           # SteamID / EOSID / layer-name validators
│
└── events/                     # Discord event listeners
    ├── handlers.py             # on_thread_create (ban evidence), on_message (sticky)
    └── startup.py              # on_ready (sync commands, start background tasks)
```

## Module reference

### `main.py`
Entry point. Imports `config` and the shared `client`, then imports every
`commands/*` and `events/*` module so their `@tree.command` / `@client.event`
decorators register against the shared tree, and finally runs the client under
`if __name__ == "__main__"`.

### `config.py`
No functions — a flat module of constants loaded from the environment (via
`python-dotenv`) plus a few derived values (`SEEDING_MESSAGE`, `milestones`,
`COMP_SFTP_SERVERS`, `WEEKLY_SHEET_IDS`). See `README.md` for every variable.

### `bot.py`
No functions — defines the shared `intents`, `client` (`discord.Client`) and
`tree` (`discord.app_commands.CommandTree`) used everywhere.

### `state.py`
- `class BotState` — holds all in-memory mutable runtime state (seeding flags,
  background-task references, rotation message/channel IDs, HTTP session, sticky
  message ID). A single instance `state` is shared across modules, replacing the
  monolith's module-level globals.
- `_read_store()` — reads the unified JSON store; on first run, migrates the legacy
  per-feature files into it.
- `_write_store(store)` — atomically writes the store (temp file + `os.replace`).
- `get_state(section, default=None)` — returns one section of the store.
- `set_state(section, data)` — replaces an entire section.
- `update_state(section, data=None, **values)` — **safely merges** keys into a
  section without clobbering other keys; the function new code should call to add
  persisted data.
- `save_state(data)` / `load_state()` — backward-compatible wrappers for the
  `seedtrack` section.
- `save_rotation_state(data)` / `load_rotation_state()` — wrappers for the
  `rotation` section.

### `database.py`
- `init_banned_players_db()` — creates the SQLite banned-players table if missing
  (called once at import).
- `load_banned_players() -> list` — loads all banned players from SQLite.
- `player_exists(banned_list, steamid) -> bool` — checks if a SteamID is present.
- `find_player_by_steamid(banned_list, steamid) -> dict | None` — looks up a record.
- `save_banned_players(banned_list) -> bool` — writes the list back to SQLite.
- `push_banned_players_to_sftp(banned_list=None) -> bool` — uploads the banned-players
  JSON to the SquadJS server over SFTP.

### `utils/sftp.py`
- `_sftp_write_content_sync(...)` — blocking SFTP upload of in-memory content.
- `sftp_write_content(...)` — async wrapper around the sync upload.
- `_sftp_download_sync(...)` — blocking SFTP download.
- `sftp_download(...)` — async wrapper around the sync download.
- `_sftp_modify(server, modify_map=None, backup_name=None)` — blocking multi-file
  edit/backup on a remote server.
- `sftp_modify_async(server, modify_map=None, backup_name=None)` — async wrapper.

### `utils/discord_helpers.py`
- `get_dynamic_message_channel_id(interaction) -> int` — returns the test channel
  when a command is run there, otherwise the default message channel.
- `class DiscordLogHandler(logging.Handler)` — logging handler that forwards log
  records to a Discord channel.
- `post_command_message(message)` — sends a message to the command channel.
- `post_message(message, interaction=None)` — posts a seeding/announcement message.
- `delete_milestone_messages()` — deletes previously posted milestone messages.
- `post_command_response(message, interaction=None)` — replies to a command.
- `requires_roles()` — slash-command check that gates commands by `ALLOWED_ROLE_IDS`.
- `format_results(header, results) -> str` — formats a list of results for display.
- `get_population_emoji(players)` — picks a population emoji for a player count.

### `utils/retry.py`
- `fetch_player_count()` — fetches the main server's player count from BattleMetrics.
- `robust_fetch_player_count(server_id, retries=2)` — player count with retries,
  returning players/max/queue.
- `init_http_session()` — creates the shared `aiohttp` session.
- `close_http_session()` — closes the shared `aiohttp` session.

### `utils/validation.py`
- `is_valid_steamid(value) -> bool` — validates a 17-digit SteamID.
- `is_valid_eosid(value) -> bool` — validates a 32-char EOSID.
- `is_valid_layer_name(value) -> bool` — validates a `Map_Mode_vX` layer name.
- `extract_steamids(text) -> list[str]` — pulls all 17-digit IDs out of text.

### `features/seeding.py`
- `is_within_auto_seed_window()` — whether the current London time is in the
  auto-seed window.
- `trigger_seeding(player_count=None)` — starts seeding and posts the seeding message.
- `reset_bot()` — clears seeding state and milestone tracking.
- `check_server()` — the monitor step: auto-triggers seeding and posts milestones.
- `schedule_seeding_at(time_str)` — schedules seeding for a given `HH:MM` London time.
- `reset_scheduled_seeding()` — cancels a scheduled seeding.
- `background_task()` — periodic loop that calls `check_server()`.

### `features/rotation.py`
- `get_row_values_from_google_sheet(...)` — reads rows from a Google Sheet tab.
- `process_rotation_sheet(rows, ignore_prefixes=...)` — formats rotation rows and
  flags fog-off layers.
- `send_rotation_update(publish=True)` — builds and posts/edits the rotation embed
  with a join-link button.
- `class RotationJoinView(discord.ui.View)` — view holding the join-link button.
- `format_join_link(join_url) -> str | None` — formats a raw join URL.
- `get_server_name_rcon() -> str | None` — fetches the current server name via RCON.
- `fetch_join_link_squadbrowser(server_name) -> str` — gets a join link from the
  squad browser API.
- `rotation_join_link_updater()` — background loop that refreshes the join link.
- `schedule_daily_rotation_update()` — schedules the daily rotation post.

### `features/prospects.py`
- `get_sheets_service()` — builds an authenticated Google Sheets client.
- `get_sheet_tab_id(service, spreadsheet_id, tab_name="Team Roster")` — resolves a
  sheet tab's numeric ID.
- `find_first_empty_row(service, sheet_id)` — finds the first empty roster row.
- `get_applicant_by_steamid(channel, logger)` — finds the applicant message/SteamID
  in a ticket channel.
- `parse_prospect(message) -> ...` — parses SteamID and requested kits from a message.
- `class RoleButton(Button)` — toggle button for a prospect role/kit.
- `get_prospect_value(service)` — reads a template value used for new roster rows.
- `copy_prospect_cell(service, target_row)` — copies formatting into a new roster row.
- `class ProspectView(View)` — the full prospect review UI (kit buttons, approve, etc.).

### `features/admins.py`
- `build_admins_cfg(steamids, role="Admin", comment="Match Config") -> str` —
  builds an `Admins.cfg` body from SteamIDs.
- `get_latest_backup(server_region) -> Path` — finds the newest local backup file.
- `fetch_remote_list() -> str` — downloads the competitive admin list from the
  remote URL.

### `commands/seeding_commands.py`
- `/seedbotstart` (`seedbotstart`) — start seeding now or schedule it at `HH:MM`.
- `/seedbotstop` (`seedbotstop`) — stop active or cancel scheduled seeding.
- `/seedbotstatus` (`seedbotstatus`) — show seeding status and player count.
- `/seedbothelp` (`seedbothelp`) — list the seedbot commands.
- `/seedbotreload` (`seedbotreload`) — reload rotation from Google Sheets.

### `commands/admin_commands.py`
- `/matchconfig` (`matchconfig`) — rewrite `Admins.cfg` for the given SteamIDs.
- `/resetconfig` (`resetconfig`) — restore `Admins.cfg` from the remote list.
- `/addcameraman` (`addcameraman`) — add the Cameraman role for given SteamIDs.
- `/removefromdb` (`removefromdb`) — remove a player from the banned-players DB.
- `/addtodb` (`addtodb`) — add a player (SteamID/EOSID) to the banned-players DB.

### `commands/prospect_commands.py`
- `/addprospect` (`addprospect`) — open the prospect review UI for a ticket.

### `commands/util_commands.py`
- `class SeedTrackRefreshView(View)` / `class SeedTrackRefreshButton(Button)` — UI
  for the live seed-tracking embed.
- `/addfog` (`add_fog`) — add a layer to `fog_off_maps.json` and upload it.
- `/showfog` (`show_fog`) — list the fog-off layers.
- `/removefog` (`remove_fog`) — remove a layer from `fog_off_maps.json`.
- `/killfeed` (`killfeed`) — pull/parse a log and show the killfeed for an ID.
- `/test` (`test`) — simple connectivity test command.
- `/seederboard` (`seederboard`) — post the seeding leaderboard embed.
- `/playercounts` (`playercounts`) — show player counts across competing servers.
- `get_start_time(timespan) -> datetime | None` — parses a timespan into a start time.
- `/seedtrack` (`seedtrack`) — start the live seed-tracking embed.
- `seedtrack_update_loop(bot)` — background loop that refreshes the seedtrack embed.

### `events/handlers.py`
- `load_sticky_state()` / `save_sticky_state(message_id)` — read/write the sticky
  message ID via the unified `sticky` state section.
- `extract_steamid_from_title(title) -> str | None` — pulls a SteamID from a forum
  thread title.
- `extract_ban_info_from_embed(embed) -> dict | None` — parses ban details from an
  embed.
- `on_thread_create(thread)` — on a new ban-evidence forum thread, records the ban
  and syncs the banned-players DB.
- `on_message(message)` — maintains the sticky reminder message in the report channel.

### `events/startup.py`
- `on_ready()` — attaches the Discord log handler, syncs slash commands to the
  guild, and starts the background tasks (seeding loop, rotation schedule, join-link
  updater).
