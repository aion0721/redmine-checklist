import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from constants import ATOM_NS
from models import Ticket


def fetch_feed(
    feed_url: str, api_key: str, feed_id: str, feed_title: str, feed_search: str, timeout: int = 15
) -> list[Ticket]:
    req = urllib.request.Request(feed_url)
    req.add_header("X-Redmine-API-Key", api_key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    tickets: list[Ticket] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        search_hit = False
        if feed_search:
            term = feed_search.lower()
            title_text = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").lower()
            content_text = (entry.findtext("atom:content", default="", namespaces=ATOM_NS) or "").lower()
            search_hit = term in title_text or term in content_text
        t = Ticket.from_entry(entry, feed_id, feed_title, feed_search, search_hit)
        tickets.append(t)
    return tickets
