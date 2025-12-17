import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
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

from config_manager import load_config, normalize_feeds, save_config
from dialogs import ConfigDialog
from feed_client import _split_terms, fetch_feed, fetch_issue_details
from models import Ticket
from storage import load_csv, save_csv
from ui_columns import COLUMNS


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Redmine チケット済管理")

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
        self.logo_icon = self._load_logo_icon()
        if self.logo_icon:
            self.setWindowIcon(self.logo_icon)

        self.status_label = QLabel("停止中")
        self.remaining_label = QLabel("-")
        self.only_open_chk = QCheckBox("未済に絞る")
        self.only_open_chk.stateChanged.connect(self.handle_only_open_changed)

        self.refetch_pending_btn = QPushButton("未済データ再取得（API）")
        self.refetch_pending_btn.clicked.connect(self.refetch_pending_api)

        self.start_btn = QPushButton("同期開始")
        self.start_btn.clicked.connect(self.toggle_sync)

        self.sync_btn = QPushButton("すぐ同期")
        self.sync_btn.clicked.connect(self.sync_now)

        self.config_btn = QPushButton("設定")
        self.config_btn.clicked.connect(self.open_config_dialog)

        self.help_btn = QPushButton("ヘルプ")
        self.help_btn.clicked.connect(self.open_help)

        self.toggle_done_btn = QPushButton("選択一括済/未済切替")
        self.toggle_done_btn.clicked.connect(self.toggle_selected)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS.labels))
        self.tree.setHeaderLabels(list(COLUMNS.labels))
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        for idx, w in enumerate(COLUMNS.widths):
            header.resizeSection(idx, w)
        self.tree.setSelectionMode(QTreeWidget.MultiSelection)
        self.tree.setSelectionBehavior(QTreeWidget.SelectRows)
        self.tree.itemDoubleClicked.connect(self.handle_item_double_clicked)

        self.build_ui()
        self.apply_config_settings()
        self.init_tray()
        self.update_column_visibility()
        QTimer.singleShot(0, self.start_sync)

    def build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        top = QHBoxLayout()
        top.addWidget(self.start_btn)
        top.addWidget(self.sync_btn)
        top.addWidget(self.config_btn)
        top.addWidget(self.help_btn)
        top.addWidget(self.only_open_chk)
        top.addWidget(self.refetch_pending_btn)
        top.addWidget(self.toggle_done_btn)
        top.addStretch(1)
        layout.addLayout(top)

        info = QHBoxLayout()
        info.addWidget(self.status_label)
        info.addWidget(QLabel(" | 次回同期まで: "))
        info.addWidget(self.remaining_label)
        info.addWidget(QLabel(" | ダブルクリックで明細を開く"))
        info.addStretch(1)
        layout.addLayout(info)

        layout.addWidget(self.tree)

    def init_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        style = QApplication.style()
        icon = self.logo_icon if self.logo_icon and not self.logo_icon.isNull() else style.standardIcon(QStyle.SP_DesktopIcon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setIcon(icon)
        self.tray.setToolTip("Redmine チケット済管理")
        self.tray.show()

    def reload_config(self, show_message: bool = True) -> None:
        self.config = load_config()
        self.apply_config_settings()
        if show_message:
            QMessageBox.information(self, "設定再読込", "config.json を再読込しました。")

    def open_config_dialog(self) -> None:
        dlg = ConfigDialog(self.config, self)
        if dlg.exec() == QDialog.Accepted:
            self.reload_config(show_message=False)
            QMessageBox.information(self, "設定保存", "config.json を保存しました。")

    def open_help(self) -> None:
        QDesktopServices.openUrl(QUrl("https://www.redmine.org/guide"))

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
        self.status_label.setText("同期中")
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
            total_fetched, total_new, total_updated = self._sync_feeds(feeds, api_key)
            save_csv(self.tickets)
            self.status_label.setText(f"同期完了: {total_fetched}件")
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

    def _sync_feeds(self, feeds: list[dict], api_key: str) -> tuple[int, int, int]:
        total_fetched = 0
        total_new = 0
        total_updated = 0
        detail_targets: set[str] = set()
        for feed in feeds:
            f_id = feed.get("id", "")
            f_title = feed.get("title", "feed")
            f_url = feed.get("url") or feed.get("feed_url")
            f_search = feed.get("search", "")
            f_search_custom = feed.get("search_custom", "")
            if not f_url:
                continue
            fetched = fetch_feed(f_url, api_key, f_id, f_title, f_search)
            total_fetched += len(fetched)
            new_cnt, updated_cnt, targets = self.merge_tickets(fetched, f_search_custom)
            total_new += new_cnt
            total_updated += updated_cnt
            detail_targets.update(targets)

        if self.config.get("enable_api_details", False) and detail_targets:
            self._update_details(detail_targets, api_key)

        return total_fetched, total_new, total_updated

    def merge_tickets(self, fetched: list[Ticket], feed_search_custom: str) -> tuple[int, int, set[str]]:
        existing = self.tickets
        new_cnt = 0
        updated_cnt = 0
        detail_targets: set[str] = set()
        for t in fetched:
            if t.ticket_id in existing:
                done = existing[t.ticket_id].done
                done_at = existing[t.ticket_id].done_at
                prev = existing[t.ticket_id]
                existing[t.ticket_id] = Ticket(
                    ticket_id=t.ticket_id,
                    subject=t.subject,
                    status=t.status,
                    updated_on=t.updated_on,
                    url=t.url or prev.url,
                    feed_id=t.feed_id or prev.feed_id,
                    feed_title=t.feed_title or prev.feed_title,
                    feed_search=t.feed_search or prev.feed_search,
                    feed_search_custom=feed_search_custom or prev.feed_search_custom,
                    search_hit=t.search_hit,
                    due_date=prev.due_date,
                    description=prev.description,
                    custom_fields=prev.custom_fields,
                    done=done,
                    done_at=done_at,
                )
                if t.updated_on != prev.updated_on:
                    updated_cnt += 1
                    detail_targets.add(t.ticket_id)
            else:
                t.feed_search_custom = feed_search_custom
                existing[t.ticket_id] = t
                new_cnt += 1
                detail_targets.add(t.ticket_id)
        self.refresh_table()
        return new_cnt, updated_cnt, detail_targets

    def _update_details(self, ticket_ids: Iterable[str], api_key: str) -> None:
        for tid in ticket_ids:
            t = self.tickets.get(tid)
            if not t or not t.url:
                continue
            try:
                details = fetch_issue_details(t.url, api_key)
                if details.get("due_date"):
                    t.due_date = details["due_date"]
                if details.get("description") is not None:
                    t.description = details["description"]
                if details.get("custom_fields") is not None:
                    t.custom_fields = details["custom_fields"]
                terms = _split_terms(t.feed_search)
                custom_targets = _split_terms(t.feed_search_custom)
                if terms and (t.description or t.custom_fields):
                    desc = (t.description or "").lower()
                    hit = any(term in desc for term in terms)
                    if not hit and custom_targets and t.custom_fields:
                        for name in custom_targets:
                            val = (t.custom_fields or {}).get(name, "").lower()
                            if any(term in val for term in terms):
                                hit = True
                                break
                    t.search_hit = t.search_hit or hit
            except Exception:
                continue
        self.refresh_table()

    def refresh_table(self) -> None:
        self.tree.clear()
        if self.config.get("sort_by_due", False):
            def sort_key(t: Ticket) -> tuple:
                due_key = t.due_date or "9999-99-99T99:99:99"
                return (due_key, t.feed_id, t.ticket_id)
        else:
            def sort_key(t: Ticket) -> tuple:
                return (t.feed_id, t.ticket_id)

        items_all = sorted(self.tickets.values(), key=sort_key)

        pending_counts: dict[str, int] = {}
        for t in items_all:
            key = t.feed_id or "feed"
            if not t.done:
                pending_counts[key] = pending_counts.get(key, 0) + 1

        display_items = [t for t in items_all if not (self.only_open_chk.isChecked() and t.done)]

        display_groups: dict[str, list[Ticket]] = {}
        for t in display_items:
            display_groups.setdefault(t.feed_id or "feed", []).append(t)

        feed_ids = sorted({t.feed_id or "feed" for t in items_all})
        feed_titles_map = {t.feed_id or "feed": t.feed_title or "feed" for t in items_all}
        for feed_id in feed_ids:
            tickets = display_groups.get(feed_id, [])
            pending = pending_counts.get(feed_id, 0)
            title = feed_titles_map.get(feed_id, "feed")
            parent_label = f"{title} (未済 {pending}件)"
            parent = QTreeWidgetItem(self.tree, [parent_label])
            parent.setFirstColumnSpanned(True)
            parent.setExpanded(True)
            for t in tickets:
                child = QTreeWidgetItem(
                    [
                        t.ticket_id,
                        "",  # 開くボタン
                        "済" if t.done else "",
                        "",  # 済ボタン
                        "○" if t.search_hit else "",
                        t.updated_on,
                        t.done_at or "",
                        t.due_date,
                        t.subject,
                    ]
                )
                child.setData(0, Qt.UserRole, t.ticket_id)
                for col in (COLUMNS.IDX_ID, COLUMNS.IDX_DONE, COLUMNS.IDX_SEARCH_HIT):
                    child.setTextAlignment(col, Qt.AlignCenter)
                parent.addChild(child)

                open_btn = QPushButton("開く")
                open_btn.clicked.connect(lambda _, tid=t.ticket_id: self.open_ticket(tid))
                self.tree.setItemWidget(child, COLUMNS.IDX_OPEN, open_btn)

                done_btn = QPushButton("済切替")
                done_btn.clicked.connect(lambda _, tid=t.ticket_id: self.toggle_done_one(tid))
                self.tree.setItemWidget(child, COLUMNS.IDX_TOGGLE, done_btn)

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

    def set_done(self, ticket_id: str) -> None:
        t = self.tickets.get(ticket_id)
        if not t:
            QMessageBox.information(self, "未選択", "チケットが見つかりません。")
            return
        if t.done:
            return
        now = datetime.now().isoformat(timespec="seconds")
        t.done = True
        t.done_at = now
        save_csv(self.tickets)
        self.refresh_table()

    def handle_item_double_clicked(self, item: QTreeWidgetItem, _: int) -> None:
        ticket_id = item.data(0, Qt.UserRole)
        if not ticket_id:
            return
        t = self.tickets.get(ticket_id)
        if not t:
            QMessageBox.information(self, "未選択", "チケットが見つかりません。")
            return
        if not t.url:
            QMessageBox.information(self, "URLなし", "チケットのURLがありません。")
            return

        QDesktopServices.openUrl(QUrl(t.url))
        res = QMessageBox.question(
            self,
            "済にしますか",
            f"この明細を済にしますか？\nID: {t.ticket_id}\n件名: {t.subject}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if res == QMessageBox.Yes:
            self.set_done(ticket_id)

    def handle_only_open_changed(self, state: int) -> None:
        self.config["only_open"] = bool(state)
        save_config(self.config)
        self.refresh_table()

    def refetch_pending_api(self) -> None:
        api_key = self.config.get("api_key", "")
        if not self.config.get("enable_api_details", False):
            QMessageBox.information(self, "API無効", "「追加情報取得（API）」をオンにしてください。")
            return
        if not api_key or api_key == "PUT_YOUR_API_KEY":
            QMessageBox.warning(self, "APIキー未設定", "config.json の api_key を設定してください。")
            return
        pending_ids = {t.ticket_id for t in self.tickets.values() if not t.done and t.url}
        if not pending_ids:
            QMessageBox.information(self, "対象なし", "未済のチケットがありません。")
            return
        self._update_details(pending_ids, api_key)
        save_csv(self.tickets)
        QMessageBox.information(self, "再取得完了", "未済チケットの追加情報を再取得しました。")

    def apply_config_settings(self) -> None:
        self.only_open_chk.blockSignals(True)
        self.only_open_chk.setChecked(bool(self.config.get("only_open", False)))
        self.only_open_chk.blockSignals(False)

        # ボタン有効/無効切替（API取得設定に追従）
        self.refetch_pending_btn.setEnabled(bool(self.config.get("enable_api_details", False)))

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

    def notify_change(self, new_cnt: int, updated_cnt: int) -> None:
        if not self.tray:
            return
        lines = []
        if new_cnt:
            lines.append(f"新規 {new_cnt} 件")
        if updated_cnt:
            lines.append(f"更新 {updated_cnt} 件")
        body = " / ".join(lines) if lines else "更新があります。"
        self.tray.showMessage("Redmine 更新通知", body, QSystemTrayIcon.Information, 5000)

    def update_column_visibility(self) -> None:
        show_updated = bool(self.config.get("show_updated", False))
        show_done_at = bool(self.config.get("show_done_at", False))
        self.tree.setColumnHidden(COLUMNS.IDX_UPDATED, not show_updated)
        self.tree.setColumnHidden(COLUMNS.IDX_DONE_AT, not show_done_at)

    def _load_logo_icon(self) -> QIcon | None:
        candidates = [
            Path(__file__).resolve().parent / "logo.png",
            Path.cwd() / "logo.png",
        ]
        for path in candidates:
            if path.exists():
                icon = QIcon(str(path))
                if not icon.isNull():
                    return icon
        return None
