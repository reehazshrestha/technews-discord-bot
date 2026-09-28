import json
import os

SEEN_FILE = os.path.join(os.path.dirname(__file__), "seen.json")


def load_seen() -> dict:
    if not os.path.exists(SEEN_FILE):
        return {}
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def save_seen(seen: dict) -> None:
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2)


def mark_seen(seen: dict, category: str, entry_id: str, cap: int = 500) -> None:
    ids = seen.setdefault(category, [])
    ids.append(entry_id)
    if len(ids) > cap:
        del ids[: len(ids) - cap]
