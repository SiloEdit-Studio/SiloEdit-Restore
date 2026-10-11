import csv
from pathlib import Path

FIELDS = ["name", "relative_path", "format", "size", "error", "method", "status", "message",
          "output", "thumbnail_message", "hat_output", "hat_message", "codeformer_message",
          "final_output", "final_message",
          "jpeg_analysis", "processed_at"]


def export_csv(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path
