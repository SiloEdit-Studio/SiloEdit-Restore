"""Image validation and conservative recovery helpers."""
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import urllib.request
import warnings


_AI_SUPERRES: dict[int, object] = {}
_AI_MODELS = {
    2: (
        "https://raw.githubusercontent.com/Saafke/EDSR_Tensorflow/master/models/EDSR_x2.pb",
        "585623221baa070279a0d1e7e113a4c3faba0f318ca7fdd9a65d9afc0763d9b4",
        "edsr-x2.pb",
    ),
    3: (
        "https://raw.githubusercontent.com/Saafke/EDSR_Tensorflow/master/models/EDSR_x3.pb",
        "3baa3740fdb8ee9c52f1a41d69fa74cb9feef0fa9bfeec24f0ee58b928068e9a",
        "edsr-x3.pb",
    ),
}


@dataclass
class Validation:
    ok: bool
    message: str
    format: str = ""
    size: tuple[int, int] | None = None
    truncated: bool = False


def validate_image(path: Path) -> Validation:
    try:
        with Image.open(path) as image:
            fmt, size = image.format or path.suffix.lstrip("."), image.size
            image.verify()
        # verify() checks structure; a second decode catches pixel-data failures.
        with Image.open(path) as image:
            image.load()
        return Validation(True, "Imagen decodificada y verificada.", fmt, size)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        return Validation(False, str(exc) or "No se pudo decodificar la imagen.", path.suffix.lstrip(".").upper())


def recover_image(source: Path, destination: Path) -> tuple[str, str]:
    """Try Pillow's explicit truncated-image decode path; never edits source."""
    from PIL import ImageFile
    old = ImageFile.LOAD_TRUNCATED_IMAGES
    try:
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with Image.open(source) as image:
                fmt = (image.format or source.suffix.lstrip(".")).upper()
                image.load()
                # Keep camera orientation and color metadata when the codec supports it.
                options = {}
                for key in ("exif", "icc_profile", "dpi"):
                    if key in image.info:
                        options[key] = image.info[key]
                if fmt in {"JPG", "JPEG"}:
                    options["quality"] = 95
                try:
                    image.save(destination, format=fmt, **options)
                except (TypeError, OSError):
                    # Some Pillow format plugins reject optional metadata arguments.
                    image.save(destination, format=fmt)
        result = validate_image(destination)
        if result.ok:
            return "partial", "Se pudo decodificar y validar una salida; el contenido ausente no se reconstruyó."
        destination.unlink(missing_ok=True)
        return "unrecoverable", "La salida parcial no superó la validación final."
    except Exception as exc:
        destination.unlink(missing_ok=True)
        return "unrecoverable", f"No fue posible recuperar el archivo: {exc}"
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = old


def recover_exif_thumbnail(source: Path, destination: Path) -> tuple[str, str]:
    """Extract a valid embedded EXIF JPEG thumbnail without touching the source."""
    try:
        with Image.open(source) as original:
            orientation = original.getexif().get(274, 1)
        data = source.read_bytes()
        if not data.startswith(b"\xff\xd8"):
            return "unrecoverable", "El archivo no tiene un encabezado JPEG."

        # Try the standard EXIF thumbnail first. Some cameras keep it inside
        # vendor APP1 data instead of the standard IFD1, so retain marker scan fallback.
        candidates = []
        try:
            import piexif
            standard_thumbnail = piexif.load(data).get("thumbnail")
            if standard_thumbnail:
                candidates.append(standard_thumbnail)
        except Exception:
            pass
        pos = 2
        while pos + 4 <= len(data):
            if data[pos] != 0xFF:
                break
            while pos < len(data) and data[pos] == 0xFF:
                pos += 1
            if pos >= len(data):
                break
            marker = data[pos]
            pos += 1
            if marker in (0xD9, 0xDA):
                break
            if marker in (0x01, *range(0xD0, 0xD8)):
                continue
            if pos + 2 > len(data):
                break
            segment_length = int.from_bytes(data[pos:pos + 2], "big")
            end = pos + segment_length
            if segment_length < 2 or end > len(data):
                break
            if marker == 0xE1 and data[pos + 2:pos + 8] == b"Exif\x00\x00":
                exif_segment = data[pos + 2:end]
                candidates.append(exif_segment)
            pos = end

        embedded_image = None
        for candidate in candidates:
            # Standard piexif thumbnails are raw JPEGs; vendor APP1 payloads
            # may contain metadata before their nested JPEG stream.
            start = candidate.find(b"\xff\xd8")
            while start >= 0:
                finish = candidate.find(b"\xff\xd9", start + 2)
                if finish < 0:
                    break
                payload = candidate[start:finish + 2]
                try:
                    probe = Image.open(io.BytesIO(payload))
                    probe.load()
                    if probe.format == "JPEG" and probe.width >= 64 and probe.height >= 64:
                        embedded_image = probe.copy()
                        break
                except (OSError, ValueError, SyntaxError):
                    pass
                start = candidate.find(b"\xff\xd8", start + 2)
            if embedded_image is not None:
                break
        if embedded_image is None:
            return "unrecoverable", "No se encontró una miniatura JPEG EXIF completa y válida."
        if orientation in range(2, 9):
            embedded_image.getexif()[274] = orientation
            embedded_image = ImageOps.exif_transpose(embedded_image)
        destination.parent.mkdir(parents=True, exist_ok=True)
        embedded_image.save(destination, format="JPEG", quality=95)
        embedded_image.close()

        checked = validate_image(destination)
        if checked.ok:
            return "low_quality", f"Miniatura EXIF original extraída ({checked.size[0]}×{checked.size[1]} px); no reconstruye el detalle de resolución completa."
        destination.unlink(missing_ok=True)
        return "unrecoverable", "La miniatura EXIF extraída no superó la validación."
    except Exception as exc:
        destination.unlink(missing_ok=True)
        return "unrecoverable", f"No fue posible extraer una miniatura EXIF: {exc}"


def _decode_truncated_rgb(source: Path) -> Image.Image:
    from PIL import ImageFile

    old = ImageFile.LOAD_TRUNCATED_IMAGES
    try:
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with Image.open(source) as source_image:
                source_image.load()
                return ImageOps.exif_transpose(source_image).convert("RGB")
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = old


def _neutral_gray_mask(pixels):
    import numpy as np

    try:
        import cv2
    except ImportError:
        cv2 = None

    spread = pixels.max(axis=2) - pixels.min(axis=2)
    midgray_distance = np.abs(pixels[:, :, 0].astype(np.int16) - 128)
    raw_mask = ((spread <= 3) & (midgray_distance <= 6)).astype(np.uint8)
    min_area = max(2048, int(pixels.shape[0] * pixels.shape[1] * 0.0005))
    if cv2 is None:
        mask = raw_mask.astype(bool)
    else:
        _, labels, stats, _ = cv2.connectedComponentsWithStats(raw_mask, connectivity=8)
        eligible = np.flatnonzero(
            (stats[1:, cv2.CC_STAT_AREA] >= min_area)
            & (stats[1:, cv2.CC_STAT_WIDTH] >= 32)
            & (stats[1:, cv2.CC_STAT_HEIGHT] >= 32)
        ) + 1
        mask = np.isin(labels, eligible)
    return mask, min_area


def create_gray_region_experiment(source: Path, thumbnail: Path, destination: Path) -> tuple[str, str]:
    """Create a lossless PNG experiment replacing only large neutral-gray dead regions."""
    try:
        import numpy as np
        damaged = _decode_truncated_rgb(source)
        with Image.open(thumbnail) as embedded:
            guide = embedded.convert("RGB").resize(damaged.size, Image.Resampling.LANCZOS)

        pixels = np.asarray(damaged)
        guide_pixels = np.asarray(guide)
        mask, min_area = _neutral_gray_mask(pixels)
        replaced = int(mask.sum())

        if replaced < min_area:
            return "unavailable", "No se detectaron sectores grandes de gris neutro para reemplazar."

        # PNG avoids the global pixel changes that a lossy JPEG re-encode causes.
        combined = pixels.copy()
        combined[mask] = guide_pixels[mask]
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(combined).save(destination, format="PNG", optimize=True)
        percent = replaced * 100.0 / (pixels.shape[0] * pixels.shape[1])
        return "experimental", (
            f"Vista experimental: {percent:.2f}% de sectores gris neutro reemplazados por la miniatura EXIF "
            f"reescalada a {damaged.width}×{damaged.height}. Detalle interpolado, no recuperado."
        )
    except Exception as exc:
        destination.unlink(missing_ok=True)
        return "unavailable", f"No se pudo crear la salida experimental: {exc}"


def _get_edsr_superres(scale: int = 2):
    if scale not in _AI_MODELS:
        raise ValueError(f"Escala EDSR no soportada: {scale}.")
    if scale in _AI_SUPERRES:
        return _AI_SUPERRES[scale]

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "Falta OpenCV contrib. Instalá las dependencias con "
            "'python -m pip install -r requirements-ai.txt'."
        ) from exc
    if not hasattr(cv2, "dnn_superres"):
        raise RuntimeError(
            "La instalación de OpenCV no incluye dnn_superres. Instalá "
            "'opencv-contrib-python' con requirements-ai.txt."
        )

    model_url, expected_sha256, filename = _AI_MODELS[scale]
    model_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache"))
    model_path = model_root / "PixelRestore" / "models" / filename
    model_path.parent.mkdir(parents=True, exist_ok=True)
    if model_path.exists():
        digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        if digest != expected_sha256:
            raise RuntimeError(f"El modelo EDSR x{scale} almacenado no pasó la verificación SHA-256: {model_path}")
    else:
        temporary_path = None
        try:
            with urllib.request.urlopen(model_url, timeout=60) as response:
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=model_path.parent, prefix="edsr-x2-", suffix=".tmp", delete=False
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    digest = hashlib.sha256()
                    while chunk := response.read(1024 * 1024):
                        digest.update(chunk)
                        temporary.write(chunk)
            if digest.hexdigest() != expected_sha256:
                raise RuntimeError(f"El modelo EDSR x{scale} descargado no pasó la verificación SHA-256.")
            os.replace(temporary_path, model_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    superres = cv2.dnn_superres.DnnSuperResImpl_create()
    superres.readModel(str(model_path))
    superres.setModel("edsr", scale)
    _AI_SUPERRES[scale] = superres
    return superres


def upscale_thumbnail_with_ai(
    thumbnail: Path,
    destination: Path,
    scale: int = 2,
) -> tuple[str, str]:
    """Create a conservative EDSR upscale of an authentic embedded thumbnail."""
    created_destination = False
    try:
        import cv2
        import numpy as np

        if scale not in _AI_MODELS:
            return "unavailable", f"Escala EDSR no soportada: {scale}."
        if destination.exists():
            return "unavailable", "La salida de reconstrucción ya existe; no se sobrescribió."
        with Image.open(thumbnail) as embedded:
            source = embedded.convert("RGB")
        if source.width < 8 or source.height < 8:
            return "unavailable", "La miniatura es demasiado pequeña para la superresolución EDSR."

        source_bgr = cv2.cvtColor(np.asarray(source), cv2.COLOR_RGB2BGR)
        inferred_bgr = _get_edsr_superres(scale).upsample(source_bgr)
        inferred_rgb = cv2.cvtColor(inferred_bgr, cv2.COLOR_BGR2RGB)
        expected_size = (source.width * scale, source.height * scale)
        if inferred_rgb.shape[:2] != (expected_size[1], expected_size[0]):
            return "unavailable", (
                f"El modelo devolvió {inferred_rgb.shape[1]}×{inferred_rgb.shape[0]}, "
                f"se esperaba {expected_size[0]}×{expected_size[1]}."
            )

        model_image = Image.fromarray(inferred_rgb)
        baseline = source.resize(expected_size, Image.Resampling.LANCZOS)
        # Keep the authentic thumbnail's geometry and color as a restraint on model detail.
        result = Image.blend(baseline, model_image, 0.65)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            created_destination = True
            result.save(stream, format="PNG", optimize=True)
        checked = validate_image(destination)
        if not checked.ok or checked.size != expected_size:
            destination.unlink(missing_ok=True)
            created_destination = False
            return "unavailable", f"La salida EDSR {scale}× no superó la validación final."
        return "reconstructed", (
            f"Superresolución neuronal EDSR {scale}× local: miniatura EXIF {source.width}×{source.height} "
            f"a {expected_size[0]}×{expected_size[1]}. Se mezcló 65% de salida del modelo con "
            "35% de ampliación Lanczos para moderar cambios de estructura. La textura fina es inferida, "
            "no recuperada; el modelo no inventa ni repara píxeles del JPEG original."
        )
    except Exception as exc:
        if created_destination:
            destination.unlink(missing_ok=True)
        return "unavailable", f"No se pudo generar la ampliación EDSR {scale}×: {exc}"


def upscale_image_lanczos(
    source: Path,
    destination: Path,
    scale: int = 2,
) -> tuple[str, str]:
    """Create a conventional Lanczos upscale for a direct comparison."""
    created_destination = False
    try:
        if scale < 1:
            return "unavailable", f"Escala de ampliación inválida: {scale}."
        if destination.exists():
            return "unavailable", "La salida normal ya existe; no se sobrescribió."
        with Image.open(source) as image:
            original = image.convert("RGB")
        expected_size = (original.width * scale, original.height * scale)
        result = original.resize(expected_size, Image.Resampling.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            created_destination = True
            result.save(stream, format="PNG", optimize=True)
        checked = validate_image(destination)
        if not checked.ok or checked.size != expected_size:
            destination.unlink(missing_ok=True)
            created_destination = False
            return "unavailable", "La ampliación normal no superó la validación final."
        return "upscaled", (
            f"Ampliación convencional Lanczos {scale}× desde {original.width}×{original.height} "
            f"a {expected_size[0]}×{expected_size[1]}; solo interpola píxeles y no agrega detalle."
        )
    except Exception as exc:
        if created_destination:
            destination.unlink(missing_ok=True)
        return "unavailable", f"No se pudo crear la ampliación normal {scale}×: {exc}"


def upscale_image_with_hat(
    source: Path,
    destination: Path,
    checkpoint: Path | None = None,
) -> tuple[str, str]:
    """Run the local 4x NOMOS HAT checkpoint on an EXIF cover."""
    model_path = checkpoint or (
        Path(__file__).resolve().parents[2]
        / "assets"
        / "test"
        / "4xNomos8kHAT-L_otf.pth"
    )
    return _run_local_upscaler(
        source,
        destination,
        model_path,
        "NOMOS 8k HAT-L",
        "hat",
        [],
        4,
        "NOMOS HAT",
    )


def _run_local_upscaler(
    source: Path,
    destination: Path,
    model_path: Path,
    model_name: str,
    runner_name: str,
    runner_arguments: list[str],
    scale: int,
    engine_name: str,
) -> tuple[str, str]:
    """Execute and validate an isolated local upscaler without overwriting files."""
    created_destination = False
    try:
        if scale not in {2, 4}:
            return "unavailable", f"Escala {engine_name} no soportada: {scale}×."
        if destination.exists():
            return "unavailable", f"La salida {model_name} ya existe; no se sobrescribió."
        if not model_path.is_file():
            return "unavailable", f"No se encontró el checkpoint local {model_name}: {model_path}"

        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        runtime_python = Path(
            os.environ.get(
                "PIXELRESTORE_REALESRGAN_PYTHON",
                str(local_app_data / "PixelRestore" / "realesrgan-runtime" / "Scripts" / "python.exe"),
            )
        )
        if not runtime_python.is_file():
            return "unavailable", (
                "No está instalado el entorno aislado de inferencia. Configurá Python 3.12 y "
                "las dependencias indicadas en requirements-realesrgan.txt."
            )

        runner = Path(__file__).with_name(f"{runner_name}_runner.py")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.stem}-",
            suffix=".tmp.png",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        temporary_path.unlink()
        completed = subprocess.run(
            [
                str(runtime_python), str(runner), str(source), str(temporary_path),
                str(model_path), *runner_arguments,
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            return "unavailable", (
                f"{engine_name} no pudo completar la inferencia local"
                + (f": {detail[-1200:]}" if detail else ".")
            )
        checked = validate_image(temporary_path)
        with Image.open(source) as original:
            expected_size = (original.width * scale, original.height * scale)
        if not checked.ok or checked.size != expected_size:
            return "unavailable", (
                f"La salida {model_name} no pasó validación o no mide "
                f"{expected_size[0]}×{expected_size[1]}."
            )
        with temporary_path.open("rb") as source_stream:
            with destination.open("xb") as destination_stream:
                created_destination = True
                shutil.copyfileobj(source_stream, destination_stream)
        checked = validate_image(destination)
        if not checked.ok or checked.size != expected_size:
            destination.unlink(missing_ok=True)
            created_destination = False
            return "unavailable", f"La salida copiada de {model_name} no superó la validación final."
        device = completed.stdout.strip() or "GPU/CUDA o CPU detectada por el runner"
        return "reconstructed", (
            f"{model_name} local ×{scale} desde {original.width}×{original.height}; "
            f"salida {expected_size[0]}×{expected_size[1]}. La textura y el detalle fino son inferidos, "
            f"no recuperados. {device}"
        )
    except subprocess.TimeoutExpired:
        if created_destination:
            destination.unlink(missing_ok=True)
        return "unavailable", f"La inferencia {engine_name} superó el límite de 10 minutos."
    except Exception as exc:
        if created_destination:
            destination.unlink(missing_ok=True)
        return "unavailable", f"No se pudo ejecutar {engine_name} local: {exc}"
    finally:
        if "temporary_path" in locals() and temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def restore_faces_with_codeformer(
    source: Path,
    destination: Path,
    checkpoint: Path | None = None,
    fidelity: float = 0.7,
) -> tuple[str, str]:
    """Restore detected faces using the local CodeFormer checkpoint."""
    if not 0.0 <= fidelity <= 1.0:
        return "unavailable", f"Fidelidad CodeFormer fuera del rango permitido: {fidelity}."
    model_path = checkpoint or (
        Path(__file__).resolve().parents[2] / "assets" / "test" / "codeformer.pth"
    )
    return _run_local_face_restorer(
        source,
        destination,
        model_path,
        "CodeFormer",
        "codeformer",
        [str(fidelity)],
    )


def _run_local_face_restorer(
    source: Path,
    destination: Path,
    model_path: Path,
    model_name: str,
    runner_name: str,
    runner_arguments: list[str],
) -> tuple[str, str]:
    created_destination = False
    try:
        if destination.exists():
            return "unavailable", f"La salida {model_name} ya existe; no se sobrescribió."
        if not model_path.is_file():
            return "unavailable", f"No se encontró el checkpoint local {model_name}: {model_path}"

        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        runtime_python = Path(
            os.environ.get(
                "PIXELRESTORE_REALESRGAN_PYTHON",
                str(local_app_data / "PixelRestore" / "realesrgan-runtime" / "Scripts" / "python.exe"),
            )
        )
        if not runtime_python.is_file():
            return "unavailable", (
                f"No está instalado el entorno aislado para {model_name}. "
                "Configurá el entorno Python 3.12 indicado en requirements-realesrgan.txt."
            )

        runner = Path(__file__).with_name(f"{runner_name}_runner.py")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.stem}-",
            suffix=".tmp.png",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        temporary_path.unlink()

        completed = subprocess.run(
            [
                str(runtime_python), str(runner), str(source),
                str(temporary_path), str(model_path), *runner_arguments,
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            return "unavailable", (
                f"{model_name} no pudo restaurar una cara localmente"
                + (f": {detail[-1200:]}" if detail else ".")
            )

        checked = validate_image(temporary_path)
        with Image.open(source) as original:
            expected_size = original.size
        if not checked.ok or checked.size != expected_size:
            return "unavailable", (
                f"La salida {model_name} no pasó la validación de imagen y dimensiones."
            )
        with temporary_path.open("rb") as source_stream:
            with destination.open("xb") as destination_stream:
                created_destination = True
                shutil.copyfileobj(source_stream, destination_stream)
        checked = validate_image(destination)
        if not checked.ok or checked.size != expected_size:
            destination.unlink(missing_ok=True)
            created_destination = False
            return "unavailable", f"La salida copiada de {model_name} no superó la validación final."
        return "reconstructed", (
            completed.stdout.strip() or f"{model_name} restauró el rostro localmente."
        )
    except subprocess.TimeoutExpired:
        if created_destination:
            destination.unlink(missing_ok=True)
        return "unavailable", f"{model_name} superó el límite de 10 minutos."
    except Exception as exc:
        if created_destination:
            destination.unlink(missing_ok=True)
        return "unavailable", f"No se pudo ejecutar {model_name} local: {exc}"
    finally:
        if "temporary_path" in locals():
            temporary_path.unlink(missing_ok=True)


def create_experimental_reconstruction(
    experimental_image: Path,
    upscaled_thumbnail: Path,
    mask_path: Path,
    destination: Path,
) -> tuple[str, str]:
    """Color-match the EXIF guide and blend it through a feathered mask."""
    created_destination = False
    try:
        import numpy as np
        from PIL import ImageFilter

        if destination.exists():
            return "unavailable", "La salida experimental ya existe; no se sobrescribió."
        with Image.open(experimental_image) as image:
            base = image.convert("RGB")
        with Image.open(upscaled_thumbnail) as image:
            guide = image.convert("RGB")
        with Image.open(mask_path) as image:
            hard_mask = image.convert("L")
        if hard_mask.size != base.size:
            return "unavailable", "La máscara no coincide con el tamaño de la imagen experimental."
        selected = np.asarray(hard_mask) > 0
        if not selected.any():
            return "unavailable", "La máscara no marca regiones para reconstruir."
        if guide.size != base.size:
            guide = guide.resize(base.size, Image.Resampling.LANCZOS)

        base_pixels = np.asarray(base)
        guide_pixels = np.asarray(guide).copy()
        height, width = base.size[1], base.size[0]
        calibration_step = max(1, int(np.sqrt((width * height) / 100_000)))
        calibration_mask = np.asarray(hard_mask)[::calibration_step, ::calibration_step] == 0
        base_sample = base_pixels[::calibration_step, ::calibration_step][calibration_mask]
        guide_sample = guide_pixels[::calibration_step, ::calibration_step][calibration_mask]
        if len(base_sample) >= 128:
            for channel in range(3):
                target_low, target_mid, target_high = np.percentile(
                    base_sample[:, channel], (10, 50, 90)
                )
                guide_low, guide_mid, guide_high = np.percentile(
                    guide_sample[:, channel], (10, 50, 90)
                )
                guide_range = max(float(guide_high - guide_low), 1.0)
                gain = float(np.clip(
                    (target_high - target_low) / guide_range,
                    0.75,
                    1.33,
                ))
                offset = float(np.clip(target_mid - gain * guide_mid, -32, 32))
                guide_pixels[:, :, channel] = np.clip(
                    guide_pixels[:, :, channel].astype(np.float32) * gain + offset,
                    0,
                    255,
                ).astype(np.uint8)

        feather_radius = max(8.0, min(width, height) / 24.0)
        feathered = np.asarray(
            hard_mask.filter(ImageFilter.GaussianBlur(radius=feather_radius)),
            dtype=np.uint8,
        )
        blended = np.empty_like(base_pixels)
        for top in range(0, height, 128):
            bottom = min(top + 128, height)
            alpha = feathered[top:bottom, :, None].astype(np.uint32)
            base_chunk = base_pixels[top:bottom].astype(np.uint32)
            guide_chunk = guide_pixels[top:bottom].astype(np.uint32)
            blended_chunk = (
                base_chunk * (255 - alpha) + guide_chunk * alpha + 127
            )
            blended[top:bottom] = (blended_chunk // 255).astype(np.uint8)
        result = Image.fromarray(blended)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            created_destination = True
            result.save(stream, format="PNG", optimize=True)
        checked = validate_image(destination)
        if not checked.ok or checked.size != base.size:
            destination.unlink(missing_ok=True)
            created_destination = False
            return "unavailable", "La composición experimental no superó la validación final."
        replaced_percent = selected.sum() * 100.0 / selected.size
        return "reconstructed", (
            f"Composición experimental {base.width}×{base.height}: color de la guía ajustado con "
            f"píxeles fuera de la máscara, reemplazo en {replaced_percent:.2f}% marcado y transición "
            f"suavizada {feather_radius:.1f}px alrededor del borde. El original no se modifica."
        )
    except Exception as exc:
        if created_destination:
            destination.unlink(missing_ok=True)
        return "unavailable", f"No se pudo crear la composición experimental: {exc}"
