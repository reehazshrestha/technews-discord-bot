import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    DAILY_POST_TIME_NPT,
    DISCORD_TOKEN,
    INTL_NEWS_CHANNEL_ID,
    MAX_POSTS_PER_CHECK,
    NEPAL_NEWS_CHANNEL_ID,
    NEWS_COMMAND_COOLDOWN_SECONDS,
)
from news import fetch_category
from storage import load_seen, mark_seen, save_seen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("it-news-bot")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

CATEGORY_META = {
    "intl": {"label": "International IT News", "color": discord.Color.blurple(), "channel_id": INTL_NEWS_CHANNEL_ID},
    "nepal": {"label": "Nepal IT News", "color": discord.Color.red(), "channel_id": NEPAL_NEWS_CHANNEL_ID},
}


def build_embed(article, category: str) -> discord.Embed:
    meta = CATEGORY_META[category]
    embed = discord.Embed(title=article.title, url=article.link, color=meta["color"])

    parts = []
    if article.author:
        parts.append(f"**{article.author}**")
    if article.summary:
        parts.append(article.summary)
    parts.append(f"[Read the full story at {article.source}.]({article.link})")
    embed.description = "\n\n".join(parts)

    if article.image:
        embed.set_image(url=article.image)
    if article.published:
        embed.set_footer(text=f"{article.source} • {article.published}")
    else:
        embed.set_footer(text=article.source)
    return embed


_synced = False


@bot.event
async def on_ready():
    global _synced
    if not _synced:
        try:
            synced = await bot.tree.sync()
            log.info("Synced %d slash commands", len(synced))
            _synced = True
        except Exception:
            log.exception("Slash command sync failed")
    if not news_check_loop.is_running():
        news_check_loop.start()
    log.info("Logged in as %s", bot.user)


EMBEDS_PER_MESSAGE = 5  # keep well under Discord's 10-embed / 6000-char-per-message caps


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def post_latest(category: str, interaction: discord.Interaction | None = None, limit: int = 5):
    try:
        articles = await fetch_category(category)
    except Exception:
        log.exception("Failed fetching feeds for category=%s", category)
        if interaction is not None:
            await interaction.followup.send("Couldn't reach news sources right now. Try again later.")
        return
    articles.sort(key=lambda a: a.published, reverse=True)

    # On-demand and scheduled posts track separate seen-state so a startup
    # backlog-prime (scheduled) doesn't swallow the first /technews call.
    seen_key = f"{category}_cmd" if interaction is not None else category
    seen = load_seen()
    seen_ids = set(seen.get(seen_key, []))
    new_articles = [a for a in articles if a.entry_id not in seen_ids]

    if interaction is not None:
        new_articles = new_articles[:limit]
        if not new_articles:
            await interaction.followup.send("No new articles since last check. Try again later.")
            return
        posted = []
        try:
            for chunk in _chunks(new_articles, EMBEDS_PER_MESSAGE):
                embeds = [build_embed(a, category) for a in chunk]
                await interaction.followup.send(embeds=embeds)
                posted.extend(chunk)
        finally:
            for article in posted:
                mark_seen(seen, seen_key, article.entry_id)
            if posted:
                save_seen(seen)
        return

    channel_id = CATEGORY_META[category]["channel_id"]
    if not channel_id:
        log.warning("No channel configured for category=%s, skipping scheduled post", category)
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        log.warning("Channel id %s not found for category=%s", channel_id, category)
        return

    new_articles = list(reversed(new_articles[:MAX_POSTS_PER_CHECK]))
    posted = []
    try:
        for chunk in _chunks(new_articles, EMBEDS_PER_MESSAGE):
            embeds = [build_embed(a, category) for a in chunk]
            await channel.send(embeds=embeds)
            posted.extend(chunk)
    except Exception:
        log.exception("Failed posting to channel for category=%s", category)
    finally:
        for article in posted:
            mark_seen(seen, seen_key, article.entry_id)
        if posted:
            save_seen(seen)
            log.info("Posted %d new articles for category=%s", len(posted), category)


@tasks.loop(time=DAILY_POST_TIME_NPT)
async def news_check_loop():
    for category in CATEGORY_META:
        try:
            await post_latest(category)
        except Exception:
            log.exception("Failed checking category=%s", category)


@news_check_loop.before_loop
async def before_news_check_loop():
    await bot.wait_until_ready()
    # Prime seen-state on first run so we don't dump the whole backlog into the channel.
    seen = load_seen()
    if seen:
        return
    for category in CATEGORY_META:
        articles = await fetch_category(category)
        for article in articles:
            mark_seen(seen, category, article.entry_id)
    save_seen(seen)
    log.info("Primed seen-state on first run")


def _cooldown_key(interaction: discord.Interaction):
    return interaction.guild_id or interaction.user.id


@bot.tree.command(name="technews", description="Latest international IT/tech news")
@app_commands.checks.cooldown(1, NEWS_COMMAND_COOLDOWN_SECONDS, key=_cooldown_key)
async def technews(interaction: discord.Interaction):
    await interaction.response.defer()
    await post_latest("intl", interaction=interaction)


@bot.tree.command(name="nepalnews", description="Latest Nepal IT/tech news")
@app_commands.checks.cooldown(1, NEWS_COMMAND_COOLDOWN_SECONDS, key=_cooldown_key)
async def nepalnews(interaction: discord.Interaction):
    await interaction.response.defer()
    await post_latest("nepal", interaction=interaction)


async def _safe_reply(interaction: discord.Interaction, msg: str):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except (discord.NotFound, discord.HTTPException):
        # Interaction token expired/invalid (e.g. bot restarted mid-flight) — nothing we can do.
        log.warning("Could not deliver reply, interaction expired")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        minutes = int(error.retry_after // 60)
        seconds = int(error.retry_after % 60)
        await _safe_reply(interaction, f"This command is on cooldown. Try again in {minutes}m {seconds}s.")
        return

    original = getattr(error, "original", error)
    log.exception("Unhandled app command error", exc_info=original)
    await _safe_reply(interaction, "Something went wrong running that command. Try again later.")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN missing. Set it in .env")
    bot.run(DISCORD_TOKEN)
