from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


MODEL_NAME = "BAAI/bge-m3"
REQUIRED_MODEL_FILES = ("config.json", "config_sentence_transformers.json", "modules.json", "pytorch_model.bin")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark BGE-M3 local embedding throughput.")
    parser.add_argument("--input", required=True, help="Input JSON file with sample texts.")
    parser.add_argument("--output", required=True, help="Output benchmark JSON file.")
    parser.add_argument("--model", default=MODEL_NAME, help="Hugging Face model name.")
    parser.add_argument("--batch-size", type=int, default=12, help="Batch size for encoding.")
    return parser.parse_args()


def load_base_texts(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    texts = payload.get("texts")
    if not isinstance(texts, list) or not texts:
        raise ValueError("input JSON must contain a non-empty texts array")
    return [str(text) for text in texts if str(text).strip()]


def expand_texts(base_texts: list[str], count: int) -> list[str]:
    result: list[str] = []
    index = 0
    while len(result) < count:
        source = base_texts[index % len(base_texts)]
        result.append(f"{source}\n\n[benchmark_item={len(result)}]")
        index += 1
    return result


def get_memory_mb() -> float | None:
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
    except Exception:
        return None


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
    except Exception as exc:
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
    return model, round((time.perf_counter() - started) * 1000), torch_info, model_dir


def encode_dense(model: Any, texts: list[str], batch_size: int):
    return model.encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )


def vector_dim(vector: Any) -> int:
    if hasattr(vector, "shape") and len(vector.shape) > 0:
        return int(vector.shape[-1])
    if hasattr(vector, "tolist"):
        return len(vector.tolist())
    return len(list(vector))


def package_version(package_name: str) -> str | None:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", package_name],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in result.stdout.splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        return None
    return None


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    try:
        base_texts = load_base_texts(Path(args.input))
        model, model_load_elapsed_ms, torch_info, model_dir = load_model(args.model)
        benchmark_results = []
        embedding_dim = None

        for count in [1, 10, 100]:
            texts = expand_texts(base_texts, count)
            memory_before_mb = get_memory_mb()
            encode_started = time.perf_counter()
            embeddings = encode_dense(model, texts, args.batch_size)
            elapsed_ms = round((time.perf_counter() - encode_started) * 1000)
            memory_after_mb = get_memory_mb()
            dims = [vector_dim(vector) for vector in embeddings]
            unique_dims = sorted(set(dims))
            if len(unique_dims) != 1:
                raise RuntimeError(f"embedding dimensions are not stable for count={count}: {unique_dims}")
            embedding_dim = unique_dims[0]
            benchmark_results.append(
                {
                    "count": count,
                    "elapsed_ms": elapsed_ms,
                    "avg_ms_per_text": round(elapsed_ms / count, 2),
                    "embedding_dim": embedding_dim,
                    "memory_before_mb": memory_before_mb,
                    "memory_after_mb": memory_after_mb,
                }
            )
            print(f"count={count} elapsed_ms={elapsed_ms} dim={embedding_dim}")

        output = {
            "ok": True,
            "model": args.model,
            "model_dir": str(model_dir),
            "embedding_backend": "sentence-transformers",
            "embedding_dim": embedding_dim,
            "batch_size": args.batch_size,
            "total_elapsed_ms": round((time.perf_counter() - started) * 1000),
            "model_load_elapsed_ms": model_load_elapsed_ms,
            "python": sys.version,
            "platform": platform.platform(),
            "torch": torch_info,
            "dependencies": {
                "torch": package_version("torch"),
                "sentence-transformers": package_version("sentence-transformers"),
                "psutil": package_version("psutil"),
            },
            "benchmarks": benchmark_results,
        }
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    except Exception as exc:
        output = {
            "ok": False,
            "model": args.model,
            "total_elapsed_ms": round((time.perf_counter() - started) * 1000),
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
            "python": sys.version,
            "platform": platform.platform(),
            "torch": detect_torch(),
        }
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"BGE-M3 benchmark failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
