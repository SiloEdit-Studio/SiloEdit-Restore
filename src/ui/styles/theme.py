APP_STYLE = """
* { font-family: 'Segoe UI'; color: #eaf0ff; }
QMainWindow, QWidget#root { background: #080f20; }
QWidget#sidebar { background: #0c162a; border-right: 1px solid #1d3157; }
QLabel#brand { color: white; font-size: 20px; font-weight: 700; }
QLabel#muted { color: #93a9d2; }
QPushButton { background: #142442; border: 1px solid #263d68; border-radius: 10px; padding: 10px 14px; color: #eaf0ff; }
QPushButton:hover { background: #1c3561; border-color: #5577ff; }
QPushButton#primary { background: #5364ff; border: 0; font-weight: 600; }
QPushButton#primary:hover { background: #6878ff; }
QPushButton#nav { text-align: left; border: 0; background: transparent; padding: 12px; }
QPushButton#nav:checked { background: #223e75; border-left: 3px solid #7365ff; }
QFrame#card { background: #101d35; border: 1px solid #233b67; border-radius: 16px; }
QLineEdit, QComboBox { background: #0b1529; border: 1px solid #263d68; border-radius: 8px; padding: 9px; }
QProgressBar { background: #1b2b4e; border: 0; border-radius: 6px; height: 12px; text-align: center; }
QProgressBar::chunk { background: #5967ff; border-radius: 6px; }
QTableWidget { background: #0b162b; alternate-background-color: #10203b; border: 1px solid #20385f; border-radius: 10px; gridline-color: #1d3152; }
QHeaderView::section { background: #132440; color: #9db3df; border: 0; padding: 8px; }
QScrollBar:vertical { width: 10px; background: #0a1427; }
QScrollBar::handle:vertical { background: #324c7c; border-radius: 4px; }
"""
