"""Run the local 4x NOMOS HAT super-resolution checkpoint."""
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
import traceback


def _load_model(checkpoint: Path):
    import numpy as np
    import torch
    from PIL import Image
    from spandrel import ImageModelDescriptor, ModelLoader

    if not checkpoint.is_file():
        raise FileNotFoundError(f"HAT checkpoint does not exist: {checkpoint}")
    descriptor = ModelLoader().load_from_file(str(checkpoint))
    if not isinstance(descriptor, ImageModelDescriptor):
        raise TypeError("The checkpoint is not an image-to-image model.")
    if descriptor.scale != 4:
        raise ValueError(
            f"Expected a 4x HAT model, but the checkpoint reports scale {descriptor.scale}."
        )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    descriptor.to(device="cpu", dtype=torch.float32).eval()
    return np, torch, Image, descriptor, device


def _process_image(source: Path, destination: Path, runtime) -> str:
    np, torch, Image, descriptor, device = runtime
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {destination}")
    if not source.is_file():
        raise FileNotFoundError(f"Input image does not exist: {source}")

    try:
        descriptor.to(device=device, dtype=torch.float32).eval()
        with Image.open(source) as image:
            rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
            input_tensor = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).to(device)
            expected_size = (image.width * 4, image.height * 4)

        with torch.inference_mode():
            output = descriptor(input_tensor)
        if not isinstance(output, torch.Tensor) or output.ndim != 4 or output.shape[0] != 1:
            raise RuntimeError("HAT returned an invalid output tensor.")
        if tuple(output.shape[-2:]) != (expected_size[1], expected_size[0]):
            raise RuntimeError(
                f"HAT returned {output.shape[-1]}×{output.shape[-2]}, "
                f"expected {expected_size[0]}×{expected_size[1]}."
            )
        if not torch.isfinite(output).all():
            raise RuntimeError("HAT returned non-finite pixel values.")

        pixels = (
            output.squeeze(0)
            .permute(1, 2, 0)
            .clamp(0.0, 1.0)
            .mul(255.0)
            .round()
            .to(torch.uint8)
            .cpu()
            .numpy()
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            Image.fromarray(pixels, "RGB").save(stream, format="PNG", optimize=True)
        return f"NOMOS 8k HAT-L x4 completed using {'CUDA' if device.type == 'cuda' else 'CPU'}."
    finally:
        if device.type == "cuda":
            descriptor.to(device="cpu")
            torch.cuda.empty_cache()


def _run_persistent(checkpoint: Path) -> int:
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
                message = _process_image(source, destination, runtime)
            response = {"ok": True, "message": message}
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        protocol.write(json.dumps(response, ensure_ascii=False) + "\n")
        protocol.flush()
    return 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--persistent":
        return _run_persistent(Path(sys.argv[2]))
    if len(sys.argv) != 4:
        print("Usage: hat_runner.py input output checkpoint", file=sys.stderr)
        return 2

    source, destination, checkpoint = map(Path, sys.argv[1:])
    try:
        runtime = _load_model(checkpoint)
        print(_process_image(source, destination, runtime))
        return 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
