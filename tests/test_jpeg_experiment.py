from pathlib import Path

from PIL import Image
import numpy as np

from src.core.jpeg_analysis import analizar_jpeg_binario, detectar_desalineacion_mcu
from src.core.jpeg_experiment import (
    alinear_miniatura_con_imagen,
    combinar_imagen_y_miniatura,
    crear_mascara_dano,
)


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + (len(payload) + 2).to_bytes(2, "big") + payload


def _baseline_jpeg_with_unexpected_scan_marker() -> bytes:
    frame = (
        b"\x08" + (32).to_bytes(2, "big") + (48).to_bytes(2, "big") + b"\x03"
        + b"\x01\x22\x00" + b"\x02\x11\x01" + b"\x03\x11\x01"
    )
    scan = b"\x03\x01\x00\x02\x11\x03\x11\x00"
    return (
        b"\xff\xd8"
        + _segment(0xDB, b"\x00" + bytes(range(1, 65)))
        + _segment(0xC0, frame)
        + _segment(0xC4, b"\x00" + bytes(16) + b"\x00")
        + _segment(0xDA, scan)
        + b"\x12\x34\xff\x08\x00\x02\x56\x78\xff\xd9"
    )


def test_binary_jpeg_analysis_reports_markers_entropy_anomaly_and_mcu_geometry(tmp_path: Path):
    source = tmp_path / "scan.jpg"
    source.write_bytes(_baseline_jpeg_with_unexpected_scan_marker())

    report = analizar_jpeg_binario(source)

    assert report["has_soi"] and report["has_eoi"]
    assert report["frame"]["marker"] == "SOF0"
    assert report["frame"]["width"] == 48
    assert report["frame"]["height"] == 32
    assert report["mcu"]["width"] == 16
    assert report["mcu"]["height"] == 16
    assert report["mcu"]["count"] == 6
    assert [item["marker"] for item in report["entropy_anomalies"]] == ["FF08"]
    assert detectar_desalineacion_mcu(report)["can_realign"] is False


def test_hybrid_uses_exif_thumbnail_for_gray_mask_and_preserves_confident_pixels():
    base = Image.new("RGB", (64, 64), (24, 48, 72))
    base.paste((128, 128, 128), (16, 16, 48, 48))
    guide = Image.new("RGB", (64, 64), (24, 48, 72))
    guide.paste((220, 30, 10), (16, 16, 48, 48))

    aligned, alignment = alinear_miniatura_con_imagen(base, guide)
    mask, confidence, metadata = crear_mascara_dano(base, aligned, (16, 16), binary_suspicious=True)
    hybrid, composition = combinar_imagen_y_miniatura(base, aligned, mask)

    assert alignment["homography_applied"] is False
    assert metadata["suspect_mcus"] > 0
    assert mask.getpixel((24, 24)) == 255
    assert confidence.getpixel((24, 24)) == 32
    original_pixels = np.asarray(base)
    mask_pixels = np.asarray(mask) > 0
    hybrid_pixels = np.asarray(hybrid)
    assert np.array_equal(hybrid_pixels[~mask_pixels], original_pixels[~mask_pixels])
    assert hybrid.getpixel((24, 24)) == (220, 30, 10)
    assert composition["gray_pixels_from_thumbnail"] > 0


def test_alignment_rejects_incompatible_thumbnail_aspect_ratio():
    base = Image.new("RGB", (96, 64), "black")
    thumbnail = Image.new("RGB", (64, 96), "black")

    try:
        alinear_miniatura_con_imagen(base, thumbnail)
    except ValueError as exc:
        assert "proporción" in str(exc)
    else:
        raise AssertionError("Expected incompatible thumbnail geometry to be rejected")
