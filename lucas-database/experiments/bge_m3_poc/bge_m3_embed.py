from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any


MODEL_NAME = "BAAI/bge-m3"
REQUIRED_MODEL_FILES = ("config.json", "config_sentence_transformers.json", "modules.json", "pytorch_model.bin")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run BGE-M3 embeddings for a JSON text list.")
    parser.add_argument("--input", required=True, help="Input JSON file with a texts array.")
    parser.add_argument("--output", required=True, help="Output JSON file.")
    parser.add_argument("--model", default=MODEL_NAME, help="Hugging Face model name.")
    parser.add_argument("--batch-size", type=int, default=12, help="Batch size for encoding.")
    parser.add_argument("--preview-dim", type=int, default=8, help="How many vector values to store.")
    return parser.parse_args()


def load_texts(input_path: Path) -> list[str]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    texts = payload.get("texts")
    if not isinstance(texts, list) or not texts:
        raise ValueError("input JSON must contain a non-empty texts array")
    normalized = [str(text) for text in texts if str(text).strip()]
    if not normalized:
        raise ValueError("texts array does not contain any non-empty strings")
    return normalized


def detect_torch() -> dict[str, Any]:
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        return {
            "torch_version": getattr(torch, "__version__", None),
            "cuda_available": cuda_available,
            "cuda_device_count": torch.cuda.device_count() if cuda_available else 0,
            "cuda_device_name": torch.cuda.get_device_name(0) if cuda_available else None,
            "device": "cuda" if cuda_available else "cpu",
        }
    except Exception as exc:  # pragma: no cover - diagnostics path
        return {
            "torch_error": repr(exc),
            "cuda_available": False,
            "device": "unknown",
        }


def _unique_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = str(path)
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def resolve_model_dir(model_name: str) -> Path:
    requested_path = Path(model_name).expanduser()
    if requested_path.exists():
        return requested_path.resolve()

    override = os.getenv("BGE_M3_MODEL_DIR")
    if override:
        override_path = Path(override).expanduser()
        if override_path.exists():
            return override_path.resolve()

    cache_roots: list[Path] = []
    hf_hub_cache = os.getenv("HF_HUB_CACHE") or os.getenv("HUGGINGFACE_HUB_CACHE")
    if hf_hub_cache:
        cache_roots.append(Path(hf_hub_cache).expanduser())
    hf_home = os.getenv("HF_HOME")
    if hf_home:
        cache_roots.append(Path(hf_home).expanduser() / "hub")
    cache_roots.append(Path.home() / ".cache" / "huggingface" / "hub")

    cache_roots = _unique_paths(cache_roots)
    snapshot_root_name = f"models--{model_name.replace('/', '--')}"
    candidates: list[Path] = []

    for cache_root in cache_roots:
        snapshot_root = cache_root / snapshot_root_name / "snapshots"
        if not snapshot_root.is_dir():
            continue
        for snapshot_dir in snapshot_root.iterdir():
            if snapshot_dir.is_dir() and all((snapshot_dir / name).exists() for name in REQUIRED_MODEL_FILES):
                candidates.append(snapshot_dir)

    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime).resolve()

    searched = [str(root / snapshot_root_name / "snapshots") for root in cache_roots]
    raise FileNotFoundError(
        f"Could not find a local snapshot for {model_name!r}. "
        f"Set BGE_M3_MODEL_DIR or place the model under one of: {searched}"
    )


def load_model(model_name: str):
    from sentence_transformers import SentenceTransformer

    torch_info = detect_torch()
    device = "cuda" if torch_info.get("cuda_available") else "cpu"
    model_dir = resolve_model_dir(model_name)
    started = time.perf_counter()
    model = SentenceTransformer(str(model_dir), device=device)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return model, elapsed_ms, torch_info, model_dir


def encode_dense(model: Any, texts: list[str], batch_size: int):
    return model.encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )


def vector_to_list(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        return vector.tolist()
    return list(vector)


def write_failure(output_path: Path, started: float, error: Exception) -> None:
    output = {
        "ok": False,
        "model": MODEL_NAME,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "error": {
            "type": error.__class__.__name__,
            "message": str(error),
        },
        "python": sys.version,
        "platform": platform.platform(),
        "torch": detect_torch(),
    }
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        texts = load_texts(Path(args.input))
        model, load_elapsed_ms, torch_info, model_dir = load_model(args.model)
        encode_started = time.perf_counter()
        embeddings = encode_dense(model, texts, args.batch_size)
        encode_elapsed_ms = round((time.perf_counter() - encode_started) * 1000)
        vectors = [vector_to_list(vector) for vector in embeddings]
        dims = [len(vector) for vector in vectors]
        unique_dims = sorted(set(dims))
        if len(unique_dims) != 1:
            raise RuntimeError(f"embedding dimensions are not stable: {unique_dims}")

        output = {
            "ok": True,
            "model": args.model,
            "model_dir": str(model_dir),
            "embedding_backend": "sentence-transformers",
            "embedding_dim": unique_dims[0],
            "count": len(texts),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "model_load_elapsed_ms": load_elapsed_ms,
            "encode_elapsed_ms": encode_elapsed_ms,
            "batch_size": args.batch_size,
            "python": sys.version,
            "platform": platform.platform(),
            "torch": torch_info,
            "items": [
                {
                    "index": index,
                    "text_preview": text[:80],
                    "embedding_dim": dims[index],
                    "embedding_preview": [round(float(value), 6) for value in vectors[index][: args.preview_dim]],
                }
                for index, text in enumerate(texts)
            ],
        }
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: output[key] for key in ["ok", "model", "embedding_dim", "count", "elapsed_ms"]}, ensure_ascii=False))
        return 0
    except Exception as exc:  # pragma: no cover - runtime diagnostics path
        write_failure(output_path, started, exc)
        print(f"BGE-M3 embedding failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
