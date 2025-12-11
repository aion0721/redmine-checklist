import csv
import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta

from PySide6.QtCore import QTimer, Qt, Signal, QThread
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

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


class FetchWorker(QThread):
    success = Signal(list)
    http_error = Signal(str)
    failure = Signal(str)

    def __init__(self, feed_url: str, api_key: str, parent=None) -> None:
        super().__init__(parent)
        self.feed_url = feed_url
        self.api_key = api_key

    def run(self) -> None:  # type: ignore[override]
        try:
            fetched = fetch_feed(self.feed_url, self.api_key)
            self.success.emit(fetched)
        except urllib.error.HTTPError as e:
            self.http_error.emit(f"HTTP {e.code}: {e.reason}")
        except Exception as e:  # noqa: BLE001
            self.failure.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Redmine チケット済管理 (PySide6)")

        self.config = load_config()
        self.tickets: dict[str, Ticket] = load_csv()

        self.sync_running = False
        self.next_sync_at: datetime | None = None

        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(self.sync_now)

        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self.update_remaining)

        self.status_label = QLabel("停止中")
        self.remaining_label = QLabel("-")
        self.only_open_chk = QCheckBox("未済のみ表示")
        self.only_open_chk.stateChanged.connect(self.refresh_table)

        self.start_btn = QPushButton("同期開始")
        self.start_btn.clicked.connect(self.toggle_sync)

        self.sync_btn = QPushButton("すぐ同期")
        self.sync_btn.clicked.connect(self.sync_now)

        self.reload_btn = QPushButton("再読込")
        self.reload_btn.clicked.connect(self.reload_config)

        self.toggle_done_btn = QPushButton("選択を済/未済切替")
        self.toggle_done_btn.clicked.connect(self.toggle_selected)

        self.save_btn = QPushButton("手動保存")
        self.save_btn.clicked.connect(self.save_current)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ID", "件名", "ステータス", "更新日", "済", "済日時"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.MultiSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)

        self.build_ui()
        self.refresh_table()

    def build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        top = QHBoxLayout()
        top.addWidget(self.start_btn)
        top.addWidget(self.sync_btn)
        top.addWidget(self.reload_btn)
        top.addWidget(self.only_open_chk)
        top.addWidget(self.toggle_done_btn)
        top.addWidget(self.save_btn)
        top.addStretch(1)
        layout.addLayout(top)

        info = QHBoxLayout()
        info.addWidget(self.status_label)
        info.addWidget(QLabel(" | 次回同期まで: "))
        info.addWidget(self.remaining_label)
        info.addStretch(1)
        layout.addLayout(info)

        layout.addWidget(self.table)

    def reload_config(self) -> None:
        self.config = load_config()
        QMessageBox.information(self, "設定再読込", "config.json を再読込しました。")

    def toggle_sync(self) -> None:
        if self.sync_running:
            self.stop_sync()
        else:
            self.start_sync()

    def start_sync(self) -> None:
        api_key = self.config.get("api_key", "")
        feed_url = self.config.get("feed_url", "")
        if not api_key or api_key == "PUT_YOUR_API_KEY":
            QMessageBox.warning(self, "APIキー未設定", "config.json の api_key を設定してください。")
            return
        if not feed_url:
            QMessageBox.warning(self, "URL未設定", "config.json の feed_url を設定してください。")
            return
        self.sync_running = True
        self.start_btn.setText("同期停止")
        self.status_label.setText("同期待ち")
        self.schedule_sync(0)
        self.countdown_timer.start()

    def stop_sync(self) -> None:
        self.sync_running = False
        self.start_btn.setText("同期開始")
        self.status_label.setText("停止中")
        self.remaining_label.setText("-")
        self.sync_timer.stop()
        self.countdown_timer.stop()

    def schedule_sync(self, delay_ms: int) -> None:
        self.sync_timer.stop()
        self.sync_timer.start(delay_ms)
        self.next_sync_at = datetime.now() + timedelta(milliseconds=delay_ms)

    def update_remaining(self) -> None:
        if not self.sync_running or not self.next_sync_at:
            self.remaining_label.setText("-")
            return
        remaining = self.next_sync_at - datetime.now()
        if remaining.total_seconds() <= 0:
            self.remaining_label.setText("同期中…")
        else:
            minutes, seconds = divmod(int(remaining.total_seconds()), 60)
            self.remaining_label.setText(f"{minutes:02d}:{seconds:02d}")

    def sync_now(self) -> None:
        feed_url = self.config.get("feed_url", "")
        api_key = self.config.get("api_key", "")
        refresh_minutes = int(self.config.get("refresh_minutes", 30))
        self.status_label.setText("同期中…")

        self.worker = FetchWorker(feed_url, api_key, self)
        self.worker.success.connect(self.on_fetch_success)
        self.worker.http_error.connect(self.on_fetch_http_error)
        self.worker.failure.connect(self.on_fetch_failure)
        self.worker.finished.connect(self.on_fetch_finished)
        self.worker.refresh_minutes = refresh_minutes  # type: ignore[attr-defined]
        self.worker.start()

    def on_fetch_success(self, fetched: list[Ticket]) -> None:
        self.merge_tickets(fetched)
        save_csv(self.tickets)
        self.status_label.setText(f"同期完了（{len(fetched)}件）")

    def on_fetch_http_error(self, msg: str) -> None:
        self.status_label.setText("HTTPエラー")
        QMessageBox.critical(self, "HTTPエラー", msg)

    def on_fetch_failure(self, msg: str) -> None:
        self.status_label.setText("同期失敗")
        QMessageBox.critical(self, "同期失敗", msg)

    def on_fetch_finished(self) -> None:
        refresh_minutes = int(self.config.get("refresh_minutes", 30))
        if self.sync_running:
            delay_ms = max(refresh_minutes, 1) * 60 * 1000
            self.schedule_sync(delay_ms)

    def merge_tickets(self, fetched: list[Ticket]) -> None:
        existing = self.tickets
        for t in fetched:
            if t.ticket_id in existing:
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
        items = sorted(self.tickets.values(), key=lambda t: t.ticket_id)
        display_items = [t for t in items if not (self.only_open_chk.isChecked() and t.done)]
        self.table.setRowCount(len(display_items))
        for row, t in enumerate(display_items):
            values = [
                t.ticket_id,
                t.subject,
                t.status,
                t.updated_on,
                "済" if t.done else "",
                t.done_at or "",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                if col == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                if col == 4:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()

    def toggle_selected(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(self, "未選択", "切り替えるチケットを選択してください。")
            return
        now = datetime.now().isoformat(timespec="seconds")
        changed = 0
        for model_index in selected:
            ticket_id = self.table.item(model_index.row(), 0).text()
            t = self.tickets.get(ticket_id)
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
        QMessageBox.information(self, "保存完了", "tickets.csv を保存しました。")


def main() -> None:
    app = QApplication([])
    window = MainWindow()
    window.resize(960, 640)
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
