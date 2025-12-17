import json
import os
import uuid
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
            "enable_api_details": False,
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(default_conf, f, indent=2)
        return default_conf
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 足りないフィードIDを採番して保存
    updated_feeds = ensure_feed_ids(cfg.get("feeds"))
    if updated_feeds is not None:
        cfg["feeds"] = updated_feeds
        save_config(cfg)
    return cfg


def normalize_feeds(cfg: dict) -> list[dict]:
    feeds = ensure_feed_ids(cfg.get("feeds")) or []
    if isinstance(feeds, list) and feeds:
        normed = []
        for f in feeds:
            if not isinstance(f, dict):
                continue
            feed_id = f.get("id") or ""
            title = f.get("title") or f.get("name") or "feed"
            url = f.get("url") or f.get("feed_url") or ""
            search = f.get("search", "")
            if url:
                normed.append({"id": feed_id, "title": title, "url": url, "search": search})
        if normed:
            return normed
    # backward compatibility: single feed_url
    if cfg.get("feed_url"):
        return [{"id": generate_feed_id(), "title": "default", "url": cfg["feed_url"], "search": ""}]
    return []


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def generate_feed_id() -> str:
    return uuid.uuid4().hex


def ensure_feed_ids(feeds: Any) -> list[dict] | None:
    if not isinstance(feeds, list):
        return None
    updated = False
    new_list: list[dict] = []
    for f in feeds:
        if not isinstance(f, dict):
            continue
        if "id" not in f or not f.get("id"):
            f = {**f, "id": generate_feed_id()}
            updated = True
        new_list.append(f)
    return new_list if updated else feeds
