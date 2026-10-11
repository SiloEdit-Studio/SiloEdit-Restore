from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from src.core.recovery import process_file
from src.core.validators import Validation, validate_image
from src.core import recovery
from src.core import validators
from src.core.codeformer_runner import _install_local_face_models


def test_valid_image_is_not_copied_to_recovery_destination(tmp_path: Path):
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    source.mkdir()
    image = source / "valid.png"
    Image.new("RGB", (8, 6), "navy").save(image)
    original = image.read_bytes()
    result = process_file(image, source, dest)
    assert result["status"] == "valid"
    assert image.read_bytes() == original
    assert result["output"] == ""
    assert not dest.exists()


def test_unreadable_input_is_never_claimed_recovered(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    image = source / "broken.jpg"
    image.write_bytes(b"not an image")
    original = image.read_bytes()
    result = process_file(image, source, tmp_path / "dest")
    assert result["status"] in {"unrecoverable", "partial"}
    assert image.read_bytes() == original
    if result["output"]:
        assert validate_image(Path(result["output"])).ok


def test_truncated_image_is_partial_only_when_output_validates(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    image = source / "truncated.png"
    Image.new("RGB", (24, 24), "green").save(image)
    original = image.read_bytes()
    image.write_bytes(original[:-20])
    damaged = image.read_bytes()
    result = process_file(image, source, tmp_path / "dest")
    assert result["status"] in {"partial", "unrecoverable"}
    assert image.read_bytes() == damaged
    if result["status"] == "partial":
        assert validate_image(Path(result["output"])).ok


def test_altered_header_is_not_mislabeled_as_repaired(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    image = source / "header.png"
    Image.new("RGB", (4, 4), "black").save(image)
    data = bytearray(image.read_bytes())
    data[:8] = b"BROKEN!!"
    image.write_bytes(data)
    original = image.read_bytes()
    result = process_file(image, source, tmp_path / "dest")
    assert result["status"] in {"partial", "unrecoverable"}
    assert image.read_bytes() == original
    if result["output"]:
        assert validate_image(Path(result["output"])).ok


def test_duplicate_output_does_not_overwrite(tmp_path: Path, monkeypatch):
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    source.mkdir(); dest.mkdir()
    (dest / "same.png").write_bytes(b"keep me")
    image = source / "same.png"
    image.write_bytes(b"damaged image")

    monkeypatch.setattr(recovery, "validate_image", lambda path: Validation(False, "dañado", "PNG"))

    def fake_recover(_source, output):
        Image.new("RGB", (2, 2), "red").save(output)
        return "partial", "parcial"

    monkeypatch.setattr(recovery, "recover_image", fake_recover)
    result = process_file(image, source, dest)

    assert result["status"] == "partial"
    assert (dest / "same.png").read_bytes() == b"keep me"
    assert (dest / "same_1.png").exists()


def test_damaged_jpeg_saves_only_final_and_cover_outputs(tmp_path: Path, monkeypatch):
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    source.mkdir()
    image = source / "Camera" / "tractor.jpg"
    image.parent.mkdir()
    original = b"damaged jpeg bytes"
    image.write_bytes(original)

    def save_image(_source, output, color="navy"):
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), color).save(output)

    monkeypatch.setattr(
        recovery,
        "validate_image",
        lambda path: Validation(Path(path) != image, "dañado" if Path(path) == image else "ok", "JPEG"),
    )
    monkeypatch.setattr(
        recovery,
        "recover_image",
        lambda _source, _output: ("unrecoverable", "sin datos decodificables"),
    )

    def fake_thumbnail(_source, output):
        save_image(_source, output)
        return "low_quality", "miniatura"

    def fake_hybrid(_source, _thumbnail, _mcu_size, hybrid, mask, confidence, **_kwargs):
        save_image(_source, hybrid, "gray")
        save_image(_source, mask, "white")
        save_image(_source, confidence, "gray")
        return "experimental", {
            "message": "híbrida",
            "suspect_mcus": 1,
            "damaged_percent": 25.0,
            "pixel_count": 256,
            "composition": {"gray_pixels_from_thumbnail": 64},
        }

    hat_calls = []

    def fake_hat(source_path, output, **options):
        source_path = Path(source_path)
        output = Path(output)
        hat_calls.append((source_path, output, options))
        with Image.open(source_path) as input_image:
            size = (input_image.width * 4, input_image.height * 4)
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, "orange").save(output)
        return "reconstructed", "NOMOS 8k HAT-L ×4"

    codeformer_calls = []

    def fake_codeformer(source_path, output, **options):
        source_path = Path(source_path)
        output = Path(output)
        codeformer_calls.append((source_path, output, options))
        with Image.open(source_path) as input_image:
            size = input_image.size
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, "violet").save(output)
        return "reconstructed", "CodeFormer restored 1 face at fidelity 0.7."

    composition_inputs = []

    def fake_composition(hybrid, upscaled_thumbnail, mask, output):
        composition_inputs.append((Path(hybrid), Path(upscaled_thumbnail), Path(mask)))
        save_image(image, output, "purple")
        return "reconstructed", "combinada"

    monkeypatch.setattr(recovery, "analizar_jpeg_binario", lambda _source: {
        "file_size": len(original),
        "has_soi": True,
        "has_eoi": False,
        "frame": {"marker": "SOF0", "width": 16, "height": 16},
        "orientation": 1,
        "mcu": {"width": 16, "height": 16, "count": 1},
        "markers": [],
        "scans": [],
        "entropy_anomalies": [{"marker": "FF08", "offset": 10}],
        "structural_errors": [],
        "trailing_bytes": 0,
    })
    monkeypatch.setattr(recovery, "recover_exif_thumbnail", fake_thumbnail)
    monkeypatch.setattr(recovery, "crear_hibrido_y_mascaras", fake_hybrid)
    monkeypatch.setattr(recovery, "upscale_image_with_hat", fake_hat)
    monkeypatch.setattr(recovery, "restore_faces_with_codeformer", fake_codeformer)
    monkeypatch.setattr(recovery, "create_experimental_reconstruction", fake_composition)

    result = process_file(image, source, dest)

    assert image.read_bytes() == original
    assert result["status"] == "low_quality"
    assert result["output"] == result["hat_output"]
    assert result["hat_output"].endswith("_portada\\Camera\\tractor_cover_nomos8khat_4x.png")
    assert "thumbnail_output" not in result
    assert "codeformer_output" not in result
    assert result["final_output"].endswith("_final\\Camera\\tractor_final.png")
    assert "fidelidad 0.7" in result["final_message"]
    assert len(hat_calls) == 1
    hat_source, hat_output, hat_options = hat_calls[0]
    assert hat_source.is_absolute()
    assert hat_output == Path(result["hat_output"])
    assert hat_options["checkpoint"].name == "4xNomos8kHAT-L_otf.pth"
    with Image.open(hat_output) as hat_result:
        assert hat_result.size == (64, 64)
    assert len(codeformer_calls) == 1
    codeformer_input, codeformer_path, codeformer_options = codeformer_calls[0]
    codeformer_intermediate = Path(codeformer_path)
    assert codeformer_input == Path(result["hat_output"])
    assert codeformer_options == {
        "checkpoint": Path(recovery.__file__).resolve().parents[1].parent
        / "assets" / "test" / "codeformer.pth",
        "fidelity": 0.7,
    }
    assert codeformer_intermediate.parent.name.startswith("pixelrestore-jpeg-")
    assert not codeformer_intermediate.exists()
    assert not hat_source.exists()
    assert len(composition_inputs) == 1
    hybrid, cover, mask = composition_inputs[0]
    assert hybrid.parent.name.startswith("pixelrestore-mask-")
    assert cover == codeformer_intermediate
    assert not cover.exists()
    assert mask.parent == hybrid.parent
    assert not (dest / "_Exa").exists()
    assert not (dest / "_experimental").exists()
    assert not (dest / "_experimentalReconstructionRealESRGANx2").exists()
    assert not (dest / "_Reconstruction").exists()
    assert not (dest / "_2xPlus").exists()
    assert not (dest / "_2xPlus_DoublePass").exists()
    assert not (dest / "_GFPGAN").exists()
    assert len(list((dest / "_final" / "Camera").iterdir())) == 1
    assert len(list((dest / "_portada" / "Camera").iterdir())) == 1
    assert not (dest / "_thumbnails").exists()
    assert not (dest / "_CodeFormer").exists()


def test_ai_upscale_is_exactly_two_times_thumbnail_size_and_does_not_overwrite(tmp_path: Path, monkeypatch):
    thumbnail = tmp_path / "thumbnail.png"
    output = tmp_path / "reconstruction.png"
    Image.new("RGB", (32, 24), "blue").save(thumbnail)

    class FakeSuperRes:
        def __init__(self, scale):
            self.scale = scale

        def upsample(self, pixels):
            import cv2

            return cv2.resize(
                pixels,
                (pixels.shape[1] * self.scale, pixels.shape[0] * self.scale),
                interpolation=cv2.INTER_NEAREST,
            )

    monkeypatch.setattr(validators, "_get_edsr_superres", lambda scale=2: FakeSuperRes(scale))

    status, message = validators.upscale_thumbnail_with_ai(thumbnail, output)
    assert status == "reconstructed"
    assert "2×" in message
    assert validate_image(output).ok
    with Image.open(output) as result:
        assert result.size == (64, 48)
    original_output = output.read_bytes()
    status, message = validators.upscale_thumbnail_with_ai(thumbnail, output)
    assert status == "unavailable"
    assert "no se sobrescribió" in message
    assert output.read_bytes() == original_output

    x3_output = tmp_path / "reconstruction_x3.png"
    status, message = validators.upscale_thumbnail_with_ai(thumbnail, x3_output, scale=3)
    assert status == "reconstructed"
    assert "3×" in message
    with Image.open(x3_output) as result:
        assert result.size == (96, 72)


def test_normal_upscale_is_exactly_two_times_and_does_not_overwrite(tmp_path: Path):
    source = tmp_path / "source.png"
    output = tmp_path / "normal.png"
    Image.new("RGB", (23, 17), "blue").save(source)

    status, message = validators.upscale_image_lanczos(source, output)

    assert status == "upscaled"
    assert "Lanczos 2×" in message
    assert validate_image(output).ok
    with Image.open(output) as result:
        assert result.size == (46, 34)
    original_output = output.read_bytes()
    status, message = validators.upscale_image_lanczos(source, output)
    assert status == "unavailable"
    assert "no se sobrescribió" in message
    assert output.read_bytes() == original_output


def test_hat_upscale_uses_local_runner_and_validates_four_times_output(tmp_path: Path, monkeypatch):
    source = tmp_path / "thumbnail.png"
    output = tmp_path / "hat.png"
    checkpoint = tmp_path / "4xNomos8kHAT-L_otf.pth"
    runtime_python = tmp_path / "python.exe"
    checkpoint.write_bytes(b"test checkpoint")
    runtime_python.write_bytes(b"test interpreter")
    Image.new("RGB", (23, 17), "blue").save(source)
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        assert Path(command[4]) == checkpoint
        assert Path(command[3]) != output
        Image.new("RGB", (92, 68), "green").save(command[3])
        return SimpleNamespace(returncode=0, stdout="NOMOS HAT CUDA", stderr="")

    monkeypatch.setenv("PIXELRESTORE_REALESRGAN_PYTHON", str(runtime_python))
    monkeypatch.setattr(validators.subprocess, "run", fake_run)

    status, message = validators.upscale_image_with_hat(
        source,
        output,
        checkpoint=checkpoint,
    )

    assert status == "reconstructed"
    assert "NOMOS 8k HAT-L local ×4" in message
    assert "NOMOS HAT CUDA" in message
    assert len(commands) == 1
    assert Path(commands[0][1]).name == "hat_runner.py"
    assert validate_image(output).size == (92, 68)
    original_output = output.read_bytes()
    status, message = validators.upscale_image_with_hat(
        source,
        output,
        checkpoint=checkpoint,
    )
    assert status == "unavailable"
    assert "no se sobrescribió" in message
    assert output.read_bytes() == original_output


def test_codeformer_uses_local_runner_and_validates_face_output(tmp_path: Path, monkeypatch):
    source = tmp_path / "cover.png"
    output = tmp_path / "codeformer.png"
    checkpoint = tmp_path / "codeformer.pth"
    runtime_python = tmp_path / "python.exe"
    checkpoint.write_bytes(b"test checkpoint")
    runtime_python.write_bytes(b"test interpreter")
    Image.new("RGB", (64, 48), "blue").save(source)
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        assert Path(command[4]) == checkpoint
        assert command[5] == "0.65"
        assert Path(command[3]) != output
        Image.new("RGB", (64, 48), "green").save(command[3])
        return SimpleNamespace(returncode=0, stdout="CodeFormer CUDA", stderr="")

    monkeypatch.setenv("PIXELRESTORE_REALESRGAN_PYTHON", str(runtime_python))
    monkeypatch.setattr(validators.subprocess, "run", fake_run)

    status, message = validators.restore_faces_with_codeformer(
        source,
        output,
        checkpoint=checkpoint,
        fidelity=0.65,
    )

    assert status == "reconstructed"
    assert "CodeFormer CUDA" in message
    assert len(commands) == 1
    assert Path(commands[0][1]).name == "codeformer_runner.py"
    assert validate_image(output).size == (64, 48)
    original_output = output.read_bytes()
    status, message = validators.restore_faces_with_codeformer(
        source,
        output,
        checkpoint=checkpoint,
        fidelity=0.65,
    )
    assert status == "unavailable"
    assert "no se sobrescribió" in message
    assert output.read_bytes() == original_output


def test_codeformer_installs_face_models_at_facexlib_cache_root(tmp_path: Path):
    checkpoint_directory = tmp_path / "assets"
    cache_root = tmp_path / "cache"
    checkpoint_directory.mkdir()
    model_data = {
        "detection_mobilenet0.25_Final.pth": b"detector",
        "parsing_parsenet.pth": b"parser",
    }
    for filename, content in model_data.items():
        (checkpoint_directory / filename).write_bytes(content)

    installed_root = _install_local_face_models(checkpoint_directory, cache_root)

    assert installed_root == cache_root
    for filename, content in model_data.items():
        assert (cache_root / filename).read_bytes() == content
        assert not (cache_root / "facexlib" / "weights" / filename).exists()


def test_experimental_reconstruction_uses_upscaled_thumbnail_only_inside_mask(tmp_path: Path):
    base_path = tmp_path / "hybrid.png"
    guide_path = tmp_path / "guide.png"
    mask_path = tmp_path / "mask.png"
    output_path = tmp_path / "composite.png"
    base = Image.new("RGB", (96, 96), "gray")
    guide = Image.new("RGB", (16, 16), "blue")
    mask = Image.new("L", (96, 96), 0)
    mask.paste(255, (32, 32, 64, 64))
    base.save(base_path)
    guide.save(guide_path)
    mask.save(mask_path)

    status, message = validators.create_experimental_reconstruction(
        base_path, guide_path, mask_path, output_path
    )

    assert status == "reconstructed"
    assert "máscara" in message
    assert validate_image(output_path).ok
    with Image.open(output_path) as output:
        assert output.size == base.size
        assert output.getpixel((0, 0)) == base.getpixel((0, 0))
        assert output.getpixel((10, 10)) == base.getpixel((10, 10))
        assert output.getpixel((48, 24))[2] > base.getpixel((48, 24))[2]
        assert output.getpixel((48, 30))[2] > output.getpixel((48, 24))[2]
        assert output.getpixel((48, 48))[2] > output.getpixel((48, 48))[0]
