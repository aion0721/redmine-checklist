from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHeaderView,
    QWidget,
)

from config_manager import normalize_feeds, save_config, generate_feed_id


class FeedEditDialog(QDialog):
    def __init__(self, parent=None, feed: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("フィード編集")
        self.resize(420, 180)

        self.title_edit = QLineEdit(feed.get("title", "") if feed else "")
        self.url_edit = QLineEdit(feed.get("url", "") if feed else "")
        self.search_edit = QLineEdit(feed.get("search", "") if feed else "")
        self.search_custom_edit = QLineEdit(feed.get("search_custom", "") if feed else "")

        form = QFormLayout()
        form.addRow("タイトル", self.title_edit)
        form.addRow("URL", self.url_edit)
        form.addRow("検索キーワード", self.search_edit)
        form.addRow("", QLabel("※カンマ区切りで複数指定できます（OR条件）"))
        form.addRow("検索対象カスタムフィールド", self.search_custom_edit)
        form.addRow("", QLabel("※カンマ区切り（OR）。「追加情報取得（API）」が必要"))

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
            search_custom = self.search_custom_edit.text().strip()
            if not url:
                QMessageBox.warning(self, "URL未入力", "URLを入力してください。")
                return None
            return {"title": title, "url": url, "search": search, "search_custom": search_custom}
        return None


class ConfigDialog(QDialog):
    def __init__(self, cfg: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("設定 (config.json)")
        self.resize(700, 420)
        self.cfg = cfg
        self.feeds: list[dict] = normalize_feeds(cfg)

        self.api_edit = QLineEdit(cfg.get("api_key", ""))
        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(1, 1440)
        self.refresh_spin.setValue(int(cfg.get("refresh_minutes", 30)))

        self.enable_api_chk = QCheckBox("追加情報取得（API）")
        self.enable_api_chk.setChecked(bool(cfg.get("enable_api_details", False)))
        self.show_updated_chk = QCheckBox("更新日を表示")
        self.show_updated_chk.setChecked(bool(cfg.get("show_updated", False)))
        self.show_done_at_chk = QCheckBox("済日時を表示")
        self.show_done_at_chk.setChecked(bool(cfg.get("show_done_at", False)))
        self.sort_by_due_chk = QCheckBox("期日でソート")
        self.sort_by_due_chk.setChecked(bool(cfg.get("sort_by_due", False)))

        self.feed_table = QTableWidget(0, 4)
        self.feed_table.setHorizontalHeaderLabels(["タイトル", "URL", "検索キーワード", "検索対象CF"])
        self.feed_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.feed_table.setSelectionMode(QTableWidget.SingleSelection)
        header = self.feed_table.horizontalHeader()
        header.setStretchLastSection(True)
        self.feed_table.doubleClicked.connect(self.handle_table_double_click)

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
        form.addRow(self.enable_api_chk)
        form.addRow(self.show_updated_chk)
        form.addRow(self.show_done_at_chk)
        form.addRow(self.sort_by_due_chk)

        btns = QHBoxLayout()
        btns.addWidget(add_btn)
        btns.addWidget(edit_btn)
        btns.addWidget(del_btn)
        btns.addStretch(1)

        bottom = QHBoxLayout()
        save_btn = QPushButton("保存")
        close_btn = QPushButton("キャンセル")
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
            self.feed_table.setItem(row, 3, QTableWidgetItem(f.get("search_custom", "")))
        self.feed_table.resizeColumnsToContents()

    def add_feed(self) -> None:
        dlg = FeedEditDialog(self)
        res = dlg.get_result()
        if res:
            res["id"] = generate_feed_id()
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
            res["id"] = self.feeds[row].get("id") or generate_feed_id()
            res["search_custom"] = res.get("search_custom") or self.feeds[row].get("search_custom", "")
            self.feeds[row] = res
            self.load_feeds_into_table()

    def handle_table_double_click(self) -> None:
        self.edit_feed()

    def delete_feed(self) -> None:
        indexes = self.feed_table.selectionModel().selectedRows()
        if not indexes:
            QMessageBox.information(self, "未選択", "削除するフィードを選択してください。")
            return
        row = indexes[0].row()
        del self.feeds[row]
        self.load_feeds_into_table()

    def save_and_close(self) -> None:
        # validate feeds
        if not self.feeds:
            QMessageBox.warning(self, "フィードなし", "少なくとも1件のフィードを登録してください。")
            return
        for f in self.feeds:
            if not f.get("url"):
                QMessageBox.warning(self, "URL未入力", "URLが未入力のフィードがあります。")
                return
        if self.enable_api_chk.isChecked() and not self.api_edit.text().strip():
            QMessageBox.warning(self, "APIキー未入力", "「追加情報取得（API）」がオンの場合、APIキーを入力してください。")
            return
        new_cfg = {
            **self.cfg,  # keep other preferences such as display options
            "api_key": self.api_edit.text().strip(),
            "refresh_minutes": int(self.refresh_spin.value()),
            "feeds": self.feeds,
            "enable_api_details": bool(self.enable_api_chk.isChecked()),
            "show_updated": bool(self.show_updated_chk.isChecked()),
            "show_done_at": bool(self.show_done_at_chk.isChecked()),
            "sort_by_due": bool(self.sort_by_due_chk.isChecked()),
        }
        save_config(new_cfg)
        self.accept()
