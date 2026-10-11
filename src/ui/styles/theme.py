APP_STYLE = """
* { font-family: 'Segoe UI'; color: #eaf0ff; }
QMainWindow, QWidget#root { background: #080f20; }
QDialog#imageViewer { background: #080f20; }
QWidget#sidebar { background: #0c162a; border-right: 1px solid #1d3157; }
QLabel#brand { color: white; font-size: 20px; font-weight: 700; }
QLabel#muted { color: #93a9d2; }
QPushButton { background: #142442; border: 1px solid #263d68; border-radius: 10px; padding: 10px 14px; color: #eaf0ff; }
QPushButton:hover { background: #1c3561; border-color: #5577ff; }
QPushButton:pressed { background: #263d68; }
QPushButton:disabled { color: #667a9f; background: #101a2e; border-color: #1b2b49; }
QPushButton#primary { background: #5364ff; border: 0; font-weight: 600; }
QPushButton#primary:hover { background: #6878ff; }
QPushButton#nav { text-align: left; border: 0; background: transparent; padding: 12px; }
QPushButton#nav:checked { background: #223e75; border-left: 3px solid #7365ff; }
QFrame#card { background: #101d35; border: 1px solid #233b67; border-radius: 16px; }
QLineEdit, QComboBox { background: #0b1529; border: 1px solid #263d68; border-radius: 8px; padding: 8px 10px; selection-background-color: #405a91; }
QLineEdit:focus, QComboBox:focus { border-color: #7186ff; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 26px; border: 0; }
QComboBox QAbstractItemView { background: #101d35; color: #eaf0ff; border: 1px solid #263d68; outline: 0; selection-background-color: #263f70; selection-color: #ffffff; }
QComboBox QAbstractItemView::item { min-height: 28px; padding: 4px 10px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:unchecked { background: #0b1529; border: 1px solid #40577e; border-radius: 4px; }
QCheckBox::indicator:checked { background: #5868f2; border: 1px solid #8791ff; border-radius: 4px; }
QProgressBar { background: #1b2b4e; border: 0; border-radius: 6px; height: 12px; text-align: center; }
QProgressBar::chunk { background: #5967ff; border-radius: 6px; }
QTableWidget { background: #0b162b; alternate-background-color: #0e1b31; border: 1px solid #20385f; border-radius: 10px; gridline-color: #1d3152; outline: 0; selection-background-color: #304b7d; selection-color: #ffffff; }
QTableWidget::item { padding: 5px 7px; border: 0; }
QTableWidget::item:selected { background: #304b7d; color: #ffffff; }
QHeaderView::section { background: #132440; color: #b1c4e8; border: 0; border-right: 1px solid #20385f; padding: 8px; }
QScrollArea { background: #0b1529; border: 1px solid #263d68; border-radius: 10px; }
QToolTip { background: #172640; color: #f3f6ff; border: 1px solid #40577e; padding: 5px; }
QScrollBar:horizontal { height: 10px; background: #0a1427; }
QScrollBar:vertical { width: 10px; background: #0a1427; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #324c7c; border-radius: 4px; }
"""
