import csv
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from tkinter import BooleanVar, IntVar, StringVar, Tk, messagebox, ttk


CONFIG_PATH = "config.json"
DATA_PATH = "tickets.csv"

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class Ticket:
    ticket_id: str
    subject: str
    status: str
    updated_on: str
    done: bool = False
    done_at: str | None = None

    @classmethod
    def from_entry(cls, entry: ET.Element) -> "Ticket":
        raw_title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
        updated = (entry.findtext("atom:updated", default="", namespaces=ATOM_NS) or "").strip()
        category = entry.find("atom:category", ATOM_NS)
        status = category.get("term") if category is not None and category.get("term") else "unknown"
        ticket_id = extract_ticket_id(entry, raw_title)
        subject = extract_subject(raw_title)
        return cls(ticket_id=ticket_id, subject=subject, status=status, updated_on=updated)


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


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        default_conf = {
            "feed_url": "https://redmine.example.com/projects/demo/issues.atom",
            "api_key": "PUT_YOUR_API_KEY",
            "refresh_minutes": 30,
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(default_conf, f, indent=2)
        return default_conf
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_feed(feed_url: str, api_key: str, timeout: int = 15) -> list[Ticket]:
    req = urllib.request.Request(feed_url)
    req.add_header("X-Redmine-API-Key", api_key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    tickets: list[Ticket] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        tickets.append(Ticket.from_entry(entry))
    return tickets


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
                done=row.get("done", "False") == "True",
                done_at=row.get("done_at") or None,
            )
    return tickets


def save_csv(tickets: dict[str, Ticket]) -> None:
    fieldnames = ["ticket_id", "subject", "status", "updated_on", "done", "done_at"]
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
                    "done": str(t.done),
                    "done_at": t.done_at or "",
                }
            )


class App:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Redmine チケット済管理")
        self.config = load_config()

        self.tickets: dict[str, Ticket] = load_csv()
        self.sync_running = False
        self.next_sync_at: datetime | None = None
        self.after_id: str | None = None
        self.countdown_id: str | None = None
        self.lock = threading.Lock()

        self.status_text = StringVar(value="停止中")
        self.remaining_text = StringVar(value="-")
        self.filter_only_open = BooleanVar(value=False)

        self._build_ui()
        self.refresh_table()

    def _build_ui(self) -> None:
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill="both", expand=True)

        # Control row
        ctrl = ttk.Frame(frm)
        ctrl.pack(fill="x", pady=(0, 8))

        self.start_btn = ttk.Button(ctrl, text="同期開始", command=self.toggle_sync)
        self.start_btn.pack(side="left", padx=(0, 4))

        self.sync_btn = ttk.Button(ctrl, text="すぐ同期", command=self.sync_now)
        self.sync_btn.pack(side="left", padx=(0, 4))

        ttk.Button(ctrl, text="再読込", command=self.reload_config).pack(side="left", padx=(0, 4))

        ttk.Checkbutton(ctrl, text="未済のみ表示", variable=self.filter_only_open, command=self.refresh_table).pack(
            side="left", padx=(8, 0)
        )

        ttk.Button(ctrl, text="選択を済/未済切替", command=self.toggle_selected).pack(side="left", padx=(8, 0))

        ttk.Button(ctrl, text="手動保存", command=self.save_current).pack(side="left", padx=(8, 0))

        info = ttk.Frame(frm)
        info.pack(fill="x", pady=(0, 8))
        ttk.Label(info, textvariable=self.status_text).pack(side="left")
        ttk.Label(info, text=" | 次回同期まで: ").pack(side="left")
        ttk.Label(info, textvariable=self.remaining_text).pack(side="left")

        # Table
        columns = ("ticket_id", "subject", "status", "updated_on", "done", "done_at")
        self.tree = ttk.Treeview(frm, columns=columns, show="headings", height=18)
        self.tree.heading("ticket_id", text="ID")
        self.tree.heading("subject", text="件名")
        self.tree.heading("status", text="ステータス")
        self.tree.heading("updated_on", text="更新日")
        self.tree.heading("done", text="済")
        self.tree.heading("done_at", text="済日時")
        self.tree.column("ticket_id", width=80, anchor="center")
        self.tree.column("subject", width=320)
        self.tree.column("status", width=120, anchor="center")
        self.tree.column("updated_on", width=160, anchor="center")
        self.tree.column("done", width=50, anchor="center")
        self.tree.column("done_at", width=160, anchor="center")
        self.tree.pack(fill="both", expand=True)

    def reload_config(self) -> None:
        self.config = load_config()
        messagebox.showinfo("設定再読込", "config.json を再読込しました。")

    def toggle_sync(self) -> None:
        if self.sync_running:
            self.stop_sync()
        else:
            self.start_sync()

    def start_sync(self) -> None:
        api_key = self.config.get("api_key", "")
        feed_url = self.config.get("feed_url", "")
        if not api_key or api_key == "PUT_YOUR_API_KEY":
            messagebox.showwarning("APIキー未設定", "config.json の api_key を設定してください。")
            return
        if not feed_url:
            messagebox.showwarning("URL未設定", "config.json の feed_url を設定してください。")
            return
        self.sync_running = True
        self.start_btn.config(text="同期停止")
        self.status_text.set("同期待ち")
        self.schedule_sync(0)
        self.schedule_countdown()

    def stop_sync(self) -> None:
        self.sync_running = False
        self.start_btn.config(text="同期開始")
        self.status_text.set("停止中")
        self.remaining_text.set("-")
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        if self.countdown_id:
            self.root.after_cancel(self.countdown_id)
            self.countdown_id = None

    def schedule_sync(self, delay_ms: int) -> None:
        if self.after_id:
            self.root.after_cancel(self.after_id)
        self.after_id = self.root.after(delay_ms, self.sync_now)
        self.next_sync_at = datetime.now() + timedelta(milliseconds=delay_ms)

    def schedule_countdown(self) -> None:
        if self.countdown_id:
            self.root.after_cancel(self.countdown_id)
        self.countdown_id = self.root.after(1000, self._update_remaining)

    def _update_remaining(self) -> None:
        if not self.sync_running or not self.next_sync_at:
            self.remaining_text.set("-")
            return
        remaining = self.next_sync_at - datetime.now()
        if remaining.total_seconds() <= 0:
            self.remaining_text.set("同期中…")
        else:
            minutes, seconds = divmod(int(remaining.total_seconds()), 60)
            self.remaining_text.set(f"{minutes:02d}:{seconds:02d}")
        self.schedule_countdown()

    def sync_now(self) -> None:
        if not self.sync_running:
            # 手動同期は停止中でも許可
            pass
        feed_url = self.config.get("feed_url", "")
        api_key = self.config.get("api_key", "")
        refresh_minutes = int(self.config.get("refresh_minutes", 30))
        self.status_text.set("同期中…")
        self.root.update_idletasks()

        def worker() -> None:
            try:
                fetched = fetch_feed(feed_url, api_key)
                self.merge_tickets(fetched)
                save_csv(self.tickets)
                self.status_text.set(f"同期完了（{len(fetched)}件）")
            except urllib.error.HTTPError as e:
                self.status_text.set(f"HTTPエラー: {e.code}")
                messagebox.showerror("HTTPエラー", str(e))
            except Exception as e:  # noqa: BLE001
                self.status_text.set("同期失敗")
                messagebox.showerror("同期失敗", str(e))
            finally:
                if self.sync_running:
                    delay_ms = max(refresh_minutes, 1) * 60 * 1000
                    self.schedule_sync(delay_ms)

        threading.Thread(target=worker, daemon=True).start()

    def merge_tickets(self, fetched: list[Ticket]) -> None:
        with self.lock:
            existing = self.tickets
            for t in fetched:
                if t.ticket_id in existing:
                    # 済フラグは維持し、他フィールドを更新
                    done = existing[t.ticket_id].done
                    done_at = existing[t.ticket_id].done_at
                    existing[t.ticket_id] = Ticket(
                        ticket_id=t.ticket_id,
                        subject=t.subject,
                        status=t.status,
                        updated_on=t.updated_on,
                        done=done,
                        done_at=done_at,
                    )
                else:
                    existing[t.ticket_id] = t
            self.refresh_table()

    def refresh_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        items = sorted(self.tickets.values(), key=lambda t: t.ticket_id)
        for t in items:
            if self.filter_only_open.get() and t.done:
                continue
            self.tree.insert(
                "",
                "end",
                iid=t.ticket_id,
                values=(t.ticket_id, t.subject, t.status, t.updated_on, "済" if t.done else "", t.done_at or ""),
            )

    def toggle_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("未選択", "切り替えるチケットを選択してください。")
            return
        now = datetime.now().isoformat(timespec="seconds")
        changed = 0
        with self.lock:
            for iid in selection:
                t = self.tickets.get(iid)
                if not t:
                    continue
                t.done = not t.done
                t.done_at = now if t.done else None
                changed += 1
        if changed:
            save_csv(self.tickets)
            self.refresh_table()

    def save_current(self) -> None:
        save_csv(self.tickets)
        messagebox.showinfo("保存完了", "tickets.csv を保存しました。")


def main() -> None:
    root = Tk()
    app = App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
