import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from constants import ATOM_NS


@dataclass
class Ticket:
    ticket_id: str
    subject: str
    status: str
    updated_on: str
    due_date: str = ""
    description: str = ""
    custom_fields: dict[str, str] | None = None
    url: str = ""
    feed_id: str = ""
    feed_title: str = ""
    feed_search: str = ""
    feed_search_custom: str = ""
    search_hit: bool = False
    done: bool = False
    done_at: str | None = None

    @classmethod
    def from_entry(
        cls, entry: ET.Element, feed_id: str, feed_title: str, feed_search: str, search_hit: bool
    ) -> "Ticket":
        raw_title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
        updated = (entry.findtext("atom:updated", default="", namespaces=ATOM_NS) or "").strip()
        entry_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS) or ""
        category = entry.find("atom:category", ATOM_NS)
        status = category.get("term") if category is not None and category.get("term") else "unknown"
        url = entry_id  # Redmineのatom:idはチケットURLが入るケースが多い
        ticket_id = extract_ticket_id(entry, raw_title)
        subject = extract_subject(raw_title)
        return cls(
            ticket_id=ticket_id,
            subject=subject,
            status=status,
            updated_on=updated,
            url=url,
            feed_id=feed_id,
            feed_title=feed_title,
            feed_search=feed_search,
            search_hit=search_hit,
        )


def extract_ticket_id(entry: ET.Element, title_text: str) -> str:
    entry_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS) or ""
    for candidate in (entry_id, title_text):
        match = re.search(r"#(\d+)", candidate)
        if match:
            return match.group(1)
    return entry_id or title_text or "unknown"


def extract_subject(title_text: str) -> str:
    # Redmine Atom のタイトルは "Project - Tracker #1234: Subject" 形式が多い
    if ": " in title_text:
        return title_text.split(": ", 1)[1]
    return title_text
