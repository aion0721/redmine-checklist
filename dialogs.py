from PySide6.QtWidgets import (
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

from config_manager import normalize_feeds, save_config


class FeedEditDialog(QDialog):
    def __init__(self, parent=None, feed: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("フィード編集")
        self.resize(420, 180)

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
