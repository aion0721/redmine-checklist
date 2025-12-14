import urllib.error
from datetime import datetime, timedelta

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config_manager import load_config, normalize_feeds
from dialogs import ConfigDialog
from feed_client import fetch_feed
from models import Ticket
from storage import load_csv, save_csv


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

        self.show_updated_chk = QCheckBox("更新日を表示")
        self.show_updated_chk.setChecked(False)
        self.show_updated_chk.stateChanged.connect(self.update_column_visibility)

        self.show_done_at_chk = QCheckBox("済日時を表示")
        self.show_done_at_chk.setChecked(False)
        self.show_done_at_chk.stateChanged.connect(self.update_column_visibility)

        self.start_btn = QPushButton("同期開始")
        self.start_btn.clicked.connect(self.toggle_sync)

        self.sync_btn = QPushButton("すぐ同期")
        self.sync_btn.clicked.connect(self.sync_now)

        self.config_btn = QPushButton("設定")
        self.config_btn.clicked.connect(self.open_config_dialog)

        self.reload_btn = QPushButton("設定再読込")
        self.reload_btn.clicked.connect(self.reload_config)

        self.toggle_done_btn = QPushButton("選択一括済/未済切替")
        self.toggle_done_btn.clicked.connect(self.toggle_selected)

        self.save_btn = QPushButton("手動データ保存")
        self.save_btn.clicked.connect(self.save_current)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(8)
        self.tree.setHeaderLabels(
            ["ID", "開く", "済", "済ボタン", "検索文字列", "更新日", "済日時", "件名"]
        )
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        fixed_widths = [80, 50, 50, 80, 100, 180]
        for idx, w in enumerate(fixed_widths):
            header.resizeSection(idx, w)
        self.tree.setSelectionMode(QTreeWidget.MultiSelection)
        self.tree.setSelectionBehavior(QTreeWidget.SelectRows)

        self.build_ui()
        self.refresh_table()
        self.init_tray()
        self.update_column_visibility()

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
        top.addWidget(self.show_updated_chk)
        top.addWidget(self.show_done_at_chk)
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

        layout.addWidget(self.tree)

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
                f_id = feed.get("id", "")
                f_title = feed.get("title", "feed")
                f_url = feed.get("url") or feed.get("feed_url")
                f_search = feed.get("search", "")
                if not f_url:
                    continue
                fetched = fetch_feed(f_url, api_key, f_id, f_title, f_search)
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
                    feed_id=t.feed_id or existing[t.ticket_id].feed_id,
                    feed_title=t.feed_title or existing[t.ticket_id].feed_title,
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
        self.tree.clear()
        items_all = sorted(self.tickets.values(), key=lambda t: (t.feed_id, t.ticket_id))
        # 未済件数はフィルタに関係なくカウント
        pending_counts: dict[str, int] = {}
        for t in items_all:
            key = t.feed_id or "feed"
            if not t.done:
                pending_counts[key] = pending_counts.get(key, 0) + 1

        display_items = [t for t in items_all if not (self.only_open_chk.isChecked() and t.done)]

        # フィルタ後の表示対象をグループ化
        display_groups: dict[str, list[Ticket]] = {}
        for t in display_items:
            display_groups.setdefault(t.feed_id or "feed", []).append(t)

        # すべてのフィード（未済0件でも表示）
        feed_ids = sorted({t.feed_id or "feed" for t in items_all})
        # feed_id -> title map
        feed_titles_map = {t.feed_id or "feed": t.feed_title or "feed" for t in items_all}
        for feed_id in feed_ids:
            tickets = display_groups.get(feed_id, [])
            pending = pending_counts.get(feed_id, 0)
            title = feed_titles_map.get(feed_id, "feed")
            parent_label = f"{title} (未済{pending}件)"
            parent = QTreeWidgetItem(self.tree, [parent_label])
            parent.setFirstColumnSpanned(True)
            parent.setExpanded(True)
            for t in tickets:
                child = QTreeWidgetItem(
                    [
                        t.ticket_id,
                        "",
                        "済" if t.done else "",
                        "",
                        "有" if t.search_hit else "",
                        t.updated_on,
                        t.done_at or "",
                        t.subject,
                        "",
                        "",
                    ]
                )
                child.setData(0, Qt.UserRole, t.ticket_id)
                for col in (0, 2, 4):
                    child.setTextAlignment(col, Qt.AlignCenter)
                parent.addChild(child)

                open_btn = QPushButton("開く")
                open_btn.clicked.connect(lambda _, tid=t.ticket_id: self.open_ticket(tid))
                self.tree.setItemWidget(child, 1, open_btn)

                done_btn = QPushButton("済切替")
                done_btn.clicked.connect(lambda _, tid=t.ticket_id: self.toggle_done_one(tid))
                self.tree.setItemWidget(child, 3, done_btn)

        self.update_column_visibility()

    def toggle_done_one(self, ticket_id: str) -> None:
        t = self.tickets.get(ticket_id)
        if not t:
            return
        now = datetime.now().isoformat(timespec="seconds")
        t.done = not t.done
        t.done_at = now if t.done else None
        save_csv(self.tickets)
        self.refresh_table()

    def toggle_selected(self) -> None:
        selected = self.tree.selectedItems()
        if not selected:
            QMessageBox.information(self, "未選択", "切り替えるチケットを選択してください。")
            return
        now = datetime.now().isoformat(timespec="seconds")
        changed = 0
        for item in selected:
            # 親（フィード行）には UserRole のIDを持たせていない
            ticket_id = item.data(0, Qt.UserRole)
            if not ticket_id:
                continue
            t = self.tickets.get(ticket_id)
            if not t:
                continue
            t.done = not t.done
            t.done_at = now if t.done else None
            changed += 1
        if changed:
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

    def update_column_visibility(self) -> None:
        # カラムインデックス: 0 ID, 1 開く, 2 済, 3 済ボタン, 4 検索文字列有無, 5 更新日, 6 済日時, 7 件名
        show_updated = self.show_updated_chk.isChecked()
        show_done_at = self.show_done_at_chk.isChecked()
        self.tree.setColumnHidden(5, not show_updated)
        self.tree.setColumnHidden(6, not show_done_at)
