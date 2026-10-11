"""Run the local NOMOS HAT super-resolution checkpoint."""
from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: hat_runner.py input output checkpoint", file=sys.stderr)
        return 2

    source, destination, checkpoint = map(Path, sys.argv[1:])
    try:
        import numpy as np
        import torch
        from PIL import Image
        from spandrel import ImageModelDescriptor, ModelLoader

        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite existing output: {destination}")
        if not source.is_file():
            raise FileNotFoundError(f"Input image does not exist: {source}")
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
        print(f"NOMOS 8k HAT-L x4 completed using {'CUDA' if device.type == 'cuda' else 'CPU'}.")
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
