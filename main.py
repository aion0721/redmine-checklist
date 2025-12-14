import csv
import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
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
    url: str = ""
    feed_title: str = ""
    feed_url: str = ""
    feed_search: str = ""
    search_hit: bool = False
    done: bool = False
    done_at: str | None = None

    @classmethod
    def from_entry(
        cls, entry: ET.Element, feed_title: str, feed_url: str, feed_search: str, search_hit: bool
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
            feed_title=feed_title,
            feed_url=feed_url,
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


def load_config() -> dict:
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


class FeedEditDialog(QDialog):
    def __init__(self, parent=None, feed: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("フィード編集")
        self.resize(420, 180)
        self.feed: dict | None = None

        self.title_edit = QLineEdit(feed.get("title", "") if feed else "")
        self.url_edit = QLineEdit(feed.get("url", "") if feed else "")
        self.search_edit = QLineEdit(feed.get("search", "") if feed else "")

        form = QFormLayout()
        form.addRow("タイトル", self.title_edit)
        form.addRow("URL", self.url_edit)
        form.addRow("検索文字列", self.search_edit)

        btn_box = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("キャンセル")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(ok_btn)
        btn_box.addWidget(cancel_btn)
        btn_box.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(btn_box)

    def get_result(self) -> dict | None:
        if self.exec() == QDialog.Accepted:
            title = self.title_edit.text().strip() or "feed"
            url = self.url_edit.text().strip()
            search = self.search_edit.text().strip()
            if not url:
                QMessageBox.warning(self, "URL未入力", "URLを入力してください。")
                return None
            return {"title": title, "url": url, "search": search}
        return None


class ConfigDialog(QDialog):
    def __init__(self, cfg: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("設定（config.json）")
        self.resize(700, 420)
        self.cfg = cfg
        self.feeds: list[dict] = normalize_feeds(cfg)

        self.api_edit = QLineEdit(cfg.get("api_key", ""))
        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(1, 1440)
        self.refresh_spin.setValue(int(cfg.get("refresh_minutes", 30)))

        self.feed_table = QTableWidget(0, 3)
        self.feed_table.setHorizontalHeaderLabels(["タイトル", "URL", "検索文字列"])
        self.feed_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.feed_table.setSelectionMode(QTableWidget.SingleSelection)
        header = self.feed_table.horizontalHeader()
        header.setStretchLastSection(True)

        self.load_feeds_into_table()

        add_btn = QPushButton("追加")
        add_btn.clicked.connect(self.add_feed)
        edit_btn = QPushButton("編集")
        edit_btn.clicked.connect(self.edit_feed)
        del_btn = QPushButton("削除")
        del_btn.clicked.connect(self.delete_feed)

        form = QFormLayout()
        form.addRow("APIキー", self.api_edit)
        form.addRow("同期間隔(分)", self.refresh_spin)

        btns = QHBoxLayout()
        btns.addWidget(add_btn)
        btns.addWidget(edit_btn)
        btns.addWidget(del_btn)
        btns.addStretch(1)

        bottom = QHBoxLayout()
        save_btn = QPushButton("保存")
        close_btn = QPushButton("閉じる")
        save_btn.clicked.connect(self.save_and_close)
        close_btn.clicked.connect(self.reject)
        bottom.addStretch(1)
        bottom.addWidget(save_btn)
        bottom.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.feed_table)
        layout.addLayout(btns)
        layout.addLayout(bottom)

    def load_feeds_into_table(self) -> None:
        self.feed_table.setRowCount(len(self.feeds))
        for row, f in enumerate(self.feeds):
            self.feed_table.setItem(row, 0, QTableWidgetItem(f.get("title", "")))
            self.feed_table.setItem(row, 1, QTableWidgetItem(f.get("url", "")))
            self.feed_table.setItem(row, 2, QTableWidgetItem(f.get("search", "")))
        self.feed_table.resizeColumnsToContents()

    def add_feed(self) -> None:
        dlg = FeedEditDialog(self)
        res = dlg.get_result()
        if res:
            self.feeds.append(res)
            self.load_feeds_into_table()

    def edit_feed(self) -> None:
        indexes = self.feed_table.selectionModel().selectedRows()
        if not indexes:
            QMessageBox.information(self, "未選択", "編集するフィードを選択してください。")
            return
        row = indexes[0].row()
        dlg = FeedEditDialog(self, self.feeds[row])
        res = dlg.get_result()
        if res:
            self.feeds[row] = res
            self.load_feeds_into_table()

    def delete_feed(self) -> None:
        indexes = self.feed_table.selectionModel().selectedRows()
        if not indexes:
            QMessageBox.information(self, "未選択", "削除するフィードを選択してください。")
            return
        row = indexes[0].row()
        del self.feeds[row]
        self.load_feeds_into_table()

    def save_and_close(self) -> None:
        new_cfg = {
            "api_key": self.api_edit.text().strip(),
            "refresh_minutes": int(self.refresh_spin.value()),
            "feeds": self.feeds,
        }
        save_config(new_cfg)
        self.accept()


def fetch_feed(feed_url: str, api_key: str, feed_title: str, feed_search: str, timeout: int = 15) -> list[Ticket]:
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
            # 件名・説明(content)に含まれているか判定のみ。除外はしない。
            title_text = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").lower()
            content_text = (entry.findtext("atom:content", default="", namespaces=ATOM_NS) or "").lower()
            search_hit = term in title_text or term in content_text
        t = Ticket.from_entry(entry, feed_title, feed_url, feed_search, search_hit)
        tickets.append(t)
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
                feed_title=row.get("feed_title", ""),
                feed_url=row.get("feed_url", ""),
                feed_search=row.get("feed_search", ""),
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
        "url",
        "feed_title",
        "feed_url",
        "feed_search",
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
                    "url": t.url,
                    "feed_title": t.feed_title,
                    "feed_url": t.feed_url,
                    "feed_search": t.feed_search,
                    "search_hit": str(t.search_hit),
                    "done": str(t.done),
                    "done_at": t.done_at or "",
                }
            )


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

        self.tray: QSystemTrayIcon | None = None

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

        self.config_btn = QPushButton("設定")
        self.config_btn.clicked.connect(self.open_config_dialog)

        self.toggle_done_btn = QPushButton("選択を済/未済切替")
        self.toggle_done_btn.clicked.connect(self.toggle_selected)

        self.save_btn = QPushButton("手動保存")
        self.save_btn.clicked.connect(self.save_current)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            ["ID", "件名", "フィード", "ステータス", "更新日", "検索文字列有無", "済", "済日時", "済ボタン", "開く"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.MultiSelection)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        # 固定幅を設定（必要に応じて調整してください）
        fixed_widths = [80, 280, 140, 120, 160, 90, 50, 160, 80, 80]
        for idx, w in enumerate(fixed_widths):
            header.resizeSection(idx, w)
        self.table.verticalHeader().setVisible(False)

        self.build_ui()
        self.refresh_table()
        self.init_tray()

    def build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        top = QHBoxLayout()
        top.addWidget(self.start_btn)
        top.addWidget(self.sync_btn)
        top.addWidget(self.reload_btn)
        top.addWidget(self.config_btn)
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

    def init_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        style = QApplication.style()
        icon = style.standardIcon(QStyle.SP_DesktopIcon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Redmine チケット済管理")
        self.tray.show()

    def reload_config(self) -> None:
        self.config = load_config()
        QMessageBox.information(self, "設定再読込", "config.json を再読込しました。")

    def open_config_dialog(self) -> None:
        dlg = ConfigDialog(self.config, self)
        if dlg.exec() == QDialog.Accepted:
            self.config = load_config()
            QMessageBox.information(self, "設定保存", "config.json を保存しました。")

    def toggle_sync(self) -> None:
        if self.sync_running:
            self.stop_sync()
        else:
            self.start_sync()

    def start_sync(self) -> None:
        api_key = self.config.get("api_key", "")
        feeds = normalize_feeds(self.config)
        if not api_key or api_key == "PUT_YOUR_API_KEY":
            QMessageBox.warning(self, "APIキー未設定", "config.json の api_key を設定してください。")
            return
        if not feeds:
            QMessageBox.warning(self, "URL未設定", "config.json の feeds に URL を設定してください。")
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
        api_key = self.config.get("api_key", "")
        refresh_minutes = int(self.config.get("refresh_minutes", 30))
        feeds = normalize_feeds(self.config)
        self.status_label.setText("同期中…")
        if not feeds:
            QMessageBox.warning(self, "URL未設定", "config.json の feeds に URL を設定してください。")
            self.status_label.setText("同期失敗")
            return
        try:
            total_fetched = 0
            total_new = 0
            total_updated = 0
            for feed in feeds:
                f_title = feed.get("title", "feed")
                f_url = feed.get("url") or feed.get("feed_url")
                f_search = feed.get("search", "")
                if not f_url:
                    continue
                fetched = fetch_feed(f_url, api_key, f_title, f_search)
                total_fetched += len(fetched)
                new_cnt, updated_cnt = self.merge_tickets(fetched)
                total_new += new_cnt
                total_updated += updated_cnt
            save_csv(self.tickets)
            self.status_label.setText(f"同期完了（{total_fetched}件）")
            if self.tray and (total_new or total_updated):
                self.notify_change(total_new, total_updated)
        except urllib.error.HTTPError as e:
            self.status_label.setText("HTTPエラー")
            QMessageBox.critical(self, "HTTPエラー", f"HTTP {e.code}: {e.reason}")
        except Exception as e:  # noqa: BLE001
            self.status_label.setText("同期失敗")
            QMessageBox.critical(self, "同期失敗", str(e))
        finally:
            if self.sync_running:
                delay_ms = max(refresh_minutes, 1) * 60 * 1000
                self.schedule_sync(delay_ms)

    def merge_tickets(self, fetched: list[Ticket]) -> tuple[int, int]:
        existing = self.tickets
        new_cnt = 0
        updated_cnt = 0
        for t in fetched:
            if t.ticket_id in existing:
                done = existing[t.ticket_id].done
                done_at = existing[t.ticket_id].done_at
                prev_updated = existing[t.ticket_id].updated_on
                existing[t.ticket_id] = Ticket(
                    ticket_id=t.ticket_id,
                    subject=t.subject,
                    status=t.status,
                    updated_on=t.updated_on,
                    url=t.url or existing[t.ticket_id].url,
                    feed_title=t.feed_title or existing[t.ticket_id].feed_title,
                    feed_url=t.feed_url or existing[t.ticket_id].feed_url,
                    feed_search=t.feed_search or existing[t.ticket_id].feed_search,
                    search_hit=t.search_hit,
                    done=done,
                    done_at=done_at,
                )
                if t.updated_on != prev_updated:
                    updated_cnt += 1
            else:
                existing[t.ticket_id] = t
                new_cnt += 1
        self.refresh_table()
        return new_cnt, updated_cnt

    def refresh_table(self) -> None:
        items = sorted(self.tickets.values(), key=lambda t: t.ticket_id)
        display_items = [t for t in items if not (self.only_open_chk.isChecked() and t.done)]
        self.table.setRowCount(len(display_items))
        for row, t in enumerate(display_items):
            values = [
                t.ticket_id,
                t.subject,
                t.feed_title,
                t.status,
                t.updated_on,
                "有" if t.search_hit else "",
                "済" if t.done else "",
                t.done_at or "",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                if col in (0, 5, 6):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)

            done_btn = QPushButton("済切替")
            done_btn.clicked.connect(lambda _, tid=t.ticket_id: self.toggle_done_one(tid))
            self.table.setCellWidget(row, 8, done_btn)

            open_btn = QPushButton("開く")
            open_btn.clicked.connect(lambda _, tid=t.ticket_id: self.open_ticket(tid))
            self.table.setCellWidget(row, 9, open_btn)

        # 固定幅設定を維持
        # （ヘッダのリサイズ設定で固定にしているため、ここでは何もしない）

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

    def toggle_done_one(self, ticket_id: str) -> None:
        t = self.tickets.get(ticket_id)
        if not t:
            return
        now = datetime.now().isoformat(timespec="seconds")
        t.done = not t.done
        t.done_at = now if t.done else None
        save_csv(self.tickets)
        self.refresh_table()

    def open_ticket(self, ticket_id: str) -> None:
        t = self.tickets.get(ticket_id)
        if not t:
            QMessageBox.information(self, "未選択", "チケットが見つかりません。")
            return
        if not t.url:
            QMessageBox.information(self, "URLなし", "チケットのURLがありません。")
            return
        QDesktopServices.openUrl(QUrl(t.url))

    def save_current(self) -> None:
        save_csv(self.tickets)
        QMessageBox.information(self, "保存完了", "tickets.csv を保存しました。")

    def notify_change(self, new_cnt: int, updated_cnt: int) -> None:
        if not self.tray:
            return
        lines = []
        if new_cnt:
            lines.append(f"新規 {new_cnt} 件")
        if updated_cnt:
            lines.append(f"更新 {updated_cnt} 件")
        body = " / ".join(lines) if lines else "更新があります"
        self.tray.showMessage("Redmine 更新通知", body, QSystemTrayIcon.Information, 5000)


def main() -> None:
    app = QApplication([])
    window = MainWindow()
    window.resize(960, 640)
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
