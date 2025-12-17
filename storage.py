import csv
import os
import json

from constants import DATA_PATH
from models import Ticket


def self_safe_load(value: str | None) -> dict:
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def load_csv() -> dict[str, Ticket]:
    if not os.path.exists(DATA_PATH):
        return {}
    tickets: dict[str, Ticket] = {}
    with open(DATA_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tickets[row["ticket_id"]] = Ticket(
                ticket_id=row["ticket_id"],
                subject=row["subject"],
                status=row["status"],
                updated_on=row["updated_on"],
                due_date=row.get("due_date", ""),
                description=row.get("description", ""),
                custom_fields=self_safe_load(row.get("custom_fields")),
                feed_id=row.get("feed_id", ""),
                feed_title=row.get("feed_title", ""),
                feed_search=row.get("feed_search", ""),
                feed_search_custom=row.get("feed_search_custom", ""),
                url=row.get("url", ""),
                search_hit=row.get("search_hit", "False") == "True",
                done=row.get("done", "False") == "True",
                done_at=row.get("done_at") or None,
            )
    return tickets


def save_csv(tickets: dict[str, Ticket]) -> None:
    fieldnames = [
        "ticket_id",
        "subject",
        "status",
        "updated_on",
        "due_date",
        "description",
        "custom_fields",
        "url",
        "feed_id",
        "feed_title",
        "feed_search",
        "feed_search_custom",
        "search_hit",
        "done",
        "done_at",
    ]
    with open(DATA_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in tickets.values():
            writer.writerow(
                {
                    "ticket_id": t.ticket_id,
                    "subject": t.subject,
                    "status": t.status,
                    "updated_on": t.updated_on,
                    "due_date": t.due_date,
                    "description": t.description,
                    "custom_fields": json.dumps(t.custom_fields or {}, ensure_ascii=False),
                    "url": t.url,
                    "feed_id": t.feed_id,
                    "feed_title": t.feed_title,
                    "feed_search": t.feed_search,
                    "feed_search_custom": t.feed_search_custom,
                    "search_hit": str(t.search_hit),
                    "done": str(t.done),
                    "done_at": t.done_at or "",
                }
            )
