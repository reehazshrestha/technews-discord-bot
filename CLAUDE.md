# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Discord bot (discord.py) with two unrelated feature sets living in one process:

1. **IT news digest** — pulls trusted RSS feeds (international + Nepal), auto-posts daily at midnight Nepal time, plus on-demand `/technews` and `/nepalnews` slash commands.
2. **AI assistant** — in two Discord **forum channels**, auto-replies to new posts and to @mentions inside their threads; anywhere else in the server, @mentioning the bot triggers a general-purpose chat reply. All backed by a self-hosted OpenAI-compatible Gemini proxy (no API key).

## Commands

```bash
# setup
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
# or: uv sync   (pyproject.toml/uv.lock are the source of truth for deps; requirements.txt is kept in sync manually for non-uv hosts like Wispbyte)

cp .env.example .env   # fill in DISCORD_TOKEN + channel IDs, see README.md

python bot.py           # run
# or: uv run bot.py

python -c "import py_compile; [py_compile.compile(f, doraise=True) for f in ['bot.py','news.py','storage.py','config.py','ai.py']]"  # syntax-check all modules (no test suite exists)
```

There is no test suite, linter, or formatter configured. `src/it_news_bot/` is unused `uv init` scaffolding — the real entrypoint is `bot.py` at the repo root.

## Architecture

- **`config.py`** — single source of truth for all env-driven settings (loads `.env` via `python-dotenv`) and the static `FEEDS` dict (RSS sources per category, `intl`/`nepal`). Adding/removing a news source means editing `FEEDS` here.
- **`news.py`** — fetches and parses RSS feeds (`feedparser`), normalizes each entry into an `Article` dataclass (title, link, summary stripped of HTML, best-effort image extraction, author). Async wrapper (`fetch_category`) runs the blocking `feedparser.parse` in an executor and fetches all feeds in a category concurrently.
- **`storage.py`** — flat-file dedup store (`seen.json`), a dict of `category -> [entry_id, ...]` capped at 500 ids per category. Nothing else persists state.
- **`ai.py`** — thin client for the self-hosted `gemini-web2api` proxy (OpenAI-compatible `/v1/chat/completions`, no auth). Wraps errors as `AIError`, caps concurrent requests with a semaphore (the proxy is a free/slow reverse-engineered Gemini web backend — expect 15–60s+ per reply, that's normal, not a bug). Holds three system prompts: coding-help, code-review, and general chat.
- **`bot.py`** — the discord.py client, event handlers, and command definitions. Two independent subsystems live here:
  - **News**: `post_latest()` is shared by both the scheduled daily loop and the on-demand slash commands, but tracks **separate seen-state per mode** (`category` for scheduled posts, `category_cmd` for on-demand) — this is deliberate: the startup backlog-prime for the scheduled loop must not suppress the first on-demand command call. Embeds are batched (`EMBEDS_PER_MESSAGE = 5`) to stay under Discord's 10-embed/6000-char message caps, and seen-state is saved incrementally (try/finally) so a partial send failure doesn't cause reposts or lost dedup state next run.
  - **AI handling** (`on_message`): three-way branch. (1) In a forum thread whose parent is `CODING_HELP_CHANNEL_ID`/`CODE_REVIEW_CHANNEL_ID` (`FORUM_SYSTEM_PROMPTS`), auto-replies to the forum post itself or to an @mention inside that thread — never to plain follow-up chatter. (2) Anywhere else in the server, only responds when directly @mentioned, using `GENERAL_CHAT_SYSTEM_PROMPT`. (3) Otherwise ignored. The trick for detecting "is this the forum post itself vs a reply": Discord gives a forum post's starter message the **same ID as the thread**, so `message.id == channel.id` identifies it. `_already_handled()` is a bounded in-memory id cache guarding against a single message getting processed twice (e.g. a gateway resume redelivering it) — it's per-process only, so never run two bot processes on the same token at once, each would reply independently and look like duplicate answers.
  - Slash commands are rate-limited via `app_commands.checks.cooldown` keyed by guild (falls back to user id in DMs), and `bot.tree.error` centrally handles `CommandOnCooldown` plus any other command exception so a failure always gets a user-visible reply instead of a silently hung interaction. `_safe_reply` swallows `NotFound`/`HTTPException` from expired interaction tokens (can happen around bot restarts) instead of crashing.
  - `on_ready` syncs the slash command tree exactly once (`_synced` flag) even across gateway reconnects, and starts the daily loop only if not already running.

## Gotchas specific to this bot

- **Message Content intent is required** for all AI features (reading message/post/mention content) and must be enabled both in code (`intents.message_content = True`, already set) *and* in the Discord Developer Portal (Bot tab → Privileged Gateway Intents). If it's off in the portal, the bot crashes on connect with `PrivilegedIntentsRequired` — this breaks the whole bot, not just the AI feature, so don't touch `intents` without checking the portal setting is enabled.
- `CODING_HELP_CHANNEL_ID`/`CODE_REVIEW_CHANNEL_ID` must be **forum channels**, not regular text channels — the `isinstance(channel, discord.Thread)` check only matches forum posts/threads.
- `seen.json` and `.env` are gitignored and must exist on persistent storage on whatever host runs this — losing `seen.json` on restart re-primes to "everything currently in the feeds is already seen" (see `before_news_check_loop`), it does not replay a backlog.
- Never run two instances of this bot on the same `DISCORD_TOKEN` simultaneously (e.g. testing locally while it's also live elsewhere) — both process every message/interaction independently, producing duplicate replies.
- Inviting the bot via its profile card's "Add to Server" button uses the Developer Portal's **Installation tab** default install settings, not the manual OAuth invite link — if that tab's Guild Install scopes are missing `bot` (only `applications.commands`), the flow reports "Success" but never actually adds the bot as a guild member. Always invite via the explicit link (`scope=bot+applications.commands&permissions=...`, see README) or fix the Installation tab's Guild Install scopes to include `bot`.
