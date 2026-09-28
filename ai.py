import asyncio
import logging

import aiohttp

from config import (
    AI_MAX_CONCURRENT_REQUESTS,
    AI_MODEL,
    AI_REQUEST_TIMEOUT_SECONDS,
    GEMINI_API_BASE,
)

log = logging.getLogger("it-news-bot.ai")

_semaphore = asyncio.Semaphore(AI_MAX_CONCURRENT_REQUESTS)

CODING_HELP_SYSTEM_PROMPT = (
    "You are a helpful programming assistant answering a student's question in a Discord "
    "coding-help forum. Explain clearly and concisely, include a short code example only if "
    "it helps. Keep the whole reply under 1800 characters. Use Discord markdown (```lang blocks``` "
    "for code, ** for bold)."
)

CODE_REVIEW_SYSTEM_PROMPT = (
    "You are a code reviewer answering in a Discord code-review forum. Review the posted code: "
    "point out real bugs, security issues, and concrete improvements. If the code is fine, say so "
    "briefly. Be direct, no filler praise. Keep the whole reply under 1800 characters. Use Discord "
    "markdown (```lang blocks``` for code snippets)."
)

GENERAL_CHAT_SYSTEM_PROMPT = (
    "You are a helpful Discord server assistant, replying because a user @mentioned you. Answer "
    "clearly and conversationally, on whatever topic they ask about (not limited to code). Keep the "
    "whole reply under 1800 characters. Use Discord markdown where useful."
)


class AIError(Exception):
    pass


async def ask_ai(system_prompt: str, user_content: str) -> str:
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
    }
    url = f"{GEMINI_API_BASE}/v1/chat/completions"
    timeout = aiohttp.ClientTimeout(total=AI_REQUEST_TIMEOUT_SECONDS)

    async with _semaphore:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.warning("AI backend returned %s: %s", resp.status, body[:300])
                        raise AIError(f"backend returned status {resp.status}")
                    data = await resp.json()
        except asyncio.TimeoutError as exc:
            raise AIError("request timed out") from exc
        except aiohttp.ClientError as exc:
            raise AIError(f"request failed: {exc}") from exc

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError("unexpected response shape from backend") from exc
