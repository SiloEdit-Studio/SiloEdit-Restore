"""Bounded structural inspection of JPEG markers and scan data.

This module reports byte-level evidence. It does not decode entropy-coded
coefficients or claim that suspicious markers identify recoverable MCUs.
"""
from __future__ import annotations

from math import ceil
from pathlib import Path


_STANDALONE_MARKERS = {0x01, 0xD8, 0xD9, *range(0xD0, 0xD8)}
_FRAME_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, *range(0xC5, 0xC8), *range(0xC9, 0xCD)}


def _marker_name(code: int) -> str:
    names = {
        0x01: "TEM", 0xC0: "SOF0", 0xC1: "SOF1", 0xC2: "SOF2",
        0xC4: "DHT", 0xDB: "DQT", 0xDD: "DRI", 0xDA: "SOS",
        0xD8: "SOI", 0xD9: "EOI", 0xFE: "COM",
    }
    if 0xE0 <= code <= 0xEF:
        return f"APP{code - 0xE0}"
    if 0xD0 <= code <= 0xD7:
        return f"RST{code - 0xD0}"
    return names.get(code, f"FF{code:02X}")


def _parse_jpeg_bytes(data: bytes, file_size: int | None = None) -> dict:
    result = {
        "file_size": file_size if file_size is not None else len(data),
        "has_soi": data[:2] == b"\xff\xd8",
        "has_eoi": False,
        "frame": None,
        "orientation": None,
        "mcu": None,
        "markers": [],
        "scans": [],
        "entropy_anomalies": [],
        "structural_errors": [],
        "trailing_bytes": 0,
    }
    if not result["has_soi"]:
        result["structural_errors"].append({"offset": 0, "reason": "Falta el marcador SOI FFD8."})
        return result

    try:
        import piexif

        exif = piexif.load(data)
        result["orientation"] = exif.get("0th", {}).get(piexif.ImageIFD.Orientation, 1)
        thumbnail = exif.get("thumbnail")
        if thumbnail:
            result["thumbnail_bytes"] = len(thumbnail)
    except (ImportError, ValueError, KeyError, TypeError):
        result["orientation"] = None

    position = 2
    active_scan: dict | None = None
    restart_markers = 0
    restart_interval = 0
    eoi_end = None

    while position < len(data):
        if active_scan is not None:
            marker_start = data.find(b"\xff", position)
            if marker_start < 0:
                active_scan["end"] = len(data)
                active_scan["entropy_bytes"] = len(data) - active_scan["start"]
                result["structural_errors"].append({
                    "offset": len(data),
                    "reason": "El escaneo comprimido termina sin un marcador JPEG.",
                })
                break

            code_position = marker_start + 1
            while code_position < len(data) and data[code_position] == 0xFF:
                code_position += 1
            if code_position >= len(data):
                active_scan["end"] = len(data)
                active_scan["entropy_bytes"] = len(data) - active_scan["start"]
                result["structural_errors"].append({
                    "offset": marker_start,
                    "reason": "Prefijo FF incompleto al final del escaneo.",
                })
                break

            code = data[code_position]
            if code == 0x00 or 0xD0 <= code <= 0xD7:
                if code != 0x00:
                    restart_markers += 1
                    result["markers"].append({
                        "marker": _marker_name(code), "offset": marker_start, "in_scan": True,
                    })
                position = code_position + 1
                continue

            if code in {0xD9, 0xDA}:
                active_scan["end"] = marker_start
                active_scan["entropy_bytes"] = marker_start - active_scan["start"]
                active_scan = None
                position = marker_start
                continue

            if result["frame"] and result["frame"]["marker"] == "SOF0":
                result["entropy_anomalies"].append({
                    "offset": marker_start,
                    "marker": _marker_name(code),
                    "reason": "Marcador no esperado dentro de un escaneo JPEG baseline.",
                })
            result["markers"].append({
                "marker": _marker_name(code), "offset": marker_start, "in_scan": True,
            })
            if code in _STANDALONE_MARKERS:
                position = code_position + 1
                continue

            segment_position = code_position + 1
            if segment_position + 2 > len(data):
                result["structural_errors"].append({
                    "offset": marker_start,
                    "reason": f"Marcador {_marker_name(code)} dentro del escaneo sin longitud legible.",
                })
                position = code_position + 1
                continue
            segment_length = int.from_bytes(data[segment_position:segment_position + 2], "big")
            segment_end = segment_position + segment_length
            if segment_length < 2 or segment_end > len(data):
                result["structural_errors"].append({
                    "offset": marker_start,
                    "reason": f"Longitud invalida en marcador {_marker_name(code)} dentro del escaneo.",
                })
                position = code_position + 1
                continue
            position = segment_end
            continue

        if data[position] != 0xFF:
            result["structural_errors"].append({
                "offset": position,
                "reason": "Se esperaba un prefijo de marcador FF fuera del escaneo.",
            })
            break

        marker_start = position
        code_position = position + 1
        while code_position < len(data) and data[code_position] == 0xFF:
            code_position += 1
        if code_position >= len(data):
            result["structural_errors"].append({
                "offset": marker_start,
                "reason": "Prefijo de marcador incompleto al final del archivo.",
            })
            break

        code = data[code_position]
        name = _marker_name(code)
        result["markers"].append({"marker": name, "offset": marker_start, "in_scan": False})
        position = code_position + 1

        if code == 0xD9:
            result["has_eoi"] = True
            eoi_end = position
            break
        if code in _STANDALONE_MARKERS:
            continue
        if position + 2 > len(data):
            result["structural_errors"].append({
                "offset": marker_start,
                "reason": f"Falta la longitud del segmento {name}.",
            })
            break

        segment_length = int.from_bytes(data[position:position + 2], "big")
        segment_end = position + segment_length
        if segment_length < 2 or segment_end > len(data):
            result["structural_errors"].append({
                "offset": marker_start,
                "reason": f"Longitud invalida o segmento truncado {name} ({segment_length}).",
            })
            break
        payload = data[position + 2:segment_end]

        if code in _FRAME_MARKERS and len(payload) >= 6:
            precision = payload[0]
            height = int.from_bytes(payload[1:3], "big")
            width = int.from_bytes(payload[3:5], "big")
            component_count = payload[5]
            expected = 6 + component_count * 3
            if len(payload) < expected:
                result["structural_errors"].append({
                    "offset": marker_start,
                    "reason": f"Segmento {name} incompleto para sus componentes.",
                })
            else:
                components = []
                for index in range(component_count):
                    offset = 6 + index * 3
                    sampling = payload[offset + 1]
                    components.append({
                        "id": payload[offset],
                        "horizontal_sampling": sampling >> 4,
                        "vertical_sampling": sampling & 0x0F,
                        "quantization_table": payload[offset + 2],
                    })
                result["frame"] = {
                    "marker": name,
                    "precision": precision,
                    "width": width,
                    "height": height,
                    "components": components,
                }

        if code == 0xDD and len(payload) >= 2:
            restart_interval = int.from_bytes(payload[:2], "big")
        if code == 0xDA:
            active_scan = {
                "start": segment_end,
                "end": None,
                "entropy_bytes": None,
                "header_offset": marker_start,
            }
            result["scans"].append(active_scan)
        position = segment_end

    if not result["has_eoi"] and not any(
        "without a JPEG marker" in error["reason"] for error in result["structural_errors"]
    ):
        result["structural_errors"].append({
            "offset": len(data),
            "reason": "Falta el marcador EOI FFD9.",
        })
    if eoi_end is not None:
        result["trailing_bytes"] = len(data) - eoi_end

    frame = result["frame"]
    if frame and frame["width"] > 0 and frame["height"] > 0 and frame["components"]:
        max_h = max(component["horizontal_sampling"] for component in frame["components"])
        max_v = max(component["vertical_sampling"] for component in frame["components"])
        if max_h and max_v:
            mcu_width, mcu_height = 8 * max_h, 8 * max_v
            columns = ceil(frame["width"] / mcu_width)
            rows = ceil(frame["height"] / mcu_height)
            result["mcu"] = {
                "width": mcu_width,
                "height": mcu_height,
                "columns": columns,
                "rows": rows,
                "count": columns * rows,
                "restart_interval": restart_interval,
                "restart_markers_found": restart_markers,
                "component_blocks": {
                    str(component["id"]): columns * rows
                    * component["horizontal_sampling"] * component["vertical_sampling"]
                    for component in frame["components"]
                },
            }
    return result


def analizar_jpeg_binario(source: Path) -> dict:
    """Inspect JPEG marker and scan structure without decoding image pixels."""
    source = Path(source)
    try:
        data = source.read_bytes()
        result = _parse_jpeg_bytes(data, source.stat().st_size)
        result["source"] = str(source)
        return result
    except OSError as exc:
        return {
            "source": str(source),
            "file_size": None,
            "has_soi": False,
            "has_eoi": False,
            "frame": None,
            "orientation": None,
            "mcu": None,
            "markers": [],
            "scans": [],
            "entropy_anomalies": [],
            "structural_errors": [{"offset": None, "reason": f"No se pudo leer el JPEG: {exc}"}],
            "trailing_bytes": 0,
        }


def analyze_jpeg_binary(source: Path) -> dict:
    """English alias for :func:`analizar_jpeg_binario`."""
    return analizar_jpeg_binario(source)


def analizar_mcus(analysis: dict) -> dict:
    """Return MCU geometry only; this does not decode individual DCT blocks."""
    return {
        **(analysis.get("mcu") or {}),
        "frame": analysis.get("frame"),
        "pixel_coefficients_decoded": False,
        "note": "La geometría se deriva de SOF y factores de muestreo; no se inspeccionaron coeficientes DCT.",
    }


def detect_mcu_misalignment(analysis: dict) -> dict:
    """Summarize marker evidence that may interrupt scan alignment."""
    findings = analysis.get("entropy_anomalies", [])
    return {
        "detected": bool(findings),
        "evidence": findings,
        "can_realign": False,
        "reason": (
            "Se observan marcadores sospechosos dentro del escaneo, pero este análisis no decodifica "
            "símbolos Huffman ni conoce los límites exactos de MCU."
            if findings else
            "No se detectó un marcador no esperado dentro del escaneo; esto no prueba que los coeficientes estén intactos."
        ),
    }


def detectar_desalineacion_mcu(analysis: dict) -> dict:
    """Spanish alias for :func:`detect_mcu_misalignment`."""
    return detect_mcu_misalignment(analysis)


def reparar_jpeg_mcu(source: Path, destination: Path, analysis: dict | None = None) -> tuple[str, str]:
    """Refuse to fabricate an MCU repair without coefficient-level decoding."""
    del source, destination, analysis
    return (
        "unavailable",
        "Reparación MCU/DCT no implementada: no se decodificaron coeficientes ni límites de MCU confiables; "
        "no se generó una imagen con datos inventados.",
    )
