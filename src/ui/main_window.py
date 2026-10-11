from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap, QColor, QPalette
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel,
    QStackedWidget, QFileDialog, QFrame, QGridLayout, QProgressBar, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QCheckBox, QLineEdit, QComboBox, QAbstractItemView, QSpacerItem, QSizePolicy,
    QApplication, QDialog, QSlider, QScrollArea)
from src.core.history import History
from src.core.report_generator import export_csv
from src.ui.components.worker import ScanWorker
from src.ui.styles.theme import APP_STYLE
from src.utils.paths import app_data_dir


def preferred_result_path(record):
    return record.get("final_output") or record.get("hat_output") or record.get("output")


class ImageViewerDialog(QDialog):
    def __init__(self, image_path, parent=None):
        super().__init__(parent)
        self.image_path = Path(image_path)
        self.original_pixmap = QPixmap(str(self.image_path))
        self.setObjectName("imageViewer")
        self.setWindowTitle(self.image_path.name)
        self.resize(1100, 760)
        self.setMinimumSize(640, 480)

        layout = QVBoxLayout(self)
        self.image_view = QScrollArea()
        self.image_view.setWidgetResizable(True)
        self.image_view.setAlignment(Qt.AlignCenter)
        self.image_label = QLabel(alignment=Qt.AlignCenter)
        self.image_view.setWidget(self.image_label)
        layout.addWidget(self.image_view, 1)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Zoom"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(25, 200)
        self.zoom_slider.setValue(100)
        self.zoom_slider.valueChanged.connect(self._render_image)
        controls.addWidget(self.zoom_slider, 1)
        self.zoom_label = QLabel("100%")
        controls.addWidget(self.zoom_label)
        external = QPushButton("Abrir con aplicación predeterminada")
        external.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.image_path)))
        )
        controls.addWidget(external)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        controls.addWidget(close)
        layout.addLayout(controls)
        self._render_image()

    def _render_image(self):
        self.zoom_label.setText(f"{self.zoom_slider.value()}%")
        if self.original_pixmap.isNull():
            return
        viewport = self.image_view.viewport().size()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        fit_scale = min(
            viewport.width() / self.original_pixmap.width(),
            viewport.height() / self.original_pixmap.height(),
            1.0,
        )
        scale = fit_scale * self.zoom_slider.value() / 100
        size = self.original_pixmap.size() * scale
        self.image_label.resize(size)
        self.image_label.setPixmap(
            self.original_pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render_image()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        app = QApplication.instance()
        if app:
            app.setStyle("Fusion")
            palette = app.palette()
            for role, color in (
                (QPalette.Window, "#080f20"),
                (QPalette.WindowText, "#eaf0ff"),
                (QPalette.Base, "#0b1529"),
                (QPalette.AlternateBase, "#0e1b31"),
                (QPalette.Text, "#eaf0ff"),
                (QPalette.Button, "#142442"),
                (QPalette.ButtonText, "#eaf0ff"),
                (QPalette.Highlight, "#304b7d"),
                (QPalette.HighlightedText, "#ffffff"),
            ):
                palette.setColor(role, QColor(color))
            app.setPalette(palette)
        self.setWindowTitle("SiloEdit Restore")
        icon_path = Path(__file__).resolve().parents[2] / "assets" / "icons" / "SiloEditRestore.ico"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1420, 900)
        self.setMinimumSize(1020, 680)
        self.setStyleSheet(APP_STYLE)
        self.history = History(app_data_dir() / "history.sqlite3")
        self.rows, self.worker = [], None
        self.source_dir, self.destination_dir = "", ""
        self._build()

    def _build(self):
        root = QWidget(objectName="root")
        outer = QHBoxLayout(root); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        side = QWidget(objectName="sidebar"); side.setFixedWidth(210)
        nav = QVBoxLayout(side); nav.setContentsMargins(16, 20, 16, 18); nav.setSpacing(8)
        brand = QLabel("SiloEdit Restore"); brand.setObjectName("brand"); nav.addWidget(brand)
        version = QLabel("      recuperación segura"); version.setObjectName("muted"); nav.addWidget(version); nav.addSpacing(24)
        self.stack = QStackedWidget()
        labels = ["⌂   Inicio", "⌕   Escanear", "▧   Resultados", "◷   Historial", "⚙   Configuración", "ⓘ   Acerca de"]
        self.nav_buttons = []
        for i, label in enumerate(labels):
            button = QPushButton(label, objectName="nav"); button.setCheckable(True); button.clicked.connect(lambda checked=False, ix=i: self.navigate(ix))
            nav.addWidget(button); self.nav_buttons.append(button)
        nav.addItem(QSpacerItem(0, 30, QSizePolicy.Minimum, QSizePolicy.Expanding))
        slogan = QLabel("Recuperá lo que\npensabas perdido"); slogan.setObjectName("muted"); nav.addWidget(slogan)
        nav.addWidget(QLabel("🐍  Hecho con Python"))
        outer.addWidget(side); outer.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.pages = [self._home(), self._scan_page(), self._results_page(), self._history_page(), self._settings_page(), self._about_page()]
        for page in self.pages: self.stack.addWidget(page)
        self.navigate(0)

    def card(self, title, detail=""):
        frame = QFrame(objectName="card"); layout = QVBoxLayout(frame); layout.setContentsMargins(18, 16, 18, 16)
        heading = QLabel(title); heading.setStyleSheet("font-size: 15px; font-weight:600")
        layout.addWidget(heading)
        body = QLabel(detail); body.setObjectName("muted"); body.setWordWrap(True); layout.addWidget(body)
        return frame

    def page(self, title, subtitle=""):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(28, 26, 28, 24); lay.setSpacing(18)
        h = QLabel(title); h.setStyleSheet("font-size:26px;font-weight:700;color:#f1f4ff"); lay.addWidget(h)
        if subtitle:
            sub = QLabel(subtitle); sub.setObjectName("muted"); lay.addWidget(sub)
        return w, lay

    def _home(self):
        w, lay = self.page("SiloEdit Restore", "Analizá tus imágenes y generá resultados de recuperación conservadores.")
        paths = QHBoxLayout()
        self.home_source = QLabel("Aún no seleccionada")
        self.home_dest = QLabel("Aún no seleccionada")
        source_card = self.card("Carpeta de origen", "Imágenes que se van a analizar.")
        source_layout = source_card.layout()
        self.home_source.setObjectName("muted")
        self.home_source.setWordWrap(True)
        source_layout.addWidget(self.home_source)
        self.home_choose_source = QPushButton("Elegir carpeta…")
        self.home_choose_source.clicked.connect(lambda: self.pick_folder(self.source_edit))
        source_layout.addWidget(self.home_choose_source)
        destination_card = self.card(
            "Carpeta de destino",
            "Portadas, composiciones finales y recuperaciones parciales.",
        )
        destination_layout = destination_card.layout()
        self.home_dest.setObjectName("muted")
        self.home_dest.setWordWrap(True)
        destination_layout.addWidget(self.home_dest)
        self.home_choose_destination = QPushButton("Elegir carpeta…")
        self.home_choose_destination.clicked.connect(lambda: self.pick_folder(self.dest_edit))
        destination_layout.addWidget(self.home_choose_destination)
        paths.addWidget(source_card)
        paths.addWidget(destination_card)
        lay.addLayout(paths)
        statrow = QHBoxLayout(); self.stat_labels = {}
        for key, label in (("total", "Archivos analizados"), ("valid", "Válidos"), ("low_quality", "Portadas generadas"), ("partial", "Recuperación parcial"), ("failed", "No recuperables / errores")):
            box = self.card(label, "0")
            self.stat_labels[key] = box.findChildren(QLabel)[1]
            statrow.addWidget(box)
        lay.addLayout(statrow)
        self.home_status = QLabel("Elegí las carpetas de origen y destino para empezar.")
        self.home_status.setObjectName("muted")
        lay.addWidget(self.home_status)
        self.home_progress = QProgressBar()
        self.home_progress.setRange(0, 100)
        self.home_progress.setValue(0)
        lay.addWidget(self.home_progress)
        self.home_progress_detail = QLabel("0 de 0 archivos")
        self.home_progress_detail.setObjectName("muted")
        lay.addWidget(self.home_progress_detail)
        go = QPushButton("Iniciar escaneo", objectName="primary")
        go.clicked.connect(self.start_scan)
        self.home_start_btn = go
        lay.addWidget(go, alignment=Qt.AlignLeft)
        lay.addWidget(QLabel("Archivos analizados en este escaneo"))
        self.home_live_table = self.make_table(["Archivo", "Estado", "Método", "Mensaje"])
        self.home_live_table.setMaximumHeight(250)
        lay.addWidget(self.home_live_table)
        lay.addStretch(1)
        return w

    def _scan_page(self):
        w, lay = self.page("Escanear imágenes", "Seleccioná origen y destino. Solo los resultados recuperados se guardan en destino; los originales se mantienen intactos.")
        self.source_edit, self.dest_edit = QLineEdit(), QLineEdit()
        self.source_edit.setPlaceholderText("Carpeta con imágenes a analizar"); self.dest_edit.setPlaceholderText("Carpeta para imágenes recuperadas")
        self.source_edit.textChanged.connect(
            lambda value: self.home_source.setText(value or "Aún no seleccionada")
        )
        self.dest_edit.textChanged.connect(
            lambda value: self.home_dest.setText(value or "Aún no seleccionada")
        )
        row = QHBoxLayout(); b1 = QPushButton("Elegir origen…"); b1.clicked.connect(lambda: self.pick_folder(self.source_edit)); row.addWidget(self.source_edit, 1); row.addWidget(b1); lay.addLayout(row)
        row = QHBoxLayout(); b2 = QPushButton("Elegir destino…"); b2.clicked.connect(lambda: self.pick_folder(self.dest_edit)); row.addWidget(self.dest_edit, 1); row.addWidget(b2); lay.addLayout(row)
        opts = QHBoxLayout(); self.recursive = QCheckBox("Incluir subcarpetas"); self.recursive.setChecked(True); opts.addWidget(self.recursive)
        opts.addWidget(QLabel("Formatos:")); self.formats = QLineEdit("JPG, JPEG, PNG, WEBP, BMP, TIFF, GIF"); self.formats.setToolTip("Separá las extensiones con comas")
        opts.addWidget(self.formats, 1); lay.addLayout(opts)
        self.scan_progress = QProgressBar(); lay.addWidget(self.scan_progress)
        self.scan_status = QLabel("Los archivos se revisan uno por uno."); self.scan_status.setObjectName("muted"); lay.addWidget(self.scan_status)
        buttons = QHBoxLayout(); self.start_btn = QPushButton("Iniciar análisis", objectName="primary"); self.start_btn.clicked.connect(self.start_scan); buttons.addWidget(self.start_btn)
        self.pause_btn = QPushButton("Pausar"); self.pause_btn.clicked.connect(self.toggle_pause); self.pause_btn.setEnabled(False); buttons.addWidget(self.pause_btn)
        self.cancel_btn = QPushButton("Cancelar"); self.cancel_btn.clicked.connect(self.cancel_scan); self.cancel_btn.setEnabled(False); buttons.addWidget(self.cancel_btn); buttons.addStretch(); lay.addLayout(buttons)
        self.scan_table = self.make_table(["Archivo", "Estado", "Método", "Mensaje"]); lay.addWidget(self.scan_table, 1)
        return w

    def _results_page(self):
        w, lay = self.page(
            "Resultados",
            "El original queda intacto; revisá la portada HAT y la composición final.",
        )
        controls = QHBoxLayout(); self.filter_state = QComboBox(); self.filter_state.addItems(["Todos", "Válido", "Recuperada", "Portada generada", "Parcial", "No recuperable", "Error"]); self.filter_state.currentIndexChanged.connect(self.refresh_results)
        self.filter_text = QLineEdit(); self.filter_text.setPlaceholderText("Filtrar por nombre o formato…"); self.filter_text.textChanged.connect(self.refresh_results)
        controls.addWidget(self.filter_state); controls.addWidget(self.filter_text, 1)
        export = QPushButton("Exportar CSV"); export.clicked.connect(self.export_report); controls.addWidget(export)
        openout = QPushButton("Abrir carpeta destino"); openout.clicked.connect(self.open_destination); controls.addWidget(openout); lay.addLayout(controls)
        self.results_table = self.make_table([
            "Archivo", "Formato", "Estado", "Método", "Detalle", "Resultado para revisar",
            "Final _final", "Portada _portada NOMOS HAT ×4"
        ]); lay.addWidget(self.results_table, 1)
        results_header = self.results_table.horizontalHeader()
        results_header.setSectionResizeMode(QHeaderView.Interactive)
        for column, width in ((0, 190), (1, 75), (2, 135), (3, 155), (5, 220), (6, 190), (7, 220)):
            results_header.resizeSection(column, width)
        results_header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.results_table.cellDoubleClicked.connect(self.open_result)
        self.results_table.cellClicked.connect(self.preview_result)
        previews = QHBoxLayout()
        self.before_preview = QLabel("Seleccioná un resultado"); self.after_preview = QLabel("La salida se mostrará acá")
        for label in (self.before_preview, self.after_preview):
            label.setAlignment(Qt.AlignCenter); label.setMinimumHeight(170); label.setStyleSheet("background:#0b1529;border:1px solid #263d68;border-radius:10px;color:#93a9d2")
            previews.addWidget(label, 1)
        lay.addLayout(previews)
        self.preview_info = QLabel("Vista previa local · doble clic en una fila para abrir el visor integrado"); self.preview_info.setObjectName("muted"); lay.addWidget(self.preview_info)
        return w

    def _history_page(self):
        w, lay = self.page("Historial", "Ejecuciones guardadas localmente en este equipo.")
        self.history_table = self.make_table(["Fecha", "Origen", "Total", "Válidos", "Parciales", "Fallidos", "Informe"]); lay.addWidget(self.history_table, 1)
        refresh = QPushButton("Actualizar historial"); refresh.clicked.connect(self.refresh_history); lay.addWidget(refresh, alignment=Qt.AlignLeft)
        return w

    def _settings_page(self):
        w, lay = self.page("Configuración", "Preferencias para futuros análisis.")
        note = QLabel(
            "Los archivos originales no se modifican ni se envían a servicios externos. "
            "NOMOS HAT-L y CodeFormer se ejecutan localmente cuando hay una miniatura EXIF utilizable. "
            "Las imágenes generadas por IA son estimaciones y no recuperan información original perdida. "
            "Las portadas se guardan en _portada y las composiciones en _final."
        ); note.setObjectName("muted"); note.setWordWrap(True); lay.addWidget(note); lay.addStretch()
        return w

    def _about_page(self):
        w, lay = self.page("Acerca de SiloEdit Restore", "Diagnóstico local y recuperación conservadora de imágenes.")
        lay.addWidget(self.card("SiloEdit Restore", "Versión 1.0.0 · Python y PySide6\nLos originales nunca se modifican. Las imágenes válidas se informan sin copiarlas; las portadas, composiciones finales y salidas parciales se guardan por separado.")); lay.addStretch()
        return w

    def make_table(self, headers):
        table = QTableWidget(0, len(headers)); table.setHorizontalHeaderLabels(headers); table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows); table.setEditTriggers(QAbstractItemView.NoEditTriggers); table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); return table

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        for i, b in enumerate(self.nav_buttons): b.setChecked(i == index)
        if index == 2: self.refresh_results()
        if index == 3: self.refresh_history()

    def pick_folder(self, edit):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Seleccionar carpeta",
            edit.text().strip() or str(Path.home()),
        )
        if folder: edit.setText(folder)

    def start_scan(self):
        source, destination = Path(self.source_edit.text()).expanduser(), Path(self.dest_edit.text()).expanduser()
        if not source.is_dir():
            QMessageBox.warning(self, "Origen inválido", "Seleccioná una carpeta de origen existente."); return
        if not self.dest_edit.text().strip() or source.resolve() == destination.resolve():
            QMessageBox.warning(self, "Destino inválido", "Elegí una carpeta de destino distinta del origen."); return
        try:
            destination.resolve().relative_to(source.resolve())
            QMessageBox.warning(self, "Destino dentro del origen", "Elegí un destino fuera del árbol de origen para evitar volver a analizar las copias."); return
        except ValueError:
            pass
        try: destination.mkdir(parents=True, exist_ok=True)
        except OSError as exc: QMessageBox.warning(self, "No se pudo crear el destino", str(exc)); return
        extensions = {"." + x.strip().lower().lstrip(".") for x in self.formats.text().split(",") if x.strip()}
        self.rows = []; self.scan_table.setRowCount(0); self.scan_progress.setValue(0)
        self.source_dir, self.destination_dir = str(source), str(destination)
        self.home_source.setText(self.source_dir)
        self.home_dest.setText(self.destination_dir)
        self.worker = ScanWorker(source, destination, self.recursive.isChecked(), extensions)
        self.worker.progress.connect(self.on_progress); self.worker.completed.connect(self.on_completed); self.worker.failed.connect(self.on_failed)
        self.start_btn.setEnabled(False); self.pause_btn.setEnabled(True); self.cancel_btn.setEnabled(True)
        self.home_start_btn.setEnabled(False)
        self.home_live_table.setRowCount(0)
        self.home_progress.setValue(0)
        self.home_progress_detail.setText("0 de 0 archivos")
        self.scan_status.setText(
            "Enumerando y procesando imágenes… Los JPEG dañados con miniatura EXIF pueden usar NOMOS HAT, "
            "CodeFormer y la composición final local."
        )
        self.home_status.setText("Escaneo en curso…")
        self.worker.start()

    def on_progress(self, done, total, row):
        self.rows.append(row); self.scan_progress.setValue(int(done * 100 / total) if total else 0)
        self.scan_status.setText(f"Procesados {done} de {total} archivos")
        self.home_progress.setValue(self.scan_progress.value())
        self.home_progress_detail.setText(f"{done} de {total} archivos procesados")
        self.home_status.setText(f"Analizando: {row['relative_path']}")
        self.update_stats()
        detail = row["message"]
        for field in (
            "thumbnail_message", "hat_message",
            "codeformer_message", "final_message",
        ):
            if row.get(field):
                detail = f"{detail} · {row[field]}"
        values = [row["relative_path"], self.status_label(row["status"]), row["method"], detail]
        self.add_row(self.scan_table, values)
        self.home_live_table.insertRow(0)
        for column, value in enumerate(values):
            self.home_live_table.setItem(0, column, QTableWidgetItem(str(value)))
        while self.home_live_table.rowCount() > 20:
            self.home_live_table.removeRow(self.home_live_table.rowCount() - 1)

    def toggle_pause(self):
        if not self.worker: return
        if self.worker._paused:
            self.worker.resume(); self.pause_btn.setText("Pausar"); self.scan_status.setText("Análisis reanudado.")
        else:
            self.worker.pause(); self.pause_btn.setText("Reanudar"); self.scan_status.setText("Análisis pausado después del archivo actual.")

    def cancel_scan(self):
        if self.worker: self.worker.cancel(); self.scan_status.setText("Cancelando después del archivo actual…")

    def on_completed(self, rows, source, destination):
        self.rows = rows; self.start_btn.setEnabled(True); self.pause_btn.setEnabled(False); self.cancel_btn.setEnabled(False); self.pause_btn.setText("Pausar")
        self.home_start_btn.setEnabled(True)
        report = ""
        if rows:
            report_path = Path(destination) / f"siledit_restore_{datetime.now():%Y%m%d_%H%M%S}.csv"
            export_csv(rows, report_path); report = str(report_path)
            self.history.add(datetime.now().isoformat(timespec="seconds"), source, destination, rows, report)
        self.home_source.setText(source); self.home_dest.setText(destination)
        self.home_status.setText(f"Análisis finalizado: {len(rows)} archivos procesados. Informe: {report or 'sin archivos'}")
        self.home_progress_detail.setText(f"{len(rows)} de {len(rows)} archivos procesados")
        self.update_stats(); self.refresh_results(); self.refresh_history()
        self.scan_status.setText(f"Finalizado. {len(rows)} archivos procesados." if not self.worker._cancelled else f"Cancelado. {len(rows)} archivos alcanzaron a procesarse.")

    def on_failed(self, message):
        self.start_btn.setEnabled(True); self.pause_btn.setEnabled(False); self.cancel_btn.setEnabled(False)
        self.home_start_btn.setEnabled(True)
        self.home_status.setText(f"El análisis encontró un error: {message}")
        self.scan_status.setText("El análisis encontró un error."); QMessageBox.critical(self, "Error de análisis", message)

    def status_label(self, status):
        return {"valid":"Válido", "recovered":"Recuperada", "low_quality":"Portada generada", "partial":"Parcial", "unrecoverable":"No recuperable", "error":"Error"}.get(status, status)

    def update_stats(self):
        counts = {s: sum(r["status"] == s for r in self.rows) for s in ("valid", "recovered", "low_quality", "partial", "unrecoverable", "error")}
        vals = {"total":len(self.rows), "valid":counts["valid"] + counts["recovered"], "low_quality":counts["low_quality"], "partial":counts["partial"], "failed":counts["unrecoverable"] + counts["error"]}
        for key, value in vals.items(): self.stat_labels[key].setText(str(value))

    def refresh_results(self):
        if not hasattr(self, "results_table"): return
        state = self.filter_state.currentText() if hasattr(self, "filter_state") else "Todos"
        query = self.filter_text.text().lower() if hasattr(self, "filter_text") else ""
        mapping = {"Válido":"valid", "Recuperada":"recovered", "Portada generada":"low_quality", "Parcial":"partial", "No recuperable":"unrecoverable", "Error":"error"}
        rows = [r for r in self.rows if (state == "Todos" or r["status"] == mapping.get(state)) and query in (r["name"] + r["format"]).lower()]
        self.results_table.setRowCount(0)
        for r in rows:
            detail = r["message"]
            if r.get("thumbnail_message"):
                detail = f"{detail} · {r['thumbnail_message']}"
            if r.get("hat_message"):
                detail = f"{detail} · {r['hat_message']}"
            if r.get("codeformer_message"):
                detail = f"{detail} · {r['codeformer_message']}"
            if r.get("final_message"):
                detail = f"{detail} · {r['final_message']}"
            primary_output = preferred_result_path(r)
            self.add_row(self.results_table, [r["relative_path"], r["format"], self.status_label(r["status"]), r["method"], detail,
                                               primary_output or "—",
                                               r.get("final_output") or "—",
                                               r.get("hat_output") or "—"])
        self.before_preview.setText("Seleccioná un resultado")
        self.after_preview.setText("La salida se mostrará acá")
        self.before_preview.setPixmap(QPixmap()); self.after_preview.setPixmap(QPixmap())

    def preview_result(self, row, column):
        item = self.results_table.item(row, 0)
        if not item: return
        record = next((r for r in self.rows if r["relative_path"] == item.text()), None)
        if not record: return
        self._set_preview(self.before_preview, record["source"])
        if column == 6:
            selected_output = record.get("final_output")
            info = record.get("final_message") or record["message"]
        elif column == 7:
            selected_output = record.get("hat_output")
            info = record.get("hat_message") or record["message"]
        else:
            selected_output = preferred_result_path(record)
            info = (
                record.get("final_message")
                if record.get("final_output")
                else record.get("hat_message")
                if record.get("hat_output")
                else record["message"]
            )
        self._set_preview(self.after_preview, selected_output or record["output"])
        self.preview_info.setText(f"{record['name']} · {self.status_label(record['status'])} · {info}")

    def _set_preview(self, label, filename):
        pixmap = QPixmap(filename) if filename else QPixmap()
        if pixmap.isNull():
            label.setPixmap(QPixmap()); label.setText("Vista previa no disponible")
        else:
            label.setText("")
            label.setPixmap(pixmap.scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def refresh_history(self):
        if not hasattr(self, "history_table"): return
        entries = self.history.list_runs(); self.history_table.setRowCount(0)
        for ident, date, source, dest, total, valid, partial, failed, report in entries:
            self.add_row(self.history_table, [date, source, str(total), str(valid), str(partial), str(failed), report])

    def add_row(self, table, values):
        row = table.rowCount(); table.insertRow(row)
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            table.setItem(row, col, item)

    def export_report(self):
        if not self.rows: QMessageBox.information(self, "Sin resultados", "Primero ejecutá un análisis."); return
        path, _ = QFileDialog.getSaveFileName(self, "Exportar informe", "siledit_restore.csv", "CSV (*.csv)")
        if path: export_csv(self.rows, Path(path))

    def open_result(self, row, column):
        if row < 0:
            return
        output_column = column if 5 <= column <= 7 else 5
        item = self.results_table.item(row, output_column)
        candidate = Path(item.text()) if item and item.text() != "—" else None
        if candidate and candidate.is_file():
            image = QPixmap(str(candidate))
            if image.isNull():
                QMessageBox.warning(self, "Vista previa", "No se pudo cargar la imagen seleccionada.")
                return
            ImageViewerDialog(candidate, self).exec()

    def open_destination(self):
        if self.destination_dir and Path(self.destination_dir).exists(): QDesktopServices.openUrl(QUrl.fromLocalFile(self.destination_dir))
        else: QMessageBox.information(self, "Sin destino", "Todavía no hay una carpeta de destino seleccionada.")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()
        event.accept()
