# IT News Discord Bot

Posts latest international + Nepal IT/tech news from trusted RSS sources. Auto-posts daily at midnight Nepal time (configurable) + `/technews` and `/nepalnews` slash commands for on-demand. No duplicates — each posted article ID tracked in `seen.json`.

## Sources

International: TechCrunch, The Verge, Ars Technica, Wired, ZDNet, The Register, BleepingComputer, Engadget.
Nepal: ICT Frame, Nepali Telecom, TechMandu.

Edit `config.py` -> `FEEDS` to add/remove sources.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DISCORD_TOKEN + channel IDs
python bot.py
```

## Discord bot setup

1. Application ID: `1554162737855078450` (already set below).
2. Get bot token: Developer Portal -> your app -> Bot tab -> Reset Token / copy. Paste into `.env` as `DISCORD_TOKEN`. Never paste token in chat/commit it — treat like a password.
3. Invite link (View Channel + Send Messages + Embed Links):
   `https://discord.com/oauth2/authorize?client_id=1554162737855078450&scope=bot+applications.commands&permissions=19456`
4. Right-click target channels in Discord (dev mode on) -> Copy Channel ID -> put in `.env`.

## Deploy on Wispbyte

1. Create a new server -> choose Python egg/type.
2. Upload project files (or git clone) into the server's file manager.
3. Set Startup command: `python bot.py` (or `python3 bot.py`, whichever egg uses).
4. Set environment variables in the panel's Variables tab: `DISCORD_TOKEN`, `INTL_NEWS_CHANNEL_ID`, `NEPAL_NEWS_CHANNEL_ID`, `POST_TIME_NPT` (default `00:00`).
5. Install deps: run `pip install -r requirements.txt` from panel console (or set as install command if egg supports it).
6. Start server. Check console log for "Logged in as ...".

Note: `seen.json` persists which articles already posted — keep it on the server's persistent disk so restarts don't re-flood the channel.
