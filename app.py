"""Entry point for SiloEdit Restore."""
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from src.ui.main_window import MainWindow
from src.utils.paths import app_data_dir


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SiloEdit Restore")
    app.setOrganizationName("SiloEdit Studio")
    app_data_dir().mkdir(parents=True, exist_ok=True)
    icon_path = Path(__file__).resolve().parent / "assets" / "icons" / "SiloEditRestore.ico"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
