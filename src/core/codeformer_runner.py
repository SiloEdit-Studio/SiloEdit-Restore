"""Restore aligned faces locally with the official CodeFormer architecture."""
from contextlib import redirect_stdout
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import traceback
from types import ModuleType


def _prepare_official_architecture(source_root: Path):
    codeformer_archs = source_root / "basicsr" / "archs"
    required_sources = (
        codeformer_archs / "codeformer_arch.py",
        codeformer_archs / "vqgan_arch.py",
    )
    missing = [path for path in required_sources if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Official CodeFormer architecture files are missing from "
            f"{codeformer_archs}: {', '.join(path.name for path in missing)}"
        )

    import torchvision.transforms.functional as tv_functional

    functional_tensor = ModuleType("torchvision.transforms.functional_tensor")
    functional_tensor.rgb_to_grayscale = tv_functional.rgb_to_grayscale
    sys.modules.setdefault("torchvision.transforms.functional_tensor", functional_tensor)

    import basicsr.archs as basicsr_archs

    basicsr_archs.__path__.insert(0, str(codeformer_archs))
    return importlib.import_module("basicsr.archs.codeformer_arch").CodeFormer


def _install_local_face_models(
    checkpoint_directory: Path,
    cache_root: Path,
) -> Path:
    source_weights = checkpoint_directory
    destination_weights = cache_root
    destination_weights.mkdir(parents=True, exist_ok=True)
    for filename in ("detection_mobilenet0.25_Final.pth", "parsing_parsenet.pth"):
        source = source_weights / filename
        destination = destination_weights / filename
        if not source.is_file():
            raise FileNotFoundError(f"Required local CodeFormer face model is missing: {source}")
        if not destination.is_file():
            with source.open("rb") as source_stream:
                with destination.open("xb") as destination_stream:
                    shutil.copyfileobj(source_stream, destination_stream)
    return cache_root


def _load_model(checkpoint: Path):
    if not checkpoint.is_file():
        raise FileNotFoundError(f"CodeFormer checkpoint does not exist: {checkpoint}")
    import torch
    import cv2
    import numpy as np
    from PIL import Image

    local_app_data = Path(
        os.environ.get(
            "LOCALAPPDATA",
            str(Path.home() / "AppData" / "Local"),
        )
    )
    cache_root = local_app_data / "PixelRestore" / "models" / "CodeFormer"
    _prepare_official_architecture(cache_root)
    _install_local_face_models(checkpoint.parent, cache_root)
    from basicsr.utils import img2tensor, tensor2img
    from facexlib.utils.face_restoration_helper import FaceRestoreHelper
    from torchvision.transforms.functional import normalize
    from basicsr.utils.registry import ARCH_REGISTRY

    model = ARCH_REGISTRY.get("CodeFormer")(
        dim_embd=512,
        codebook_size=1024,
        n_head=8,
        n_layers=9,
        connect_list=["32", "64", "128", "256"],
    )
    checkpoint_data = torch.load(
        str(checkpoint),
        map_location="cpu",
        weights_only=True,
    )
    parameters = checkpoint_data.get("params_ema", checkpoint_data.get("params"))
    if parameters is None:
        raise ValueError("CodeFormer checkpoint must contain params_ema or params.")
    model.load_state_dict(parameters, strict=True)
    model.eval()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return {
        "torch": torch,
        "model": model,
        "device": device,
        "cache_root": cache_root,
        "cv2": cv2,
        "np": np,
        "Image": Image,
        "img2tensor": img2tensor,
        "tensor2img": tensor2img,
        "FaceRestoreHelper": FaceRestoreHelper,
        "normalize": normalize,
        "helper": None,
    }


def _process_image(source: Path, destination: Path, fidelity: float, runtime) -> str:
    torch = runtime["torch"]
    model = runtime["model"]
    device = runtime["device"]
    helper = runtime["helper"]
    if not 0.0 <= fidelity <= 1.0:
        raise ValueError(f"Fidelity must be between 0 and 1, got {fidelity}.")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {destination}")
    if not source.is_file():
        raise FileNotFoundError(f"Input image does not exist: {source}")

    try:
        model.to(device)
        if helper is None:
            helper = runtime["FaceRestoreHelper"](
                upscale_factor=1,
                face_size=512,
                crop_ratio=(1, 1),
                det_model="retinaface_mobile0.25",
                save_ext="png",
                use_parse=True,
                device=torch.device("cpu"),
                model_rootpath=str(runtime["cache_root"]),
            )
            runtime["helper"] = helper
        helper.clean_all()
        helper.device = device
        helper.face_det.to(device)
        helper.face_det.device = device
        helper.face_det.mean_tensor = helper.face_det.mean_tensor.to(device)
        helper.face_parse.to(device)

        cv2 = runtime["cv2"]
        np = runtime["np"]
        Image = runtime["Image"]
        image_bgr = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if image_bgr is None:
            raise OSError(f"Could not decode input image: {source}")

        helper.read_image(image_bgr)
        face_count = helper.get_face_landmarks_5(
            only_center_face=False,
            resize=640,
            eye_dist_threshold=5,
        )
        if not face_count:
            raise ValueError("CodeFormer did not detect a face in the input cover.")
        helper.align_warp_face()

        with torch.inference_mode():
            for cropped_face in helper.cropped_faces:
                face_tensor = runtime["img2tensor"](
                    cropped_face.astype(np.float32) / 255.0,
                    bgr2rgb=True,
                    float32=True,
                )
                runtime["normalize"](
                    face_tensor,
                    (0.5, 0.5, 0.5),
                    (0.5, 0.5, 0.5),
                    inplace=True,
                )
                restored = model(
                    face_tensor.unsqueeze(0).to(device),
                    w=fidelity,
                    adain=True,
                )[0]
                restored_face = runtime["tensor2img"](
                    restored.squeeze(0),
                    rgb2bgr=True,
                    min_max=(-1, 1),
                ).astype(np.uint8)
                helper.add_restored_face(restored_face)
                del restored, face_tensor

        helper.get_inverse_affine(None)
        result_bgr = helper.paste_faces_to_input_image(upsample_img=image_bgr)
        if result_bgr is None or result_bgr.shape != image_bgr.shape:
            raise RuntimeError("CodeFormer returned an invalid image size.")
        result_rgb = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            Image.fromarray(result_rgb, "RGB").save(stream, format="PNG", optimize=True)
        return (
            f"CodeFormer restored {face_count} face(s) at fidelity {fidelity:.2f} "
            f"using {'CUDA' if device.type == 'cuda' else 'CPU'}."
        )
    finally:
        if device.type == "cuda":
            if helper is not None:
                helper.clean_all()
                helper.face_det.to("cpu")
                helper.face_det.device = torch.device("cpu")
                helper.face_det.mean_tensor = helper.face_det.mean_tensor.to("cpu")
                helper.face_parse.to("cpu")
                helper.device = torch.device("cpu")
            model.to("cpu")
            torch.cuda.empty_cache()


def _run_persistent(checkpoint: Path, fidelity: float) -> int:
    protocol = sys.stdout
    try:
        with redirect_stdout(sys.stderr):
            runtime = _load_model(checkpoint)
        protocol.write(json.dumps({"ready": True}) + "\n")
        protocol.flush()
    except Exception as exc:
        protocol.write(json.dumps({"ready": False, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
        protocol.flush()
        return 1

    for line in sys.stdin:
        try:
            request = json.loads(line)
            source = Path(request["source"])
            destination = Path(request["destination"])
            with redirect_stdout(sys.stderr):
                message = _process_image(source, destination, fidelity, runtime)
            response = {"ok": True, "message": message}
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        protocol.write(json.dumps(response, ensure_ascii=False) + "\n")
        protocol.flush()
    return 0


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--persistent":
        try:
            fidelity = float(sys.argv[3])
            if not 0.0 <= fidelity <= 1.0:
                raise ValueError(f"Fidelity must be between 0 and 1, got {fidelity}.")
        except ValueError as exc:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        return _run_persistent(Path(sys.argv[2]), fidelity)
    if len(sys.argv) != 5:
        print("Usage: codeformer_runner.py input output checkpoint fidelity", file=sys.stderr)
        return 2

    source, destination, checkpoint = map(Path, sys.argv[1:4])
    try:
        fidelity = float(sys.argv[4])
        if not 0.0 <= fidelity <= 1.0:
            raise ValueError(f"Fidelity must be between 0 and 1, got {fidelity}.")
        runtime = _load_model(checkpoint)
        print(_process_image(source, destination, fidelity, runtime))
        return 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
