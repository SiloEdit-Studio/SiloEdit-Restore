"""Entry point for PixelRestore."""
import sys
from PySide6.QtWidgets import QApplication
from src.ui.main_window import MainWindow
from src.utils.paths import app_data_dir


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("PixelRestore")
    app.setOrganizationName("PixelRestore")
    app_data_dir().mkdir(parents=True, exist_ok=True)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
