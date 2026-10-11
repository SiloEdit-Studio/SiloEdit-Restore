"""Experimental JPEG masks and thumbnail-guided image compositions."""
from __future__ import annotations

from math import ceil
from pathlib import Path

from PIL import Image

from .validators import _decode_truncated_rgb


def alinear_miniatura_con_imagen(base: Image.Image, thumbnail: Image.Image) -> tuple[Image.Image, dict]:
    """Apply size/orientation-aware scaling without distorting aspect ratio."""
    base_rgb = base.convert("RGB")
    thumbnail_rgb = thumbnail.convert("RGB")
    base_ratio = base_rgb.width / base_rgb.height
    thumbnail_ratio = thumbnail_rgb.width / thumbnail_rgb.height
    ratio_error = abs(base_ratio - thumbnail_ratio) / base_ratio
    if ratio_error > 0.02:
        raise ValueError(
            f"La proporción de la miniatura ({thumbnail_ratio:.4f}) no coincide con la imagen "
            f"orientada ({base_ratio:.4f}); se evita una alineación especulativa."
        )
    aligned = thumbnail_rgb.resize(base_rgb.size, Image.Resampling.LANCZOS)
    return aligned, {
        "method": "EXIF orientation + aspect-ratio resize",
        "base_size": list(base_rgb.size),
        "thumbnail_size": list(thumbnail_rgb.size),
        "relative_aspect_error": ratio_error,
        "homography_applied": False,
        "warning": "La ampliación interpola la miniatura y no recupera detalle de resolución completa.",
    }


def crear_mascara_dano(
    base: Image.Image,
    guide: Image.Image,
    mcu_size: tuple[int, int] = (16, 16),
    binary_suspicious: bool = False,
) -> tuple[Image.Image, Image.Image, dict]:
    """Build a conservative MCU-grid mask and confidence map using pixel evidence."""
    import numpy as np

    try:
        import cv2
    except ImportError:
        cv2 = None

    source = base.convert("RGB")
    reference, alignment = alinear_miniatura_con_imagen(source, guide)
    width, height = source.size
    mcu_width, mcu_height = max(1, mcu_size[0]), max(1, mcu_size[1])
    columns, rows = ceil(width / mcu_width), ceil(height / mcu_height)

    if cv2 is not None:
        source_pixels = np.asarray(source)
        reference_pixels = np.asarray(reference)
        grid_size = (columns, rows)
        source_mean = cv2.resize(source_pixels, grid_size, interpolation=cv2.INTER_AREA).astype(np.float32)
        reference_mean = cv2.resize(reference_pixels, grid_size, interpolation=cv2.INTER_AREA).astype(np.float32)
        source_gray = cv2.cvtColor(source_pixels, cv2.COLOR_RGB2GRAY).astype(np.float32)
        source_gray_sq = source_gray * source_gray
        mean_gray = cv2.resize(source_gray, grid_size, interpolation=cv2.INTER_AREA)
        mean_gray_sq = cv2.resize(source_gray_sq, grid_size, interpolation=cv2.INTER_AREA)
        source_std = np.sqrt(np.maximum(mean_gray_sq - mean_gray * mean_gray, 0.0))
        reference_gray = cv2.cvtColor(reference_pixels, cv2.COLOR_RGB2GRAY).astype(np.float32)
        ref_gray_mean = cv2.resize(reference_gray, grid_size, interpolation=cv2.INTER_AREA)
        ref_gray_sq = cv2.resize(reference_gray * reference_gray, grid_size, interpolation=cv2.INTER_AREA)
        reference_std = np.sqrt(np.maximum(ref_gray_sq - ref_gray_mean * ref_gray_mean, 0.0))
    else:
        source_pixels = np.asarray(source)
        reference_pixels = np.asarray(reference)
        source_mean = np.asarray(source.resize((columns, rows), Image.Resampling.BOX)).astype(np.float32)
        reference_mean = np.asarray(reference.resize((columns, rows), Image.Resampling.BOX)).astype(np.float32)
        source_std = np.zeros((rows, columns), dtype=np.float32)
        reference_std = np.zeros((rows, columns), dtype=np.float32)

    delta = np.abs(source_mean - reference_mean).mean(axis=2)
    source_spread = source_mean.max(axis=2) - source_mean.min(axis=2)
    neutral_decoder_gray = (source_spread <= 7) & (source_mean[:, :, 0] >= 116) & (source_mean[:, :, 0] <= 140)
    flat_source = source_std <= 4.0
    reference_has_structure = reference_std >= 7.0
    green_shift = (
        (source_mean[:, :, 1] - np.maximum(source_mean[:, :, 0], source_mean[:, :, 2]) >= 35)
        & (source_mean[:, :, 1] >= 90)
        & (delta >= 70)
        & (source_std <= 12.0)
    )
    possible = flat_source & (delta >= 18)
    gray_damage = (
        neutral_decoder_gray & flat_source & (delta >= 10)
        if binary_suspicious
        else neutral_decoder_gray & flat_source & reference_has_structure & (delta >= 10)
    )
    damaged = (
        gray_damage
        | (flat_source & reference_has_structure & (delta >= 38))
        | green_shift
    )

    if cv2 is not None:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(damaged.astype(np.uint8), connectivity=8)
        min_cells = max(2, int(rows * columns * 0.0001))
        keep = np.flatnonzero(stats[1:, cv2.CC_STAT_AREA] >= min_cells) + 1
        connected_damage = np.isin(labels, keep)
        isolated_damage = damaged & (green_shift | neutral_decoder_gray)
        damaged = connected_damage | isolated_damage
        count = len(keep)
    else:
        min_cells = 2
        count = int(damaged.sum())
        if count < min_cells:
            damaged[:] = False
            count = 0

    confidence_grid = np.full((rows, columns), 255, dtype=np.uint8)
    confidence_grid[possible] = 160
    confidence_grid[damaged] = 32
    confidence = Image.fromarray(confidence_grid, mode="L").resize(
        (width, height), Image.Resampling.NEAREST
    )
    mask = Image.fromarray((damaged.astype(np.uint8) * 255), mode="L").resize(
        (width, height), Image.Resampling.NEAREST
    )
    metadata = {
        "mcu_size": [mcu_width, mcu_height],
        "grid": [columns, rows],
        "suspect_mcus": int(damaged.sum()),
        "possible_mcus": int((possible & ~damaged).sum()),
        "pixel_count": width * height,
        "damaged_percent": float(min(100.0, damaged.sum() * mcu_width * mcu_height * 100 / (width * height))),
        "confidence_levels": {
            "255": "visualmente consistente con la miniatura; no prueba integridad DCT",
            "160": "posiblemente dañado",
            "32": "probablemente perdido o gravemente alterado; zona propuesta para inferencia",
        },
        "detector": "MCU-grid flatness + color difference against oriented EXIF thumbnail",
        "binary_corruption_evidence": binary_suspicious,
        "limitations": [
            "La miniatura y el JPEG pueden tener distinta compresión, exposición o procesado.",
            "Una superficie auténtica uniforme que difiera de la miniatura puede marcarse por error.",
            "La máscara indica sospecha visual, no errores de coeficientes ni límites del flujo Huffman.",
        ],
        "alignment": alignment,
        "connected_regions": count,
        "min_region_mcus": min_cells,
    }
    return mask, confidence, metadata


def compensar_color_ycbcr(
    base: Image.Image,
    guide: Image.Image,
    mask: Image.Image,
) -> Image.Image:
    """Transfer chroma from the authentic thumbnail while retaining base luminance."""
    import numpy as np

    source = base.convert("YCbCr")
    reference = guide.convert("YCbCr").resize(source.size, Image.Resampling.LANCZOS)
    source_pixels = np.asarray(source).copy()
    reference_pixels = np.asarray(reference)
    selected = np.asarray(mask.convert("L")) > 0
    source_pixels[selected, 1:] = reference_pixels[selected, 1:]
    return Image.fromarray(source_pixels, mode="YCbCr").convert("RGB")


def combinar_imagen_y_miniatura(
    base: Image.Image,
    guide: Image.Image,
    mask: Image.Image,
) -> tuple[Image.Image, dict]:
    """Compose a lossless hybrid: thumbnail RGB for lost gray, guided chroma otherwise."""
    import numpy as np

    source = base.convert("RGB")
    reference = guide.convert("RGB").resize(source.size, Image.Resampling.LANCZOS)
    selected = np.asarray(mask.convert("L")) > 0
    if not selected.any():
        return source.copy(), {
            "replaced_percent": 0.0,
            "gray_pixels_from_thumbnail": 0,
            "chroma_corrected_pixels": 0,
        }

    source_pixels = np.asarray(source)
    reference_pixels = np.asarray(reference)
    spread = source_pixels.max(axis=2) - source_pixels.min(axis=2)
    gray_lost = selected & (spread <= 7) & (source_pixels[:, :, 0] >= 116) & (source_pixels[:, :, 0] <= 140)
    chroma_selected = selected & ~gray_lost
    hybrid = source_pixels.copy()
    corrected = np.asarray(compensar_color_ycbcr(source, reference, mask))
    hybrid[chroma_selected] = corrected[chroma_selected]
    hybrid[gray_lost] = reference_pixels[gray_lost]
    result = Image.fromarray(hybrid, mode="RGB")
    return result, {
        "replaced_percent": float(selected.mean() * 100),
        "gray_pixels_from_thumbnail": int(gray_lost.sum()),
        "chroma_corrected_pixels": int(chroma_selected.sum()),
        "content_source": (
            "La miniatura aporta RGB en gris casi neutro; en otras zonas marcadas solo se transfieren "
            "Cb/Cr y se conserva Y decodificado."
        ),
    }


def crear_hibrido_y_mascaras(
    source: Path,
    thumbnail_path: Path,
    mcu_size: tuple[int, int],
    hybrid_path: Path,
    mask_path: Path,
    confidence_path: Path,
    binary_suspicious: bool = False,
) -> tuple[str, dict]:
    """Decode, align, classify, and save independent non-overwriting PNG outputs."""
    from PIL import ImageOps

    base = _decode_truncated_rgb(source)
    with Image.open(thumbnail_path) as thumbnail_file:
        thumbnail = ImageOps.exif_transpose(thumbnail_file).convert("RGB")
    guide, alignment = alinear_miniatura_con_imagen(base, thumbnail)
    mask, confidence, metadata = crear_mascara_dano(
        base, guide, mcu_size, binary_suspicious=binary_suspicious
    )
    if metadata["suspect_mcus"] == 0:
        return "unavailable", {
            **metadata,
            "message": "No se encontró evidencia suficiente para crear una máscara; se evitaron cambios especulativos.",
        }

    hybrid, composition = combinar_imagen_y_miniatura(base, guide, mask)
    outputs = [(hybrid_path, hybrid), (mask_path, mask), (confidence_path, confidence)]
    created = []
    try:
        for target, image in outputs:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                created.append(target)
                image.save(stream, format="PNG", optimize=True)
            with Image.open(target) as check:
                check.verify()
        return "experimental", {
            **metadata,
            "alignment": alignment,
            "composition": composition,
            "outputs": {
                "hybrid": str(hybrid_path),
                "mask": str(mask_path),
                "confidence": str(confidence_path),
            },
            "message": (
                f"Híbrido experimental: máscara en {metadata['damaged_percent']:.2f}% de la cuadrícula MCU; "
                "la miniatura aporta contenido RGB o crominancia donde fue marcada. No es reparación DCT."
            ),
        }
    except (OSError, ValueError) as exc:
        for target in created:
            target.unlink(missing_ok=True)
        return "unavailable", {**metadata, "message": f"No se pudieron validar las salidas experimentales: {exc}"}


def generar_informe_reparacion(
    analysis: dict,
    mask_metadata: dict | None = None,
    output_paths: dict | None = None,
) -> dict:
    """Create machine-readable evidence with explicit recovered/inferred provenance."""
    mcu = analysis.get("mcu") or {}
    return {
        "format": "SiloEdit Restore experimental JPEG report",
        "binary_analysis": analysis,
        "image_analysis": mask_metadata or {},
        "outputs": output_paths or {},
        "provenance": {
            "jpeg_pixels": "Datos que el decodificador pudo leer; no se afirma que todos sean correctos.",
            "exif_thumbnail": "Píxeles auténticos de baja resolución, reescalados/interpolados al alinear.",
            "hybrid": "Mezcla experimental de decodificación y miniatura EXIF según la máscara.",
            "ai": (
                "NOMOS 8k HAT-L y CodeFormer estiman textura y detalle facial desde la miniatura EXIF; "
                "no recupera detalle ausente ni demuestra la apariencia original."
            ),
            "experimental_reconstruction": (
                "Composición experimental del híbrido con la guía NOMOS HAT/CodeFormer, "
                "ajustada en color y mezclada en la máscara con borde suavizado."
            ),
            "mcu_dct_repair": "No realizado: no hay decoder de coeficientes DCT/Huffman ni reparación validada.",
        },
        "metrics": {
            "original_bytes": analysis.get("file_size"),
            "resolution": (
                [analysis["frame"]["width"], analysis["frame"]["height"]]
                if analysis.get("frame") else None
            ),
            "exif_orientation": analysis.get("orientation"),
            "mcu_count": mcu.get("count"),
            "suspect_mcus": (mask_metadata or {}).get("suspect_mcus"),
            "percentage_inferred": None,
            "inference_percent_note": (
                "No se cuantifica: HAT/CodeFormer estiman detalle; no realizan reparación DCT ni garantizan "
                "la apariencia original."
            ),
            "percentage_interpolated": (
                (mask_metadata or {}).get("composition", {}).get("gray_pixels_from_thumbnail", 0)
                * 100.0 / (mask_metadata or {}).get("pixel_count", 1)
            ),
            "percentage_recovered": None,
            "recovery_percent_note": "No se calcula recuperación real porque no se reconstruyeron coeficientes DCT.",
        },
    }


def extraer_miniatura_exif(source: Path, destination: Path) -> tuple[str, str]:
    """Spanish entry point reusing the validated EXIF thumbnail extractor."""
    from .validators import recover_exif_thumbnail

    return recover_exif_thumbnail(source, destination)


def crear_mascara_de_dano(*args, **kwargs):
    """Spanish alias for :func:`crear_mascara_dano`."""
    return crear_mascara_dano(*args, **kwargs)
