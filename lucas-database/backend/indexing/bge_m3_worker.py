from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


MODEL_NAME = "BAAI/bge-m3"
REQUIRED_MODEL_FILES = ("config.json", "config_sentence_transformers.json", "modules.json", "pytorch_model.bin")

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

_model: Any | None = None
_model_dir: Path | None = None
_torch_info: dict[str, Any] | None = None
_model_load_elapsed_ms: int | None = None


def detect_torch() -> dict[str, Any]:
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        return {
            "torch_version": getattr(torch, "__version__", None),
            "torch_cuda_version": getattr(torch.version, "cuda", None),
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


def get_model(model_name: str):
    global _model, _model_dir, _torch_info, _model_load_elapsed_ms

    if _model is not None:
        return _model

    from sentence_transformers import SentenceTransformer

    _torch_info = detect_torch()
    device = "cuda" if _torch_info.get("cuda_available") else "cpu"
    _model_dir = resolve_model_dir(model_name)
    started = time.perf_counter()
    _model = SentenceTransformer(str(_model_dir), device=device)
    _model_load_elapsed_ms = round((time.perf_counter() - started) * 1000)
    return _model


def handle_embed(request: dict[str, Any]) -> dict[str, Any]:
    request_id = str(request.get("id") or "")
    model_name = str(request.get("model") or MODEL_NAME)
    texts = request.get("texts")
    batch_size = int(request.get("batch_size") or 12)

    if not request_id:
        raise ValueError("request id is required")
    if not isinstance(texts, list):
        raise ValueError("texts must be an array")

    normalized_texts = [str(text) for text in texts]
    started = time.perf_counter()
    model = get_model(model_name)
    embeddings = model.encode(
        normalized_texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    vectors = [embedding.tolist() if hasattr(embedding, "tolist") else list(embedding) for embedding in embeddings]
    embedding_dim = len(vectors[0]) if vectors else 0

    return {
        "id": request_id,
        "ok": True,
        "model": model_name,
        "model_dir": str(_model_dir) if _model_dir else None,
        "embedding_dim": embedding_dim,
        "count": len(vectors),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "model_load_elapsed_ms": _model_load_elapsed_ms,
        "torch": _torch_info,
        "embeddings": vectors,
    }


def respond(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def respond_error(request_id: str | None, error: Exception) -> None:
    respond(
        {
            "id": request_id,
            "ok": False,
            "error": {
                "type": error.__class__.__name__,
                "message": str(error),
            },
            "torch": detect_torch(),
        }
    )


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        request_id: str | None = None
        try:
            request = json.loads(line)
            request_id = str(request.get("id") or "")
            request_type = request.get("type")
            if request_type == "embed":
                respond(handle_embed(request))
            elif request_type == "shutdown":
                respond({"id": request_id, "ok": True, "shutdown": True})
                return 0
            else:
                raise ValueError(f"unsupported request type: {request_type!r}")
        except Exception as exc:
            respond_error(request_id, exc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
