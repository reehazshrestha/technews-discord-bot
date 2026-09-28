# IT News Discord Bot

Posts latest international + Nepal IT/tech news from trusted RSS sources. Auto-posts daily at midnight Nepal time (configurable) + `/technews` and `/nepalnews` slash commands for on-demand. No duplicates — each posted article ID tracked in `seen.json`.

Also answers coding questions in two **forum channels**:
- New post in `#coding-help` → bot auto-replies trying to solve it.
- New post in `#code-review` → bot auto-reviews the posted code.
- Anywhere in either thread, @mention the bot to ask a follow-up — it only replies when tagged after the initial post.

Backed by a self-hosted gemini-web2api OpenAI-compatible proxy (no API key needed). Replies can take 15–60s since it's a free reverse-engineered Gemini web backend, not the official API — that's expected, not a bug.

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
4. Right-click target channels in Discord (dev mode on) -> Copy Channel ID -> put in `.env`. `CODING_HELP_CHANNEL_ID` / `CODE_REVIEW_CHANNEL_ID` must be **forum channels** (the ones with a "+" New Post button), not regular text channels.
5. **Enable Message Content Intent**: Developer Portal -> your app -> Bot tab -> scroll to "Privileged Gateway Intents" -> toggle on **MESSAGE CONTENT INTENT** -> Save. Required for the bot to read forum posts and @mentions. Without this, code-help/code-review auto-replies silently do nothing.

## Deploy on Wispbyte

1. Create a new server -> choose Python egg/type.
2. Upload project files (or git clone) into the server's file manager.
3. Set Startup command: `python bot.py` (or `python3 bot.py`, whichever egg uses).
4. Set environment variables in the panel's Variables tab: everything in `.env.example` (`DISCORD_TOKEN`, `INTL_NEWS_CHANNEL_ID`, `NEPAL_NEWS_CHANNEL_ID`, `POST_TIME_NPT`, `CODING_HELP_CHANNEL_ID`, `CODE_REVIEW_CHANNEL_ID`, `GEMINI_API_BASE`, `AI_MODEL`).
5. Install deps: run `pip install -r requirements.txt` from panel console (or set as install command if egg supports it).
6. Start server. Check console log for "Logged in as ...".

Note: `seen.json` persists which articles already posted — keep it on the server's persistent disk so restarts don't re-flood the channel.
