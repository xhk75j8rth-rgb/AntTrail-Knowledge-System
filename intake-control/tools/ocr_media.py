#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import uuid
import sys
from pathlib import Path
from typing import Any


RAPIDOCR_PYTHON = Path(r"C:\Users\pppppqr\tools\rapidocr-venv\Scripts\python.exe")
OCR_MATERIAL_SCHEMA_NAME = "OCRMaterialV1"
OCR_MATERIAL_SCHEMA_VERSION = "1"


def write_json(job_dir: Path, name: str, payload: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_outputs(job_dir: Path, result: dict[str, Any], material: dict[str, Any]) -> None:
    write_json(job_dir, "ocr.json", result)
    write_json(job_dir, "ocr_material.json", material)


def base_result(input_path: str, temp_dir: Path | None, frames_dir: Path | None, should_run: bool, force_ocr: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "used_mcp": False,
        "stage": "ocr",
        "should_run_ocr": should_run,
        "force_ocr": force_ocr,
        "input_path": input_path,
        "temp_dir": str(temp_dir) if temp_dir else "",
        "frames_dir": str(frames_dir) if frames_dir else "",
        "frame_count": 0,
        "text_items": [],
        "merged_text": "",
        "confidence": "low",
        "status": "not_started",
        "error": None,
        "ocr_material_path": "",
    }


def empty_sampling(max_frames: int, timeout_sec: int, frames_dir: Path | None) -> dict[str, Any]:
    return {
        "strategy": "not_sampled",
        "reason": "",
        "max_frames": max(1, max_frames),
        "timeout_sec": timeout_sec,
        "duration_sec": None,
        "ffmpeg_filter": "",
        "frames_dir": str(frames_dir) if frames_dir else "",
        "frame_count": 0,
        "frames": [],
    }


def build_material(
    *,
    job_dir: Path,
    result: dict[str, Any],
    input_type: str,
    max_frames: int,
    timeout_sec: int,
    sampling: dict[str, Any],
    raw_text_item_count: int = 0,
) -> dict[str, Any]:
    text_items = result.get("text_items") or []
    merged_text = result.get("merged_text") or ""
    return {
        "schema_name": OCR_MATERIAL_SCHEMA_NAME,
        "schema_version": OCR_MATERIAL_SCHEMA_VERSION,
        "job_id": job_dir.name,
        "ok": bool(result.get("ok")),
        "used_mcp": False,
        "stage": "ocr",
        "status": result.get("status") or "not_started",
        "error": result.get("error"),
        "source": {
            "input_type": input_type,
            "input_path": result.get("input_path") or "",
            "input_exists": bool(result.get("input_path")) and Path(str(result.get("input_path"))).exists(),
        },
        "policy": {
            "should_run_ocr": bool(result.get("should_run_ocr")),
            "force_ocr": bool(result.get("force_ocr")),
            "max_frames": max(1, max_frames),
            "timeout_sec": timeout_sec,
        },
        "sampling": sampling,
        "dedupe": {
            "strategy": "normalized_exact_text_first_seen",
            "normalization": "collapse whitespace, casefold ASCII, remove common punctuation",
            "keeps": "first OCR item for each normalized text key",
            "raw_text_item_count": raw_text_item_count,
            "deduped_text_item_count": len(text_items),
        },
        "evidence_items": text_items,
        "merged_text": merged_text,
        "confidence": result.get("confidence") or "low",
    }


def material_path(job_dir: Path) -> str:
    return str(job_dir / "ocr_material.json")


def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)


def get_duration(path: Path, timeout: int) -> float | None:
    completed = run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ], timeout)
    if completed.returncode != 0:
        return None
    try:
        return float(completed.stdout.strip())
    except ValueError:
        return None


def frame_timestamp(index: int, frame_count: int, duration: float | None) -> float | None:
    if not duration or frame_count <= 0:
        return None
    if frame_count == 1:
        return round(duration / 2, 3)
    return round((duration * index) / max(1, frame_count - 1), 3)


def extract_frames(video: Path, frames_dir: Path, max_frames: int, timeout: int) -> tuple[bool, dict[str, Any], str]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    duration = get_duration(video, timeout) or 0
    if duration > 0:
        fps = max_frames / duration
        vf = f"fps={fps:.6f}"
        strategy = "uniform_by_duration"
        reason = "sample up to max_frames evenly across the detected duration"
    else:
        vf = f"fps=1,select='lte(n\\,{max_frames - 1})'"
        strategy = "first_n_seconds_fallback"
        reason = "duration unavailable; sample one frame per second and cap at max_frames"
    output_pattern = str(frames_dir / "frame_%03d.jpg")
    completed = run([
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        "-vf",
        vf,
        "-frames:v",
        str(max_frames),
        output_pattern,
    ], timeout)
    images = sorted(frames_dir.glob("*.jpg"))
    sampling = {
        "strategy": strategy,
        "reason": reason,
        "max_frames": max_frames,
        "timeout_sec": timeout,
        "duration_sec": round(duration, 3) if duration > 0 else None,
        "ffmpeg_filter": vf,
        "frames_dir": str(frames_dir),
        "frame_count": len(images),
        "frames": [
            {
                "frame_index": index,
                "path": str(path),
                "timestamp_sec": frame_timestamp(index, len(images), duration if duration > 0 else None),
            }
            for index, path in enumerate(images)
        ],
    }
    return completed.returncode == 0, sampling, (completed.stderr or completed.stdout or "").strip()


def ocr_with_rapidocr(images: list[Path], timeout: int) -> tuple[bool, list[dict[str, Any]], str]:
    script = (
        "import json,sys;"
        "from rapidocr import RapidOCR;"
        "engine=RapidOCR();"
        "items=[];"
        "\nfor p in sys.argv[1:]:\n"
        "    r=engine(p)\n"
        "    texts=getattr(r,'txts',None) or []\n"
        "    scores=getattr(r,'scores',None) or []\n"
        "    for i,t in enumerate(texts):\n"
        "        items.append({'frame':p,'text':str(t),'score':float(scores[i]) if i < len(scores) and scores[i] is not None else None})\n"
        "print(json.dumps(items,ensure_ascii=False))"
    )
    completed = run([str(RAPIDOCR_PYTHON), "-c", script, *[str(p) for p in images]], timeout)
    if completed.returncode != 0:
        return False, [], completed.stderr or completed.stdout
    try:
        return True, json.loads(completed.stdout.strip() or "[]"), completed.stderr
    except json.JSONDecodeError as exc:
        return False, [], f"Invalid RapidOCR JSON: {exc}; stdout={completed.stdout[-500:]}"


def normalized_dedupe_key(text: str) -> str:
    text = " ".join(str(text or "").split()).casefold()
    return re.sub(r"[\s,，.。!！?？:：;；、_\-—]+", "", text)


def frame_index_from_path(path: str) -> int | None:
    match = re.search(r"frame_(\d+)", Path(path).stem)
    if not match:
        return None
    return max(0, int(match.group(1)) - 1)


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        text = " ".join(str(item.get("text", "")).split())
        key = normalized_dedupe_key(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        frame = str(item.get("frame") or "")
        deduped.append({
            "text": text,
            "frame": frame,
            "frame_path": frame,
            "frame_index": frame_index_from_path(frame),
            "score": item.get("score"),
        })
    return deduped


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract frames and OCR media without MCP.")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--video")
    parser.add_argument("--image")
    parser.add_argument("--should-run-ocr", action="store_true")
    parser.add_argument("--force-ocr", action="store_true")
    parser.add_argument("--max-frames", type=int, default=10)
    parser.add_argument("--timeout-sec", type=int, default=180)
    args = parser.parse_args()

    job_dir = Path(args.job_dir).resolve()
    input_path = args.video or args.image or ""
    should_run = bool(args.should_run_ocr or args.force_ocr)
    temp_root = Path(os.environ.get("TEMP") or os.environ.get("TMP") or str(job_dir))
    temp_dir = temp_root / "LucasVideoOCR" / f"{job_dir.name}-{uuid.uuid4().hex[:6]}" if input_path else None
    frames_dir = temp_dir / "frames" if temp_dir else None
    result = base_result(input_path, temp_dir, frames_dir, should_run, args.force_ocr)
    result["ocr_material_path"] = material_path(job_dir)
    input_type = "video" if args.video else ("image" if args.image else "none")
    sampling = empty_sampling(args.max_frames, args.timeout_sec, frames_dir)

    if not should_run:
        result.update({"ok": True, "status": "skipped_not_needed"})
        sampling["reason"] = "conditional OCR policy did not request OCR"
        write_outputs(job_dir, result, build_material(
            job_dir=job_dir,
            result=result,
            input_type=input_type,
            max_frames=args.max_frames,
            timeout_sec=args.timeout_sec,
            sampling=sampling,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not input_path:
        result.update({"ok": True, "status": "skipped_no_video"})
        sampling["reason"] = "OCR was requested but no local video or image path was available"
        write_outputs(job_dir, result, build_material(
            job_dir=job_dir,
            result=result,
            input_type=input_type,
            max_frames=args.max_frames,
            timeout_sec=args.timeout_sec,
            sampling=sampling,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not RAPIDOCR_PYTHON.exists():
        result.update({"ok": False, "status": "ocr_unavailable", "error": f"RapidOCR python not found: {RAPIDOCR_PYTHON}"})
        sampling["reason"] = "RapidOCR runtime is unavailable"
        write_outputs(job_dir, result, build_material(
            job_dir=job_dir,
            result=result,
            input_type=input_type,
            max_frames=args.max_frames,
            timeout_sec=args.timeout_sec,
            sampling=sampling,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    path = Path(input_path).resolve()
    if not path.exists():
        result.update({"ok": False, "status": "ocr_input_missing", "error": f"Input not found: {path}"})
        sampling["reason"] = "configured OCR input path does not exist"
        write_outputs(job_dir, result, build_material(
            job_dir=job_dir,
            result=result,
            input_type=input_type,
            max_frames=args.max_frames,
            timeout_sec=args.timeout_sec,
            sampling=sampling,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    images: list[Path]
    if args.video:
        ok, sampling, detail = extract_frames(path, frames_dir or (job_dir / "ocr_frames"), max(1, args.max_frames), args.timeout_sec)
        if not ok:
            result.update({"ok": False, "status": "frame_extract_failed", "error": detail[-1000:]})
            write_outputs(job_dir, result, build_material(
                job_dir=job_dir,
                result=result,
                input_type=input_type,
                max_frames=args.max_frames,
                timeout_sec=args.timeout_sec,
                sampling=sampling,
            ))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
        images = sorted((frames_dir or (job_dir / "ocr_frames")).glob("*.jpg"))
    else:
        images = [path]
        sampling = {
            "strategy": "single_image",
            "reason": "OCR input is a single image; no frame extraction needed",
            "max_frames": 1,
            "timeout_sec": args.timeout_sec,
            "duration_sec": None,
            "ffmpeg_filter": "",
            "frames_dir": "",
            "frame_count": 1,
            "frames": [{"frame_index": 0, "path": str(path), "timestamp_sec": None}],
        }

    result["frame_count"] = len(images)
    if not images:
        result.update({"ok": True, "status": "ocr_done", "merged_text": "", "confidence": "low"})
        sampling["reason"] = "frame extraction completed but produced no image frames"
        write_outputs(job_dir, result, build_material(
            job_dir=job_dir,
            result=result,
            input_type=input_type,
            max_frames=args.max_frames,
            timeout_sec=args.timeout_sec,
            sampling=sampling,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    ok, items, detail = ocr_with_rapidocr(images, args.timeout_sec)
    if not ok:
        result.update({"ok": False, "status": "ocr_failed", "error": detail[-1000:]})
        write_outputs(job_dir, result, build_material(
            job_dir=job_dir,
            result=result,
            input_type=input_type,
            max_frames=args.max_frames,
            timeout_sec=args.timeout_sec,
            sampling=sampling,
            raw_text_item_count=len(items),
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    text_items = dedupe_items(items)
    merged = "\n".join(item["text"] for item in text_items)
    result.update({
        "ok": True,
        "status": "ocr_done",
        "text_items": text_items,
        "merged_text": merged,
        "confidence": "medium" if merged else "low",
        "rapidocr_stderr_tail": detail[-1000:] if detail else "",
    })
    write_outputs(job_dir, result, build_material(
        job_dir=job_dir,
        result=result,
        input_type=input_type,
        max_frames=args.max_frames,
        timeout_sec=args.timeout_sec,
        sampling=sampling,
        raw_text_item_count=len(items),
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
