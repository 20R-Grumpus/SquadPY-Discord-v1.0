bot/
├── __init__.py
├── config.py          (Config loading & validation)
├── bot.py             (Discord client setup & startup)
├── database.py        (SQLite operations)
├── state.py           (Unified BotState class)
├── features/
│   ├── seeding.py     (Seeding logic)
│   ├── rotation.py    (Rotation & join links)
│   ├── admins.py      (Admin config)
│   └── prospects.py   (Prospect management)
├── commands/
│   ├── seeding_commands.py
│   ├── admin_commands.py
│   ├── prospect_commands.py
│   └── util_commands.py
├── utils/
│   ├── sftp.py        (SFTP wrapper)
│   ├── discord_helpers.py
│   ├── retry.py       (Retry logic)
│   └── validation.py  (Input validation)
└── events/
    ├── handlers.py    (Event listeners)
    └── startup.py     (Startup logic)

main.py               (Entry point)
requirements.txt
env.txt
README.md