# Architecture — SquadPY Discord

This document maps the current file structure and summarizes the main
responsibilities and entry points in each module.

The bot is organized into configuration and shared runtime primitives, domain
features, and Discord command/event adapters. These layers are not strictly
one-directional: features and adapters share the client, configuration, state,
and utility modules.

```
config / bot / state / utils
             ↓
  database / features
             ↓
    commands / events
             ↓
            main
```

`bot.py` defines the shared Discord `client` and command `tree` without project
imports. `main.py` imports command and event modules to register their decorators,
then starts the client.

## File tree

```
.
├── main.py                     # Entry point: loads config, registers commands/events, runs the client
├── config.py                   # All env/config loading and derived constants
├── state.py                    # Runtime BotState + unified JSON persistence store
├── bot.py                      # Shared discord.Client and CommandTree
├── database.py                 # SQLite BattleMetrics ban and sync-state storage
├── requirements.txt            # Python dependencies
├── env.txt.example             # Sample environment file (keys only, no secrets)
│
├── features/                   # Domain logic (no slash-command decorators)
│   ├── seeding.py              # Seeding triggers, auto-seed window, scheduling, monitor loop
│   ├── rotation.py             # Map rotation, Google Sheet parsing, join-link updates
│   ├── prospects.py            # Prospect review UI, roster sheet writes, role assignment
│   ├── admins.py               # Admins.cfg generation, backups, remote admin list
│   ├── whitelist.py            # Whitelister API, clan/group/list refresh, audit logging
│   └── battlemetrics_bans.py   # Ban sync, forum posts/updates, active-ban SFTP export
│
├── commands/                   # Slash commands, grouped by domain
│   ├── seeding_commands.py     # /seedbotstart /seedbotstop /seedbotstatus /seedbothelp /seedbotreload
│   ├── admin_commands.py       # /matchconfig /resetconfig /addcameraman
│   ├── whitelist_commands.py   # /addwl /removewl
│   ├── prospect_commands.py    # /addprospect
│   └── util_commands.py        # /addfog /showfog /removefog /killfeed /test /seedtrack
│
├── utils/                      # Cross-cutting helpers
│   ├── sftp.py                 # SFTP read/write/download/modify helpers
│   ├── discord_helpers.py      # Messaging helpers, log handler, role checks
│   ├── retry.py                # BattleMetrics HTTP session, player counts, ban-list fetch
│   └── validation.py           # SteamID / EOSID / layer-name validators
│
└── events/                     # Discord event listeners
    ├── handlers.py             # on_message (sticky reminder and !practice)
    └── startup.py              # on_ready (sync commands, start background tasks)
```

## Module reference

### `main.py`
Entry point. Imports `config` and the shared `client`, then imports every
`commands/*` and `events/*` module so their `@tree.command` / `@client.event`
decorators register against the shared tree, and finally runs the client under
`if __name__ == "__main__"`.

### `config.py`
Configuration constants loaded from the environment (via `python-dotenv`), plus
derived values such as `SEEDING_MESSAGE`, `milestones`, `COMP_SFTP_SERVERS`, and
`WEEKLY_SHEET_IDS`. See `README.md` for the settings reference.

### `bot.py`
No functions — defines the shared `intents`, `client` (`discord.Client`) and
`tree` (`discord.app_commands.CommandTree`) used everywhere.

### `state.py`
- `class BotState` — holds all in-memory mutable runtime state (seeding flags,
  background-task references including the BattleMetrics ban sync, rotation
  message/channel IDs, HTTP session, and sticky message ID). A single instance
  `state` is shared across modules.
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
Owns the BattleMetrics bans SQLite database and creates/migrates its schema on
import. It provides ban lookups and bulk upserts/imports, records synchronization
checkpoints and Discord-posting state, tracks removals from BattleMetrics, and
builds/exports the active-ban JSON sent to SquadJS.

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
- `fetch_battlemetrics_bans()` — fetches all pages of the configured BattleMetrics
  ban list and its included player/server/user records.
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
- `get_server_name_rcon() -> str | None` — returns the configured
  `JOIN_LINK_SERVER_NAME` if set, otherwise fetches the current server name via RCON.
- `fetch_join_link_squadbrowser(server_name) -> str` — gets a join link from the
  squad browser API.
- `rotation_join_link_updater()` — background loop that refreshes the join link.
- `schedule_daily_rotation_update()` — schedules the daily rotation post.

### `features/battlemetrics_bans.py`
- `normalize_ban(...)` — converts BattleMetrics ban records and included
  relationships into the local database shape.
- `build_ban_message(...)` / `build_ban_embed(...)` — format new ban details for
  Discord evidence posts.
- `create_ban_forum_post(ban)` — creates a forum thread for a newly observed ban.
- `post_ban_update(thread_id, changes)` / `post_ban_deleted(thread_id)` — append
  change and removal notices to the associated evidence thread.
- `sync_battlemetrics_bans()` — imports the initial baseline, then synchronizes
  new, changed, and removed bans and pushes active bans to SquadJS.
- `battlemetrics_ban_sync_task()` — repeats synchronization at the configured
  interval; started by `events/startup.py`.

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
- `/seedtrack` (`playercounts`) — show player counts across tracked servers and
  provide a manual refresh button.
- `/seederboard` is currently disabled; its implementation is commented out.
- `seedtrack_update_loop(bot)` — background loop that refreshes the seedtrack embed.

### `events/handlers.py`
- `load_sticky_state()` / `save_sticky_state(message_id)` — read/write the sticky
  message ID via the unified `sticky` state section.
- `on_message(message)` — handles `!practice` and maintains the sticky reminder
  in the configured channel.
- Legacy ban-evidence parsing helpers and the commented `on_thread_create` handler
  are inactive. Current forum integration is implemented in
  `features/battlemetrics_bans.py`.

### `events/startup.py`
- `on_ready()` — attaches the Discord log handler, syncs slash commands to the
  guild, and starts the background tasks (seeding loop, rotation schedule, join-link
  updater, BattleMetrics ban sync, and Whitelister group/list/entry refresh).
