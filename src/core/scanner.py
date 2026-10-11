from pathlib import Path
from .recovery import SUPPORTED


def iter_images(folder: Path, recursive: bool = True, formats: set[str] | None = None):
    allowed = formats or SUPPORTED
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    for item in iterator:
        if item.is_file() and item.suffix.lower() in allowed:
            yield item
