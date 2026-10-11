from pathlib import Path
from PySide6.QtCore import QThread, Signal
from src.core.scanner import iter_images
from src.core.recovery import process_file
from src.core.ai_runtime import (
    close_ai_inference_session,
    start_ai_inference_session,
)


class ScanWorker(QThread):
    progress = Signal(int, int, dict)
    completed = Signal(list, str, str)
    failed = Signal(str)

    def __init__(self, source: Path, destination: Path, recursive: bool, formats: set[str]):
        super().__init__()
        self.source, self.destination = source, destination
        self.recursive, self.formats = recursive, formats
        self._paused = False
        self._cancelled = False

    def pause(self): self._paused = True
    def resume(self): self._paused = False
    def cancel(self): self._cancelled = True

    def run(self):
        start_ai_inference_session()
        rows = []
        failure = None
        try:
            files = list(iter_images(self.source, self.recursive, self.formats))
            for i, path in enumerate(files, 1):
                while self._paused and not self._cancelled:
                    self.msleep(100)
                if self._cancelled: break
                try:
                    row = process_file(path, self.source, self.destination)
                except Exception as exc:
                    row = {"name": path.name, "relative_path": str(path.relative_to(self.source)), "source": str(path),
                           "output": "", "format": path.suffix.upper(), "size": path.stat().st_size,
                           "error": str(exc), "method": "", "status": "error", "message": str(exc),
                           "thumbnail_message": "",
                           "hat_output": "", "hat_message": "",
                           "codeformer_message": "",
                           "final_output": "", "final_message": "", "jpeg_analysis": "",
                           "processed_at": ""}
                rows.append(row)
                self.progress.emit(i, len(files), row)
        except Exception as exc:
            failure = str(exc)
        finally:
            close_ai_inference_session()
        if failure is not None:
            self.failed.emit(failure)
        else:
            self.completed.emit(rows, str(self.source), str(self.destination))
