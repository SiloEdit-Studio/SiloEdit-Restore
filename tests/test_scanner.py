from pathlib import Path
from src.core.scanner import iter_images


def test_scanner_recurses_and_filters_extensions(tmp_path: Path):
    (tmp_path / "one.jpg").write_bytes(b"x")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "two.PNG").write_bytes(b"y")
    (nested / "ignore.txt").write_text("no")
    assert {p.name for p in iter_images(tmp_path, recursive=True)} == {"one.jpg", "two.PNG"}
    assert {p.name for p in iter_images(tmp_path, recursive=False)} == {"one.jpg"}
