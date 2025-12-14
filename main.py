from PySide6.QtWidgets import QApplication

from main_window import MainWindow


def main() -> None:
    app = QApplication([])
    window = MainWindow()
    window.resize(960, 640)
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
