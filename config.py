import os
from datetime import time, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
INTL_NEWS_CHANNEL_ID = int(os.getenv("INTL_NEWS_CHANNEL_ID", "0"))
NEPAL_NEWS_CHANNEL_ID = int(os.getenv("NEPAL_NEWS_CHANNEL_ID", "0"))
MAX_POSTS_PER_CHECK = int(os.getenv("MAX_POSTS_PER_CHECK", "10"))
NEWS_COMMAND_COOLDOWN_SECONDS = int(os.getenv("NEWS_COMMAND_COOLDOWN_SECONDS", "1500"))

NEPAL_TZ = timezone(timedelta(hours=5, minutes=45))

_post_time_raw = os.getenv("POST_TIME_NPT", "00:00")
_hour, _minute = (int(x) for x in _post_time_raw.split(":"))
DAILY_POST_TIME_NPT = time(hour=_hour, minute=_minute, tzinfo=NEPAL_TZ)

# Trusted RSS sources. Add/remove entries here to tune coverage.
FEEDS = {
    "intl": [
        {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
        {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml"},
        {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index"},
        {"name": "Wired", "url": "https://www.wired.com/feed/rss"},
        {"name": "ZDNet", "url": "https://www.zdnet.com/news/rss.xml"},
        {"name": "The Register", "url": "https://www.theregister.com/headlines.atom"},
        {"name": "BleepingComputer", "url": "https://www.bleepingcomputer.com/feed/"},
        {"name": "Engadget", "url": "https://www.engadget.com/rss.xml"},
    ],
    "nepal": [
        {"name": "ICT Frame", "url": "https://ictframe.com/feed/"},
        {"name": "Nepali Telecom", "url": "https://www.nepalitelecom.com/feed"},
        {"name": "TechMandu", "url": "https://techmandu.com/feed/"},
    ],
}
