import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Iterable

from constants import ATOM_NS
from models import Ticket


def _split_terms(search: str | Iterable[str]) -> list[str]:
    if not search:
        return []
    if isinstance(search, str):
        parts = [p.strip() for p in search.split(",")]
        return [w.lower() for w in parts if w]
    return [str(w).strip().lower() for w in search if str(w).strip()]


def fetch_feed(
    feed_url: str,
    api_key: str,
    feed_id: str,
    feed_title: str,
    feed_search: str | list[str],
    timeout: int = 15,
) -> list[Ticket]:
    req = urllib.request.Request(feed_url)
    req.add_header("X-Redmine-API-Key", api_key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    tickets: list[Ticket] = []
    terms = _split_terms(feed_search)
    for entry in root.findall("atom:entry", ATOM_NS):
        search_hit = False
        if terms:
            title_text = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").lower()
            content_text = (entry.findtext("atom:content", default="", namespaces=ATOM_NS) or "").lower()
            search_hit = any(term in title_text or term in content_text for term in terms)
        t = Ticket.from_entry(entry, feed_id, feed_title, feed_search, search_hit)
        tickets.append(t)
    return tickets


def fetch_issue_due_date(issue_url: str, api_key: str, timeout: int = 15) -> str:
    """Redmine REST APIから期日（カスタムフィールド名: 期日）を取得する。"""
    detail_url = issue_url.rstrip("/") + ".json?include=journals"
    req = urllib.request.Request(detail_url)
    req.add_header("X-Redmine-API-Key", api_key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    payload = json.loads(data.decode("utf-8"))
    issue = payload.get("issue", {})
    # 期日フィールド（デフォルト）優先
    if issue.get("due_date"):
        return issue.get("due_date") or ""
    for cf in issue.get("custom_fields", []):
        if cf.get("name") == "期日":
            return cf.get("value") or ""
    return ""
