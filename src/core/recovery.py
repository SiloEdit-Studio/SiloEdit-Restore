"""Safe, per-file image diagnosis and recovery orchestration."""
from pathlib import Path
from datetime import datetime
import json
import tempfile
from .jpeg_analysis import analizar_jpeg_binario
from .jpeg_experiment import crear_hibrido_y_mascaras
from .validators import (validate_image, recover_image, recover_exif_thumbnail,
                         create_experimental_reconstruction, upscale_image_with_hat,
                         restore_faces_with_codeformer)

SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


def unique_path(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        n += 1
    return candidate


def _process_jpeg_experimental(
    source: Path,
    rel: Path,
    destination: Path,
    analysis: dict,
) -> dict:
    """Create one compact, thumbnail-guided reconstruction pipeline."""
    result = {
        "thumbnail_message": "",
        "hat_output": "", "hat_message": "",
        "codeformer_message": "",
        "final_output": "", "final_message": "",
        "jpeg_analysis": json.dumps(analysis, ensure_ascii=False),
    }
    has_damage_evidence = bool(analysis["entropy_anomalies"] or analysis["structural_errors"])
    if not has_damage_evidence:
        result["final_message"] = (
            "No se detectaron anomalías estructurales en los marcadores JPEG. "
            "La ausencia de anomalías no garantiza que todos los coeficientes DCT sean correctos."
        )
        return result

    with tempfile.TemporaryDirectory(prefix="pixelrestore-jpeg-") as temporary_dir:
        temporary_dir = Path(temporary_dir)
        thumbnail_target = temporary_dir / "thumbnail.jpg"
        thumbnail_status, thumbnail_message = recover_exif_thumbnail(source, thumbnail_target)
        result["thumbnail_message"] = thumbnail_message
        if thumbnail_status != "low_quality":
            return result

        mcu = analysis.get("mcu") or {}
        mcu_width, mcu_height = mcu.get("width", 16), mcu.get("height", 16)
        if analysis.get("orientation") in {5, 6, 7, 8}:
            mcu_width, mcu_height = mcu_height, mcu_width

        hat_target = unique_path(
            destination / "_portada" / rel.parent / f"{rel.stem}_cover_nomos8khat_4x.png"
        )
        codeformer_target = temporary_dir / "codeformer.png"
        final_target = unique_path(
            destination / "_final" / rel.parent / f"{rel.stem}_final.png"
        )
        with tempfile.TemporaryDirectory(prefix="pixelrestore-mask-") as mask_dir:
            mask_dir = Path(mask_dir)
            hybrid_target = mask_dir / "hybrid.png"
            mask_target = mask_dir / "mask.png"
            confidence_target = mask_dir / "confidence.png"
            status, experiment = crear_hibrido_y_mascaras(
                source,
                thumbnail_target,
                (mcu_width, mcu_height),
                hybrid_target,
                mask_target,
                confidence_target,
                binary_suspicious=has_damage_evidence,
            )
            result["final_message"] = experiment.get("message", "")

            hat_checkpoint = (
                Path(__file__).resolve().parents[2]
                / "assets"
                / "test"
                / "4xNomos8kHAT-L_otf.pth"
            )
            hat_status, hat_message = upscale_image_with_hat(
                thumbnail_target,
                hat_target,
                checkpoint=hat_checkpoint,
            )
            result["hat_message"] = hat_message
            if hat_status == "reconstructed":
                result["hat_output"] = str(hat_target)

            codeformer_output = ""
            if result["hat_output"]:
                codeformer_checkpoint = (
                    Path(__file__).resolve().parents[2]
                    / "assets"
                    / "test"
                    / "codeformer.pth"
                )
                codeformer_status, codeformer_message = restore_faces_with_codeformer(
                    hat_target,
                    codeformer_target,
                    checkpoint=codeformer_checkpoint,
                    fidelity=0.7,
                )
                result["codeformer_message"] = codeformer_message
                if codeformer_status == "reconstructed":
                    codeformer_output = str(codeformer_target)
            else:
                codeformer_message = (
                    "CodeFormer no se ejecutó porque NOMOS HAT no generó la portada de entrada."
                )
                result["codeformer_message"] = codeformer_message

            if status != "experimental":
                return result

            guide_path = codeformer_output or result["hat_output"]
            if not guide_path:
                result["final_message"] = (
                    f"No se pudo generar la guía NOMOS HAT/CodeFormer. {hat_message}"
                )
                return result
            reconstruction_guide = Path(guide_path)
            composition_status, composition_message = create_experimental_reconstruction(
                hybrid_target,
                reconstruction_guide,
                mask_target,
                final_target,
            )
            result["final_message"] = (
                f"{composition_message} "
                + (
                    "NOMOS 8k HAT aporta la textura general y CodeFormer refina las caras detectadas "
                    "(fidelidad 0.7); la salida se mezcla únicamente en la máscara experimental."
                    if codeformer_output
                    else (
                        "CodeFormer no estuvo disponible; se usó NOMOS 8k HAT sin restauración facial."
                    )
                )
            )
            if composition_status == "reconstructed":
                result["final_output"] = str(final_target)
            if hat_status != "reconstructed":
                result["final_message"] = (
                    f"{result['final_message']} HAT no estuvo disponible: {hat_message}"
                )
            if not codeformer_output:
                result["final_message"] = (
                    f"{result['final_message']} CodeFormer no generó una guía facial: "
                    f"{codeformer_message}"
                )

    return result


def process_file(source: Path, root: Path, destination: Path) -> dict:
    started = datetime.now().isoformat(timespec="seconds")
    rel = source.relative_to(root)
    out = unique_path(destination / rel)
    checked = validate_image(source)
    status, method, message, out_path = "valid", "Ninguno", checked.message, ""
    jpeg_analysis = {}
    jpeg_experimental = {}
    is_jpeg = source.suffix.lower() in {".jpg", ".jpeg"}
    if is_jpeg:
        jpeg_analysis = analizar_jpeg_binario(source)
    jpeg_suspicious = bool(
        jpeg_analysis.get("entropy_anomalies") or jpeg_analysis.get("structural_errors")
    )
    if checked.ok and not jpeg_suspicious:
        # Already-valid images are diagnosed but omitted from the recovery folder.
        message = "La imagen ya es válida; no se copió a la carpeta de recuperadas."
    else:
        status, method = "unrecoverable", "Decodificación estándar"
        out.parent.mkdir(parents=True, exist_ok=True)
        # Recovery is only attempted for formats Pillow can encode safely.
        if source.suffix.lower() in SUPPORTED:
            if source.suffix.lower() in {".jpg", ".jpeg"}:
                recovered, recovery_message = recover_image(source, out)
                if recovered == "partial":
                    status, method, message, out_path = (
                        "partial", "Decodificación de datos disponibles", recovery_message, str(out)
                    )
                else:
                    status, message = recovered, recovery_message
            else:
                recovered, message = recover_image(source, out)
                if recovered == "partial":
                    status, method, out_path = "partial", "Decodificación de datos disponibles", str(out)
                else:
                    status = recovered
    if is_jpeg:
        jpeg_experimental = _process_jpeg_experimental(
            source,
            rel,
            destination,
            jpeg_analysis,
        )
        if status == "unrecoverable" and jpeg_experimental.get("hat_output"):
            status, method, message, out_path = (
                "low_quality", "Portada NOMOS HAT ×4",
                (
                    "Portada NOMOS HAT generada desde la miniatura EXIF. "
                    f"{jpeg_experimental.get('thumbnail_message', '')}"
                ),
                jpeg_experimental["hat_output"],
            )

    result = {
        "name": source.name, "relative_path": str(rel), "source": str(source),
        "output": out_path, "format": checked.format or source.suffix.lstrip("."),
        "size": source.stat().st_size,
        "error": (
            f"JPEG con {len(jpeg_analysis.get('entropy_anomalies', []))} marcador(es) sospechoso(s) y "
            f"{len(jpeg_analysis.get('structural_errors', []))} anomalía(s) estructural(es)."
            if jpeg_suspicious else "" if checked.ok else checked.message
        ),
        "method": method, "status": status, "message": message,
        "processed_at": started,
        "jpeg_analysis": json.dumps(jpeg_analysis, ensure_ascii=False) if jpeg_analysis else "",
    }
    if jpeg_suspicious:
        marker_count = len(jpeg_analysis.get("entropy_anomalies", []))
        structural_count = len(jpeg_analysis.get("structural_errors", []))
        evidence_message = (
            f"Análisis binario: {marker_count} marcador(es) sospechoso(s) y "
            f"{structural_count} anomalía(s) estructural(es); requiere revisión experimental."
        )
        result["message"] = f"{result['message']} {evidence_message}".strip()
    result.update(jpeg_experimental)
    return result
