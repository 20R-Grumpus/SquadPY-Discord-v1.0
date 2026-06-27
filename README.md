# SquadPY Discord

A Discord bot for managing a Squad game-server community. It encourages server
"seeding" (getting enough players online to make a server live), posts the daily
map rotation, manages competitive `Admins.cfg` files, tracks banned players, and
runs the prospect-recruitment workflow.

This is the public/template branch: it ships with **no community-specific
defaults** (channel/role IDs, emoji, branding, or file paths). Configure it for
your own server entirely through the environment file described below.

The codebase is a modular Python package. For a full map of every file and
function, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Requirements

- Python 3.12+
- The dependencies in [`requirements.txt`](requirements.txt)
- A Discord bot token and a configured `env.txt` (see below)
- A Google service-account JSON file (for the Google Sheets features)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Create your environment file from the template and fill in the values
cp env.txt.example env.txt       # then edit env.txt

python main.py
```

> By default the bot reads `env.txt` from the project root. Override its location
> with the `ENV_FILE` environment variable if you keep it elsewhere.
> Runtime files (state, database, backups) are written to a `data/` folder next to
> the code unless you override `DATA_DIR` or the individual path variables.

## Configuration reference

All configuration is loaded in [`config.py`](config.py) from the environment
file. Every key below corresponds to a line in `env.txt.example`.

**Required** variables have no default — the bot will fail to start if they are
missing or malformed. **Optional** variables fall back to the listed default.

### Discord core

| Variable | Required | Description |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Bot token used to log in to Discord. Keep this secret. |
| `GUILD_ID` | Yes | ID of the Discord server (guild). Slash commands are synced to this guild. |
| `COMMAND_CHANNEL_ID` | Yes | Channel where the bot posts command responses/output. |
| `MESSAGE_CHANNEL_ID` | Yes | Default channel for seeding/announcement messages. |
| `LOG_CHANNEL_ID` | Yes | Channel the logging handler mirrors log records into. |
| `STICKY_CHANNEL_ID` | No (default `0`) | Report-only channel that gets the auto-reposted "sticky" reminder message. `0` disables it. |
| `FORUM_CHANNEL_ID` | Yes | Forum channel watched for new ban-evidence threads. |
| `PROSPECT_ANNOUNCEMENT_CHANNEL_ID` | Yes | Channel where new-prospect announcements are posted. |
| `TEST_CHANNEL_ID` | No (default `0` = disabled) | If a command is run in this channel, seeding messages are mirrored here instead of `MESSAGE_CHANNEL_ID`. Useful for testing. |

### Roles

| Variable | Required | Description |
| --- | --- | --- |
| `SQUAD_ROLE_ID` | Yes | Role pinged in the seeding message ("We're seeding!"). |
| `PROSPECT_ROLE_ID` | Yes | Role assigned to approved prospects. |
| `ALLOWED_ROLE_IDS` | No (default empty) | Comma-separated role IDs allowed to use gated slash commands. Empty means no role passes the check. |
| `PROSPECT_EXCLUDE_ROLE_IDS` | No (default empty) | Comma-separated role IDs that are never treated as prospects (e.g. staff/admins) when scanning a ticket. |
| `STICKY_PING_ROLE_ID` | No (default `0`) | Role mentioned in the sticky reminder embed for urgent matters. |

### Prospect welcome message

These are the channels linked in the welcome message posted in a prospect's ticket.

| Variable | Required | Description |
| --- | --- | --- |
| `PROSPECT_QUESTIONS_CHANNEL_ID` | No (default `0`) | Channel prospects are told to ask questions in. |
| `PROSPECT_FORM_CHANNEL_ID` | No (default `0`) | Channel with the form prospects should fill out. |
| `PROSPECT_RULES_CHANNEL_ID` | No (default `0`) | Rules channel prospects are told to read. |
| `PROSPECT_INFO_CHANNEL_ID` | No (default `0`) | Additional info channel prospects are told to read. |

### Community text

| Variable | Required | Description |
| --- | --- | --- |
| `COMMUNITY_NAME` | No (default `the community`) | Name used in the prospect welcome message ("Welcome to &lt;name&gt; …"). |
| `PROSPECT_NICK_PREFIX` | No (default empty) | Prefix added to an approved prospect's nickname, e.g. `[P] `. Empty means nicknames are left unchanged. |

### Sticky reminder embed

| Variable | Required | Description |
| --- | --- | --- |
| `STICKY_TICKET_CHANNEL_ID` | No (default `0`) | Ticket channel linked in the sticky reminder for users who need to add details. |
| `STICKY_PING_ROLE_ID` | No (default set) | See Roles above. |

### Emoji

Discord custom-emoji markup, e.g. `<a:name:id>` (animated) or `<:name:id>` (static).

| Variable | Required | Description |
| --- | --- | --- |
| `ALARM_EMOJI` | No (default empty) | Emoji repeated around the seeding announcement. |
| `ROTATION_EMOJI` | No (default empty) | Emoji shown in the rotation embed title. |

### Seeding / monitoring

| Variable | Required | Description |
| --- | --- | --- |
| `MAIN_BATTLEMETRICS_SERVER_ID` | Yes | BattleMetrics server ID used to read the main server's player count. |
| `MILESTONE_STEP` | Yes | Step size for player-count milestones; milestones are `range(10, 70, MILESTONE_STEP)`. |
| `CHECK_INTERVAL_SECONDS` | Yes | How often the monitor loop polls the player count. |
| `LIVE_PLAYER_COUNT` | Yes | Player count at/above which the server is considered "live". |
| `RESTART_HOUR` | Yes | Hour (London time) used for the scheduled daily restart/reset logic. Does not restart server, pushes rotation prior to restart. |
| `RESTART_MINUTE` | Yes | Minute used alongside `RESTART_HOUR`. Set 5 min prior to server restart. |
| `COMPETING_SERVER_DICT` | No (default `{}`) | JSON object of `{"Display Name": "battlemetrics_id"}` for the `/seedtrack` command. |

Fixed seeding behavior (not env-configurable, defined in `config.py`):
`COOLDOWN_HOURS=8`, auto-trigger band `45`–`55` players, and an auto-seed window of
`11:00`–`18:00` London time.

### Google Sheets

| Variable | Required | Description |
| --- | --- | --- |
| `GOOGLE_CREDENTIALS_FILE` | Yes (for Sheets features) | Path to the Google service-account JSON credentials file. |
| `GOOGLE_SHEET_ID_WEEK_1` | Yes (for rotation) | Spreadsheet ID for week 1 of the rotation cycle. |
| `GOOGLE_SHEET_ID_WEEK_2` | Yes (for rotation) | Spreadsheet ID for week 2 of the rotation cycle. |
| `COMP_ROSTER_SHEET_ID` | Yes (for prospects) | Spreadsheet ID of the competitive team roster. |
| `CYCLE_START_DATE` | No (default `2025-06-01`) | `YYYY-MM-DD` anchor date for the 14-day rotation cycle (interpreted in London time). |

### Join link (squad browser / RCON)

Used to fetch and refresh the "Join" button link on the rotation message.

| Variable | Required | Description |
| --- | --- | --- |
| `JOIN_LINK_API_KEY` | Yes (for join link) | API key for the squad-browser join-link service. |
| `JOIN_LINK_API_URL` | Yes (for join link) | Base URL of the join-link service. |
| `JOIN_LINK_SERVER_NAME` | Yes (for join link) | Server name used to look up the join link. |
| `JOIN_LINK_REFRESH_INTERVAL` | No (default `300`) | Seconds between join-link refreshes. |
| `JOIN_LINK_RCON_HOST` | Yes (for join link) | RCON host used to read the live server name. |
| `JOIN_LINK_RCON_PORT` | Yes (for join link) | RCON port. |
| `JOIN_LINK_RCON_PASSWORD` | Yes (for join link) | RCON password. Keep this secret. |
| `JOIN_REDIRECT_URL_TEMPLATE` | No (default empty) | Optional public redirect for the Join button; must contain `{lobby_id}`, e.g. `https://example.com/join?id={lobby_id}`. When empty, the raw join URL is used as-is. |

### Main SFTP (rotation files / logs)

| Variable | Required | Description |
| --- | --- | --- |
| `SFTP_HOST` | Yes (for SFTP features) | Host of the main game server's SFTP. |
| `SFTP_PORT` | No (default `22`) | SFTP port. |
| `SFTP_USER` | Yes (for SFTP features) | SFTP username. |
| `SFTP_PASSWORD` | Yes (for SFTP features) | SFTP password. Keep this secret. |
| `SFTP_REMOTE_PATH` | Yes (for rotation) | Remote path the rotation file is written to. |
| `SFTP_REMOTE_LOG_PATH` | Yes (for killfeed) | Remote path of the server log read by `/killfeed`. |

### SquadJS SFTP (fog / banned players / IP tracking)

| Variable | Required | Description |
| --- | --- | --- |
| `SQUADJS_SFTP_HOST` | Yes (for these features) | Host of the SquadJS data SFTP. |
| `SQUADJS_SFTP_PORT` | No (default `22`) | SFTP port. |
| `SQUADJS_SFTP_USER` | Yes | SFTP username. |
| `SQUADJS_SFTP_PASSWORD` | Yes | SFTP password. Keep this secret. |
| `SQUADJS_SFTP_FOG_PATH` | Yes (for fog commands) | Remote path of the fog-off layers JSON. |
| `SQUADJS_SFTP_IP_TRACK_PATH` | Yes (for IP tracking) | Remote path of the IP-tracking data. |
| `SQUADJS_SFTP_BANNED_PLAYERS_PATH` | No (default `/data/banned_players.json`) | Remote path the banned-players JSON is pushed to. |

### Competitive SFTP servers (NA / EU)

Two competitive servers whose `Admins.cfg` the bot manages. Each region has its own
host/port/user/password. They are combined into `COMP_SFTP_SERVERS` in `config.py`.

| Variable | Required | Description |
| --- | --- | --- |
| `SFTP_COMP_NA_HOST` | Yes (for comp admin) | NA competitive server SFTP host. |
| `SFTP_COMP_NA_PORT` | No (default `22`) | NA SFTP port. |
| `SFTP_COMP_NA_USER` | Yes | NA SFTP username. |
| `SFTP_COMP_NA_PASSWORD` | Yes | NA SFTP password. Keep this secret. |
| `SFTP_COMP_EU_HOST` | Yes (for comp admin) | EU competitive server SFTP host. |
| `SFTP_COMP_EU_PORT` | No (default `22`) | EU SFTP port. |
| `SFTP_COMP_EU_USER` | Yes | EU SFTP username. |
| `SFTP_COMP_EU_PASSWORD` | Yes | EU SFTP password. Keep this secret. |

The remote `Admins.cfg` / `RemoteAdminListHosts.cfg` paths
(`SFTP_COMP_ADMIN_PATH`, `SFTP_COMP_REMOTEADMIN_PATH`) are fixed in `config.py`.

### Competitive admin list

| Variable | Required | Description |
| --- | --- | --- |
| `COMP_ADMIN_LIST_URL` | No (default empty) | URL of the canonical competitive admin list fetched by `/resetconfig`. Empty disables the fetch. |

### File path overrides (optional)

All of these have sensible defaults in `config.py` and only need to be set if you
want to relocate the files (e.g. on a different machine).

By default every runtime file lives under a portable `data/` directory next to the
code (created automatically at startup), so the bot works out of the box. Override
`DATA_DIR` to move them all at once, or override an individual path below.

| Variable | Default | Description |
| --- | --- | --- |
| `ENV_FILE` | `./env.txt` | Path to the environment file this table is loaded from. |
| `DATA_DIR` | `./data` | Base directory for all files the bot creates. |
| `UNIFIED_STATE_FILE` | `./data/bot_state.json` | Single JSON file holding all runtime state sections (`seedtrack`, `rotation`, `sticky`, …). |
| `SEEDING_LEADERBOARD_FILE` | `./data/seeding_leaderboard.json` | JSON data file backing `/seederboard`. |
| `FOG_JSON_PATH` | `./data/fog_off_maps.json` | Local cache of fog-off layers. |
| `BANNED_PLAYERS_DB_PATH` | `./data/banned_players.db` | SQLite database of banned players. |
| `BACKUP_DIR` | `./data/admin_backups` | Local directory where `Admins.cfg` backups are written (created at startup). |
| `POP_TRACK_FILE` | `./data/seedtrack_state.json` | **Legacy.** Only read once to migrate seedtrack state into `UNIFIED_STATE_FILE`. |
| `ROTATION_TRACK_FILE` | `./data/rotation_state.json` | **Legacy.** Only read once to migrate rotation state into `UNIFIED_STATE_FILE`. |
| `STICKY_STATE_FILE` | `./data/sticky_state.json` | **Legacy.** Only read once to migrate sticky state into `UNIFIED_STATE_FILE`. |

## Runtime state

All persistent runtime state lives in a single JSON file (`UNIFIED_STATE_FILE`),
organized into named sections. Code reads/writes it through `state.py`:

```python
from state import get_state, set_state, update_state

update_state("rotation", message_id=123)   # merge a key without clobbering others
get_state("rotation")                       # -> {"message_id": 123, ...}
set_state("rotation", {})                    # replace/clear the whole section
```

On first run the bot automatically migrates the older per-feature files
(`seedtrack_state.json`, `rotation_state.json`, `sticky_state.json`) into the
unified file, so existing state is preserved.

## Commands

Slash commands are defined under `commands/` and registered when `main.py` imports
those modules. Summary:

- **Seeding:** `/seedbotstart`, `/seedbotstop`, `/seedbotstatus`, `/seedbothelp`, `/seedbotreload`
- **Admin config / bans:** `/matchconfig`, `/resetconfig`, `/addcameraman`, `/removefromdb`, `/addtodb`
- **Prospects:** `/addprospect`
- **Utilities:** `/addfog`, `/showfog`, `/removefog`, `/killfeed`, `/test`, `/seederboard`, `/playercounts`, `/seedtrack`
- **Whitelist:** `/link`, `/overwritelink`, `/unlink`, `/whitelist`, `/whiteliststatus`

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for what each command and helper does.

---

## Whitelist system

The whitelist system lets community members link their Steam or EOS ID to their
Discord account, lets staff manually whitelist players, and lets qualifying
members add friends via DMs. All data feeds into a single generated `Admins.cfg`
file that the game server reads for permissions.

### How it works

1. **Linked IDs** — Any member runs `/link <steam_or_eos_id>` once to associate
   their game identity with their Discord account. The link is **one-time**; to
   change it the member must ask staff, who use `/overwritelink`.

2. **Role-based groups** — Discord roles are mapped to Squad permission groups
   via `WHITELIST_ROLE_GROUP_MAP`. When `Admins.cfg` is regenerated, each linked
   member who still holds a mapped role gets an `Admin=` line in the
   corresponding group. If a member loses the role, their entry is automatically
   removed on the next refresh.

3. **Manual whitelist** — Staff run `/whitelist <name> <id> <duration>` to add
   anyone (even players not in the Discord) to a specific group. Entries can be
   permanent or expire after a duration (e.g. `7d`, `30d`, `12h`). Expired
   entries are pruned automatically.

4. **Friend whitelist (DMs)** — Members with a friend-tier role can DM the bot
   Steam/EOS IDs to add friends to the whitelist. The number of friend slots
   depends on the member's highest tier role (configured via
   `WHITELIST_FRIEND_TIERS`, e.g. 3, 5, or 10 slots). If the member loses
   their tier role, all their friends are automatically removed.

5. **Background sync** — A background task runs every `WHITELIST_REFRESH_INTERVAL`
   seconds (default 300). It prunes expired entries, checks that role-based and
   friend-based entries are still valid, regenerates `Admins.cfg`, and optionally
   pushes it to the game server via SFTP.

### Slash commands

| Command | Who can use it | Description |
| --- | --- | --- |
| `/link <game_id>` | Everyone | Link your Steam ID (17 digits) or EOS ID (32 chars) to your Discord account. One-time use. |
| `/overwritelink <member> <game_id>` | Staff | Set or overwrite any member's linked Steam/EOS ID. |
| `/unlink <member>` | Staff | Remove a member's linked ID. |
| `/whitelist <name> <game_id> [duration] [group]` | Staff | Manually whitelist a player. Duration examples: `7d`, `30d`, `12h`, `1d6h`. Omit for permanent. Group defaults to `WHITELIST_DEFAULT_GROUP`. |
| `/whiteliststatus` | Everyone | Show your own linked ID. Staff also see the total whitelist entry count. |

"Staff" means any member with a role listed in `WHITELIST_ALLOWED_ROLE_IDS`
(falls back to `ALLOWED_ROLE_IDS` if not set).

### DM commands (friend whitelist)

Members with a qualifying friend-tier role can DM the bot directly:

| DM message | Description |
| --- | --- |
| `<steam_or_eos_id> [label]` | Add a friend. The optional label is stored for reference. |
| `!remove <steam_or_eos_id>` | Remove a friend from your list. |
| `!friends` | List your current friends and remaining slots. |

The bot responds with clear success/error messages including how many slots
remain. If the member has no qualifying friend-tier role, the bot tells them.

### Generated Admins.cfg format

The output file is organized by group. Group headers appear first, then `Admin=`
entries grouped together under their respective group:

```
Group=Donors:reserve
Group=Cameraman:cameraman
Group=Admin:balance,ban,cameraman,canseeadminchat,chat,forceteamchange,kick,teamchange,reserve
Group=HeadAdmin:balance,ban,cameraman,canseeadminchat,changemap,chat,forceteamchange,pause,kick,teamchange,reserve,config
Group=Moderator:canseeadminchat,reserve,chat,kick,teamchange,forceteamchange
Group=ExpWL:reserve
Group=Whitelisted:reserve

Admin=76561198140219287:HeadAdmin // [20R] Grumps @grumpus.
Admin=76561198041972329:HeadAdmin // [20R] Dash @dashoor
Admin=76561198033778626:HeadAdmin // [20R] Gamoor @obamacare0626
Admin=76561199243588806:Admin // [20R] Ali Nawar @ali
Admin=76561197971371461:Admin // [20R] Legendary @odllegendary
Admin=76561198974359243:Moderator // [20R] Sir._Captain_Sky_Walker @sky
Admin=76561199803401091:Whitelisted // [20R] 20R Banzai @banzai
Admin=76561198026181506:Whitelisted // [20R] Aekin @cedkin
Admin=76561198124080679:ExpWL // [20R] -NSF- Spokels (added by Admin#1234)
Admin=76561198374673266:ExpWL // antouan (friend of Grumps)
```

Entries come from three sources, all merged by group:
- **Role-based**: linked IDs whose Discord member still holds a mapped role.
- **Manual**: `/whitelist` entries (with optional expiry).
- **Friends**: DM-submitted IDs under the default whitelist group.

### Whitelist configuration reference

| Variable | Required | Description |
| --- | --- | --- |
| `WHITELIST_ROLE_GROUP_MAP` | Yes | JSON mapping Discord role IDs to Squad group names, e.g. `{"123456": "Admin", "789012": "Moderator", "345678": "Whitelisted"}`. The order determines priority when a member has multiple mapped roles (first match wins). |
| `WHITELIST_GROUP_PERMS` | Yes | JSON mapping Squad group names to their comma-separated permissions, e.g. `{"Admin": "balance,ban,cameraman,...", "Whitelisted": "reserve"}`. These become the `Group=` header lines in the output. |
| `WHITELIST_ALLOWED_ROLE_IDS` | No | Comma-separated Discord role IDs allowed to use `/whitelist`, `/overwritelink`, and `/unlink`. Falls back to `ALLOWED_ROLE_IDS` when empty. |
| `WHITELIST_DEFAULT_GROUP` | No (default `Whitelisted`) | The group assigned by `/whitelist` when no explicit group is given, and the group used for friend-whitelist entries. |
| `WHITELIST_FRIEND_TIERS` | No (default `{}`) | JSON mapping Discord role IDs to the max number of friend slots, e.g. `{"111": 3, "222": 5, "333": 10}`. Members with multiple qualifying roles get the highest limit. |
| `WHITELIST_TAG_PREFIX` | No (default empty) | Tag prefix added to `Admin=` comment lines for role-based entries, e.g. `20R` produces `[20R] PlayerName`. |
| `WHITELIST_REFRESH_INTERVAL` | No (default `300`) | Seconds between background regeneration cycles. |
| `WHITELIST_OUTPUT_PATH` | No (default `data/Admins.cfg`) | Local file path for the generated `Admins.cfg`. |
| `WHITELIST_SFTP_REMOTE_PATH` | No (default empty) | Remote SFTP path to push the generated `Admins.cfg` to. Uses the main SFTP credentials (`SFTP_HOST`, etc.). Leave empty to skip SFTP push. |
| `WHITELIST_DB_PATH` | No (default `data/whitelist.db`) | Path to the SQLite database storing linked IDs, whitelist entries, friends, and the audit log. |

### Audit log

Every mutation (link, unlink, overwrite, whitelist add, friend add/remove, and
automatic purges) is recorded in the `whitelist_log` table of the SQLite
database with a timestamp, action type, actor, target, and detail string. This
log is append-only and is not automatically pruned.

## Security notes

- `env.txt` and the runtime state/DB files are gitignored — never commit real
  tokens, passwords, or API keys.
- `env.txt.example` is a keys-only template and contains no secrets.
