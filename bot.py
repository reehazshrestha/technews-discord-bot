import logging
import re
from collections import deque

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ai import (
    CODE_REVIEW_SYSTEM_PROMPT,
    CODING_HELP_SYSTEM_PROMPT,
    GENERAL_CHAT_SYSTEM_PROMPT,
    AIError,
    ask_ai,
)
from config import (
    CODE_REVIEW_CHANNEL_ID,
    CODING_HELP_CHANNEL_ID,
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
intents.message_content = True
intents.members = True  # privileged: needed to build the server member roster for role checks
bot = commands.Bot(command_prefix="!", intents=intents)

CATEGORY_META = {
    "intl": {"label": "International IT News", "color": discord.Color.blurple(), "channel_id": INTL_NEWS_CHANNEL_ID},
    "nepal": {"label": "Nepal IT News", "color": discord.Color.red(), "channel_id": NEPAL_NEWS_CHANNEL_ID},
}

FORUM_SYSTEM_PROMPTS = {
    CODING_HELP_CHANNEL_ID: CODING_HELP_SYSTEM_PROMPT,
    CODE_REVIEW_CHANNEL_ID: CODE_REVIEW_SYSTEM_PROMPT,
}

_DIRECTORY_CHANNEL_IDS = {
    "the coding-help forum": CODING_HELP_CHANNEL_ID,
    "the code-review forum": CODE_REVIEW_CHANNEL_ID,
    "the international news channel": INTL_NEWS_CHANNEL_ID,
    "the Nepal news channel": NEPAL_NEWS_CHANNEL_ID,
}

# Descriptions of every configured channel get appended to the AI system
# prompt so the model can reference them with <#id> mentions, which Discord
# renders as clickable channel links itself.
_channel_directory: str = ""


def _build_channel_directory() -> None:
    global _channel_directory
    lines = [f"- {label} (id {cid}): refer to it as <#{cid}>"
             for label, cid in _DIRECTORY_CHANNEL_IDS.items() if cid]
    if lines:
        _channel_directory = (
            "\n\nChannel directory for this server (use these exact <#id> mentions "
            "when pointing the user to one of them):\n" + "\n".join(lines))
    else:
        _channel_directory = ""


_FOLLOWUPS_RE = re.compile(r"FOLLOW-UPS:\s*([^\n]*)", re.IGNORECASE)
_ELICITATION_JUNK_RE = re.compile(r"\s*<Elicit\w*[^>]*>[\s\S]*?(?:</Elicit\w+>\s*)?$|\s*<Elicit\w*[^>]*>[^<]*$",
                                  re.IGNORECASE)


def _clean_label(label: str) -> str:
    label = re.sub(r"<[^>]+>", "", label)
    label = re.sub(r"[*_`~]", "", label)
    return label.strip().strip(". \t")[:40]


def _split_followups(text: str) -> tuple[str, list[str]]:
    """Split 'FOLLOW-UPS: a | b | c' off the end of an AI reply.

    Returns (visible_body, [button_labels]). Also strips any Elicit-tag
    junk the model may have leaked despite the output contract.
    """
    body = _ELICITATION_JUNK_RE.sub("", text).rstrip()
    match = _FOLLOWUPS_RE.search(body)
    queries: list[str] = []
    if match:
        body = body[: match.start()].rstrip()
        raw = match.group(1).strip()
        if raw.lower() != "none":
            queries = [q for q in (_clean_label(part) for part in raw.split("|")) if q][:3]
    return body, queries


# Cached server member roster: user_id -> (is_admin, is_moderator, [top role names])
_MEMBER_ROSTER: dict[int, tuple[bool, bool, list[str]]] = {}


async def _build_member_roster() -> None:
    """One-time fetch of the full member list on startup (needs the 'members' intent).

    Caches: { guild_id: { user_id: (is_admin, is_moderator, [top 3 role names]) } }
    If the roster can't be built (missing intent, rate limit, partial fetch), the
    AI still gets per-message author context, so it never sits in the dark.
    """
    global _MEMBER_ROSTER
    _MEMBER_ROSTER = {}
    guild = getattr(bot, "guilds", [])[0] if bot.guilds else None
    if guild is None:
        log.warning("No guild found - member roster unavailable")
        return
    try:
        # fetch_members pulls all members into the cache in one go (members intent)
        async for member in guild.fetch_members(limit=None):
            pass
        roster: dict[int, tuple[bool, bool, list[str]]] = {}
        for m in guild.members:
            if m.bot:
                continue
            perms = m.guild_permissions
            is_admin = bool(perms.administrator)
            is_mod = bool(perms.manage_guild or perms.manage_roles or perms.manage_messages
                           or perms.ban_members or perms.kick_members or perms.moderate_members)
            roles = [r.name for r in sorted(m.roles, key=lambda r: r.position, reverse=True)
                     if not r.is_default()][:3]
            roster[m.id] = (is_admin, is_mod, roles)
        _MEMBER_ROSTER[guild.id] = roster
        log.info("Built member roster for %s: %d members", guild.name, len(roster))
    except discord.HTTPException:
        log.exception("Failed to build member roster - AI will use per-message context only")
        _MEMBER_ROSTER = {}


async def build_embed(article, category: str) -> discord.Embed:
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
    await _build_member_roster()
    _build_channel_directory()
    if not news_check_loop.is_running():
        news_check_loop.start()
    log.info("Logged in as %s", bot.user)


MAX_DISCORD_MSG_LEN = 1900
MAX_AI_REPLY_CHUNKS = 3

CODE_BLOCK_RE = re.compile(r"(```[^\n]*\n?[\s\S]*?```)")


def _extract_code_blocks(text: str) -> list[str]:
    return [m.group(0) for m in CODE_BLOCK_RE.finditer(text)]


def _smart_split(text: str, limit: int = MAX_DISCORD_MSG_LEN) -> list[str]:
    """Split into <=limit chunks WITHOUT cutting inside a code fence.

    Code blocks that fit are kept whole. One code block longer than the
    limit is split ChatGPT-style: every fragment re-fenced with the same
    language label, so formatting survives chunking.
    """
    chunks: list[str] = []
    buf: str = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            chunks.append(buf.rstrip())
        buf = ""

    for part in CODE_BLOCK_RE.split(text):
        if not part:
            continue
        if len(buf) + len(part) + 2 <= limit:
            buf += ("" if not buf else "\n\n") + part
            if len(buf) < limit:
                continue
            flush()
            continue
        flush()
        if len(part) <= limit:
            chunks.append(part)
            continue
        open_line, nl, rest = part.partition("\n")
        if not nl or not part.startswith("```"):
            for slice_ in (part[i : i + limit] for i in range(0, len(part), limit)):
                chunks.append(slice_)
            continue
        body = rest[:-3] if rest.endswith("```") else rest
        if body.endswith("\n"):
            body = body[:-1]  # fence always closes with \n```; that newline is not code
        room = limit - len(open_line) - 5
        cur: list[str] = []
        cur_len = 0

        def flush_block() -> None:
            nonlocal cur, cur_len
            if cur:
                chunks.append(f"{open_line}\n" + "\n".join(cur) + "\n```")
            cur = []
            cur_len = 0

        for line_content in body.split("\n"):
            if cur_len + len(line_content) + 1 > room:
                flush_block()
                if len(line_content) > room:
                    # single line longer than a whole chunk: hard-slice it
                    pieces = [line_content[i : i + room]
                              for i in range(0, len(line_content), room)]
                    for piece in pieces[:-1]:
                        chunks.append(f"{open_line}\n{piece}\n```")
                    line_content = pieces[-1]
            cur.append(line_content)
            cur_len += len(line_content) + 1
        flush_block()
    flush()
    return chunks


def _member_status_line(guild: discord.Guild, user_id: int) -> str:
    """Look up a user_id in the cached roster and return a short role summary.

    Format: 'Administrator' / 'Moderator' / '<top roles>' / 'regular member'.
    Falls back to 'unknown' if the user left, the bot wasn't in the guild, or
    the roster couldn't be built.
    """
    guild_id = guild.id
    roster = _MEMBER_ROSTER.get(guild_id)
    if roster is None:
        return "unknown"
    info = roster.get(user_id)
    if info is None:
        return "unknown"
    is_admin, is_mod, roles = info
    if is_admin:
        return "server **Administrator**"
    if is_mod:
        return "server **Moderator**"
    if roles:
        return ", ".join(roles)
    return "regular member"


class FollowUpView(discord.ui.View):
    """Buttons beneath AI replies: follow-up questions + code copying.

    Non-persistent by design: after a bot process restarts, the buttons on
    old messages stop responding (Discord shows 'interaction failed'). The
    AI regenerates suggestions on every reply, so that's an acceptable
    trade-off for not needing fixed custom ids.
    """

    custom_id_prefix = "it-news-bot:ai-followup:"

    def __init__(self, channel: discord.abc.Messageable, system_prompt: str,
                 queries: list[str], code_blocks: list[str] | None = None):
        super().__init__(timeout=None)
        self.channel = channel
        self.system_prompt = system_prompt
        self.code_blocks = code_blocks or []
        self.followup_buttons: list[discord.ui.Button] = []
        for query in queries:
            if not query:
                continue
            button = discord.ui.Button(
                label=query[:80],
                custom_id=f"{self.custom_id_prefix}{query[:40]}",
            )
            button.callback = self._make_callback(query)
            self.followup_buttons.append(button)
            self.add_item(button)
        for index, block in enumerate(self.code_blocks[:4]):
            first_line = block[3:].split("\n", 1)[0].strip() or "code"
            button = discord.ui.Button(
                label=f"Copy code ({first_line[:24]})",
                style=discord.ButtonStyle.secondary,
                custom_id=f"it-news-bot:copy-code:{index}",
            )
            button.callback = self._make_copy_callback(index)
            self.add_item(button)

    def _make_copy_callback(self, index: int):
        async def callback(interaction: discord.Interaction) -> None:
            # Copy = send the snippet alone as ephemeral messages: clean,
            # properly fenced, one long-press/mobile copy per message.
            block = self.code_blocks[index]
            pieces = [block[i : i + MAX_DISCORD_MSG_LEN]
                      for i in range(0, len(block), MAX_DISCORD_MSG_LEN)]
            try:
                await interaction.response.send_message(pieces[0], ephemeral=True)
                for piece in pieces[1:]:
                    await interaction.followup.send(piece, ephemeral=True)
            except discord.HTTPException:
                log.exception("Copy-code button failed in channel=%s", self.channel.id)
        return callback

    def _make_callback(self, query: str):
        async def callback(interaction: discord.Interaction) -> None:
            # Anti-spam: burn the follow-up buttons on this message after the
            # first click so a message can only ever launch one AI chain.
            # Copy-code buttons stay enabled (they don't call the AI).
            for button in self.followup_buttons:
                button.disabled = True
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                log.exception("Could not disable follow-up buttons in channel=%s", self.channel.id)
            await interaction.response.defer()
            try:
                async with self.channel.typing():
                    reply = await ask_ai(self.system_prompt + _channel_directory, query)
            except AIError:
                log.exception("AI follow-up failed for query=%r", query)
                try:
                    await interaction.followup.send(
                        "Couldn't reach the AI backend just now. Try again in a bit.", ephemeral=True)
                except discord.HTTPException:
                    pass
                return
            body, followups = _split_followups(reply)
            await _send_body(self.channel, body, self.system_prompt, followups)
        return callback


def _split_segments(body: str) -> list[tuple[bool, str]]:
    """(is_code, part) pairs in original order, via CODE_BLOCK_RE.split()."""
    segments: list[tuple[bool, str]] = []
    for idx, part in enumerate(CODE_BLOCK_RE.split(body)):
        if part:
            segments.append((bool(idx % 2), part))
    return segments


async def _send_body(channel: discord.abc.Messageable, body: str, system_prompt: str,
                     followups: list[str], reply_to: discord.Message | None = None) -> None:
    """Send an AI reply ChatGPT-style:

    prose chunks go out as plain messages, and every code block is its own
    message with a "Copy code" button attached directly beneath it. The
    follow-up buttons land on the final message (Discord allows one component
    bar per message, so the bar on the last code message combines them).
    """
    last_message: discord.Message | None = None
    last_is_code = False
    last_piece = ""
    sent = 0
    truncated = False
    for is_code, part in _split_segments(body):
        pieces = [part] if is_code else _smart_split(part)
        for piece in pieces:
            if sent >= MAX_AI_REPLY_CHUNKS:
                truncated = True
                break
            view = FollowUpView(channel, system_prompt, [], [piece]) if is_code else None
            try:
                if sent == 0 and reply_to is not None:
                    last_message = await reply_to.reply(piece, view=view)
                else:
                    last_message = await channel.send(piece, view=view)
            except discord.HTTPException:
                log.exception("Failed sending AI reply in channel=%s", channel.id)
                return
            last_is_code = is_code
            last_piece = piece
            sent += 1
        if truncated:
            break

    if last_message is None:
        return
    if not truncated and not followups:
        # last message already has its (single-block) copy bar — done
        return
    # Rebuild the bar on the final message: follow-ups + copy for its block.
    final_view = FollowUpView(
        channel, system_prompt, followups,
        [last_piece] if last_is_code else None)
    content = last_message.content
    if truncated:
        content = (content + "\n\n*[Reply truncated — use the Copy code buttons above for "
                   "the complete snippets.]*")[:MAX_DISCORD_MSG_LEN]
    try:
        await last_message.edit(content=content, view=final_view)
    except discord.HTTPException:
        log.exception("Failed attaching final button bar in channel=%s", channel.id)


def _is_bot_or_system(message: discord.Message) -> bool:
    return message.author.bot


async def _send_ai_reply(channel: discord.abc.Messageable, reply: str,
                         system_prompt: str,
                         reply_to: discord.Message | None = None) -> None:
    """Send an AI reply: strip/rescue the FOLLOW-UPS block, chunk the body
    without breaking code fences, attach the button view to the last chunk."""
    body, followups = _split_followups(reply)
    await _send_body(channel, body, system_prompt, followups, reply_to=reply_to)



_HANDLED_IDS_CAP = 500
_handled_message_ids: deque[int] = deque()
_handled_message_ids_set: set[int] = set()


def _already_handled(message_id: int) -> bool:
    """Guard against processing the same message twice (e.g. a gateway
    resume redelivering an event). Best-effort within one process only —
    never run two bot processes on the same token at once, each would
    reply independently."""
    if message_id in _handled_message_ids_set:
        return True
    _handled_message_ids.append(message_id)
    _handled_message_ids_set.add(message_id)
    if len(_handled_message_ids) > _HANDLED_IDS_CAP:
        oldest = _handled_message_ids.popleft()
        _handled_message_ids_set.discard(oldest)
    return False


MAX_CONTEXT_HOPS = 4          # how far up the reply chain we look
MAX_PARENT_CHARS = 1200       # per quoted message


async def _collect_reply_context(message: discord.Message) -> list[str]:
    """Walk up a Discord reply chain and quote what was said.

    This is the restart-proof conversation memory: the bot keeps no chat
    history between process restarts, but Discord itself stores the full
    reply chain — so 'show me that in python' sent as a *reply* to an older
    bot message works even when the live conversation state is gone.
    Chain is returned oldest-first.
    """
    chain: list[str] = []
    seen: set[int] = set()
    ref = message.reference
    current = message
    for _ in range(MAX_CONTEXT_HOPS):
        if ref is None or ref.message_id is None:
            break
        parent = ref.resolved if isinstance(ref.resolved, discord.Message) else None
        if parent is None:
            try:
                parent = await current.channel.fetch_message(ref.message_id)
            except discord.HTTPException:
                break  # deleted or permission issue; use what we have
        if parent.id in seen:
            break
        seen.add(parent.id)
        if parent.author == bot.user:
            who = "the bot"
        else:
            who = parent.author.display_name or parent.author.name
        text = parent.content.strip() or "(no plain text content)"
        if len(text) > MAX_PARENT_CHARS:
            text = text[:MAX_PARENT_CHARS] + " …[truncated]"
        chain.append(f"{who} said:\n{text}")
        ref = parent.reference
        current = parent
    chain.reverse()
    return chain


def _member_context_line(member: discord.Member) -> str:
    """Who is the AI talking to? Discord gives us every message author as a
    full Member (roles + permissions), so no extra intent is needed."""
    perms = member.guild_permissions
    if perms.administrator:
        status = "a server **Administrator**"
    elif (perms.manage_guild or perms.manage_roles or perms.manage_messages
          or perms.ban_members or perms.kick_members or perms.moderate_members):
        status = "a server **Moderator**"
    else:
        status = "a regular member"
    roles = [role.name for role in
             sorted(member.roles, key=lambda r: r.position, reverse=True)
             if not role.is_default()][:3]
    role_note = f" Their top roles: {', '.join(roles)}." if roles else ""
    return f"You are assisting {member.mention}, who is {status}.{role_note}"


@bot.event
async def on_message(message: discord.Message):
    if _is_bot_or_system(message):
        return

    channel = message.channel
    is_forum_thread = isinstance(channel, discord.Thread) and channel.parent_id in FORUM_SYSTEM_PROMPTS
    is_mention = bot.user in message.mentions
    is_reply_to_bot = False
    if message.reference is not None and isinstance(message.reference.resolved, discord.Message):
        is_reply_to_bot = message.reference.resolved.author == bot.user

    if is_forum_thread:
        system_prompt = FORUM_SYSTEM_PROMPTS[channel.parent_id]
        is_new_post = message.id == channel.id
        if not is_new_post and not (is_mention or is_reply_to_bot):
            return
    elif is_mention or is_reply_to_bot:
        # Anywhere else in the server: respond when @mentioned, or when the
        # user replies to one of the bot's messages. Replying is how users
        # "restore" context the bot lost by restarting.
        system_prompt = GENERAL_CHAT_SYSTEM_PROMPT
        is_new_post = False
    else:
        return

    if _already_handled(message.id):
        log.info("Skipping duplicate message id=%s in channel=%s", message.id, channel.id)
        return

    content = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
    if is_new_post:
        content = f"Post title: {channel.name}\n\n{content}" if channel.name else content
    if not content:
        return

    if message.reference is not None:
        conversation = await _collect_reply_context(message)
        if conversation:
            quoted = "\n\n".join(conversation)
            content = (f"Earlier conversation in this reply thread (it may have started "
                       f"before the bot last restarted; oldest message first):\n\n{quoted}\n\n"
                       f"{message.author.display_name} now replies with this follow-up:\n{content}")
    if isinstance(message.author, discord.Member) and message.guild:
        # AI context 1: who is the current chat partner in the server hierarchy?
        content = _member_context_line(message.author) + "\n\n" + content
        # AI context 2: the whole server roster so it knows anyone else's role,
        # regardless of who is currently chatting.
        roster_lines = [
            f"Server roster: user {user_id} is a {role_line}."
            for user_id, (admin, mod, r) in _MEMBER_ROSTER.get(message.guild.id, {}).items()
            for role_line in [("Administrator" if admin else "Moderator" if mod else ", ".join(r) if r else "regular member")]
        ]
        if roster_lines:
            content += "\n\n" + "\n".join(roster_lines)

    try:
        async with channel.typing():
            reply = await ask_ai(system_prompt + _channel_directory, content)
    except AIError:
        log.exception("AI request failed in thread=%s", channel.id)
        await message.reply("Couldn't reach the AI backend just now. Try again in a bit or tag me again.")
        return

    await _send_ai_reply(channel, reply, system_prompt, reply_to=None if is_new_post else message)


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
    await _add_who_command()
    await interaction.response.defer()
    await post_latest("intl", interaction=interaction)


@bot.tree.command(name="nepalnews", description="Latest Nepal IT/tech news")
@app_commands.checks.cooldown(1, NEWS_COMMAND_COOLDOWN_SECONDS, key=_cooldown_key)
async def nepalnews(interaction: discord.Interaction):
    await _add_who_command()
    await interaction.response.defer()
    await post_latest("nepal", interaction=interaction)


@bot.tree.command(name="who", description="Show a user's roles/permissions")
@app_commands.describe(user="The member to check")
@app_commands.guild_only()
async def who(interaction: discord.Interaction, user: discord.Member):
    """Show a user's role/permission status from the cached server roster."""
    if user.id not in _MEMBER_ROSTER.get(interaction.guild.id, {}):
        await interaction.response.send_message(
            "That user isn't in the bot's member roster (left the server, or the bot couldn't fetch all members).",
            ephemeral=True)
        return
    is_admin, is_mod, roles = _MEMBER_ROSTER[interaction.guild.id][user.id]
    parts = []
    if is_admin:
        parts.append("Administrator")
    elif is_mod:
        parts.append("Moderator")
    else:
        parts.append("Member")
    if roles:
        parts.append("Roles: " + ", ".join(roles))
    await interaction.response.send_message(
        f"{user.mention} is **{', '.join(parts)}** ({user.name}).", ephemeral=True)


async def _add_who_command():
    """Register the /who slash command on the current guild (instant, no global sync)."""
    if not bot.guilds:
        return
    try:
        await bot.tree.sync(guild=discord.Object(id=bot.guilds[0].id))
    except Exception:
        log.exception("Failed to sync /who command")


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
