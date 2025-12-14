import json
import os
from typing import Any

from constants import CONFIG_PATH


def load_config() -> dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        default_conf = {
            "api_key": "PUT_YOUR_API_KEY",
            "refresh_minutes": 30,
            "feeds": [
                {
                    "title": "Demo feed",
                    "url": "https://redmine.example.com/projects/demo/issues.atom",
                    "search": "",
                }
            ],
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(default_conf, f, indent=2)
        return default_conf
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_feeds(cfg: dict) -> list[dict]:
    feeds = cfg.get("feeds")
    if isinstance(feeds, list) and feeds:
        normed = []
        for f in feeds:
            if not isinstance(f, dict):
                continue
            title = f.get("title") or f.get("name") or "feed"
            url = f.get("url") or f.get("feed_url") or ""
            search = f.get("search", "")
            if url:
                normed.append({"title": title, "url": url, "search": search})
        if normed:
            return normed
    # backward compatibility: single feed_url
    if cfg.get("feed_url"):
        return [{"title": "default", "url": cfg["feed_url"], "search": ""}]
    return []


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
