#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRANSCRIBE_SCRIPT = PROJECT_ROOT / "scripts" / "transcribe_douyin_once.ps1"
DYT_EXE = Path(os.environ.get("LUCAS_DYT_EXE") or os.environ.get("DYT_EXE") or "dyt")
WHISPER_CLI = Path(os.environ.get("LUCAS_WHISPER_CLI") or os.environ.get("WHISPER_CLI") or "whisper-cli")
WHISPER_DIR = WHISPER_CLI.parent
MODEL_PATH = Path(
    os.environ.get("LUCAS_WHISPER_MODEL_PATH")
    or os.environ.get("WHISPER_MODEL_PATH")
    or "__missing_whisper_model__"
)
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
MAX_VIDEO_FALLBACK_BYTES = 80 * 1024 * 1024
MAX_AUDIO_FALLBACK_BYTES = 80 * 1024 * 1024


def tail(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text[-limit:]


def write_json(job_dir: Path, name: str, payload: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_text(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )


def command_available(command: Path) -> bool:
    return command.exists() or shutil.which(str(command)) is not None


def is_music_only(transcript: str) -> bool:
    compact = re.sub(r"\s+", "", transcript or "").casefold()
    compact = compact.replace("（", "(").replace("）", ")")
    if not compact:
        return True
    music_tokens = {"(音樂)", "(音乐)", "[音樂]", "[音乐]", "(music)", "[music]", "音樂", "音乐", "music"}
    return compact in music_tokens


def speech_fallback_policy() -> dict[str, Any]:
    return {
        "name": "douyin_speech_degradation_v1",
        "primary": "dyt_local_transcribe",
        "fallback_order": [
            "video_embedded_audio_local_whisper",
            "douyin_page_audio_source_local_whisper",
        ],
        "trigger_reasons": [
            "dyt_exit_nonzero",
            "dyt_empty_transcript",
            "dyt_music_only_transcript",
        ],
    }


def dyt_fallback_reason(exit_code: int, transcript: str) -> str:
    if exit_code != 0:
        return "dyt_exit_nonzero"
    if not (transcript or "").strip():
        return "dyt_empty_transcript"
    if is_music_only(transcript):
        return "dyt_music_only_transcript"
    return ""


def local_fallback_stage(result: dict[str, Any]) -> str:
    if not result:
        return ""
    if result.get("source"):
        return str(result.get("source"))
    if result.get("audio_whisper_attempt"):
        return "douyin_page_audio_source"
    if result.get("audio_source_attempts"):
        return "douyin_page_audio_source"
    if result.get("video_audio_attempt"):
        return "video_embedded_audio"
    return "local_whisper_fallback"


def clean_transcript(raw: str) -> str:
    keep: list[str] = []
    noise_prefixes = (
        "read_audio_data:",
        "whisper_",
        "main:",
        "system_info:",
        "load_backend:",
    )
    for line in (raw or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(stripped.startswith(prefix) for prefix in noise_prefixes):
            continue
        keep.append(stripped)
    return "\n".join(keep).strip()


def parse_stderr_paths(stderr: str) -> tuple[str, str]:
    transcript_path = ""
    run_dir = ""
    for line in (stderr or "").splitlines():
        if "Transcript temp path:" in line:
            transcript_path = line.split("Transcript temp path:", 1)[1].strip()
        if "Run temp dir:" in line:
            run_dir = line.split("Run temp dir:", 1)[1].strip()
    return transcript_path, run_dir


def parse_dyt_output(text: str) -> dict[str, str]:
    info = {"resolved_url": "", "audio_path": "", "video_path": "", "transcript_path": ""}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("Resolved:"):
            info["resolved_url"] = stripped.split("Resolved:", 1)[1].strip()
        elif stripped.startswith("Audio downloaded:"):
            info["audio_path"] = stripped.split("Audio downloaded:", 1)[1].strip()
        elif stripped.startswith("Video downloaded:"):
            info["video_path"] = stripped.split("Video downloaded:", 1)[1].strip()
        elif stripped.startswith("Transcript saved to:"):
            info["transcript_path"] = stripped.split("Transcript saved to:", 1)[1].strip()
        else:
            match = re.search(r"([A-Za-z]:\\[^\r\n\"<>|]+?\.(?:mp4|mov|m4v|webm|mkv))", stripped, flags=re.I)
            if match and not info["video_path"]:
                info["video_path"] = match.group(1).strip()
    return info


def video_id_from_url(url: str) -> str:
    match = re.search(r"/video/(\d+)", url or "")
    return match.group(1) if match else ""


def douyin_candidates(input_url: str, job_dir: Path) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(label: str, url: str) -> None:
        if url and url not in seen:
            seen.add(url)
            candidates.append({"label": label, "url": url, "video_id": video_id_from_url(url)})

    add("original_url", input_url)
    content_path = job_dir / "content.json"
    if content_path.exists():
        try:
            content = json.loads(content_path.read_text(encoding="utf-8"))
            final_url = str(content.get("final_url") or "")
            add("final_url", final_url)
            vid = video_id_from_url(final_url)
            if vid:
                add("video_id_url", f"https://www.douyin.com/video/{vid}")
        except Exception:
            pass
    return candidates


def classify_transcribe_failure(stderr: str, stdout: str) -> str:
    text = f"{stderr}\n{stdout}".casefold()
    if "not found" in text and "dyt" in text:
        return "dyt_missing"
    if "not found" in text and "whisper" in text:
        return "whisper_cli_missing"
    if "model" in text and "not found" in text:
        return "model_missing"
    if "ffmpeg not found" in text:
        return "ffmpeg_unavailable"
    if "resolving url" in text and "download" not in text and "transcript file" not in text:
        return "url_resolution_failed"
    if "not a valid win32 application" in text or "main.cpl" in text:
        return "whisper_executable_resolution_failed"
    if "download" in text or "audio" in text or "video" in text:
        return "media_fetch_failed"
    return "whisper_failed"


def base_result(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ok": False,
        "used_mcp": False,
        "stage": "transcribe",
        "dry_run_ok": False,
        "source_type": "douyin_video" if args.url and "douyin" in args.url.casefold() else "media",
        "url": args.url or "",
        "temp_dir": "",
        "video_path": args.video_path or "",
        "audio_path": "",
        "transcript_path": "",
        "transcript": "",
        "has_speech": False,
        "status": "not_started",
        "confidence": "low",
        "failed_reason": None,
        "dependency_check": {
            "script_exists": TRANSCRIBE_SCRIPT.exists(),
            "dyt_exists": command_available(DYT_EXE),
            "whisper_cli_exists": command_available(WHISPER_CLI),
            "model_exists": MODEL_PATH.exists(),
            "ffmpeg_available": False,
        },
        "resolver_attempts": [],
        "transcribe_source": "",
        "fallback_policy": speech_fallback_policy(),
        "fallback_triggered": False,
        "fallback_reason": "",
        "fallback_ok": False,
        "fallback_stage": "",
        "local_whisper_fallback": {},
        "dyt_command_sanitized": "",
        "dyt_exit_code": None,
        "dyt_stdout_tail": "",
        "dyt_stderr_tail": "",
        "resolved_url_used": "",
        "video_id": "",
        "error": None,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def run_powershell(url: str, timeout_sec: int, dry_run: bool) -> subprocess.CompletedProcess[str]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(TRANSCRIBE_SCRIPT),
        "-Url",
        url,
    ]
    if dry_run:
        cmd.append("-DryRun")
    env = build_tool_env()
    return run_text(cmd, cwd=str(PROJECT_ROOT), capture_output=True, timeout=timeout_sec, env=env)


def build_tool_env() -> dict[str, str]:
    env = os.environ.copy()
    path_key = next((key for key in env.keys() if key.upper() == "PATH"), "PATH")
    existing_path = env.get(path_key, "")
    env[path_key] = f"{WHISPER_DIR};{existing_path}"
    pathext_key = next((key for key in env.keys() if key.upper() == "PATHEXT"), "PATHEXT")
    env[pathext_key] = ".EXE;.COM;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.PY;.PYW;.CPL"
    return env


def sanitized_command(url: str, transcript_path: Path) -> str:
    return f"{DYT_EXE} {url} --local --language zh --model-path {MODEL_PATH} --output {transcript_path}"


def sanitized_local_whisper_command(audio_path: Path, output_base: Path) -> str:
    return f"{WHISPER_CLI} -m {MODEL_PATH} -f {audio_path} -l zh -otxt -of {output_base} -nt"


def find_video_file(run_dir: Path, info: dict[str, str]) -> str:
    explicit = str(info.get("video_path") or "").strip().strip('"')
    if explicit and Path(explicit).exists():
        return str(Path(explicit).resolve())
    candidates = [
        path for path in run_dir.rglob("*")
        if path.is_file() and path.suffix.casefold() in VIDEO_SUFFIXES
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return str(candidates[0].resolve())


def safe_video_source(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    text = url.casefold()
    if any(marker in text for marker in (".js", ".css", "media-audio", "mime_type=audio", "mp4a")):
        return False
    if "douyin-pc-web" in text or "xgplayer" in text or "/libs/" in text:
        return False
    return (
        ".mp4" in text
        or ".m3u8" in text
        or "mime_type=video_mp4" in text
        or "media-video" in text
        or "aweme/v1/play" in text
        or "playwm" in text
        or "play_addr" in text
    )


def safe_audio_source(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    text = url.casefold()
    if any(marker in text for marker in (".js", ".css", "douyin-pc-web", "xgplayer", "/libs/")):
        return False
    return (
        ".m4a" in text
        or ".mp3" in text
        or ".aac" in text
        or ".wav" in text
        or ".ogg" in text
        or "media-audio" in text
        or "mime_type=audio" in text
        or "audio_mp4" in text
        or "mp4a" in text
    )


def video_source_rank(url: str) -> int:
    text = url.casefold()
    if "media-video" in text or "mime_type=video_mp4" in text:
        return 0
    if ".m3u8" in text:
        return 1
    if ".mp4" in text:
        return 2
    return 3


def load_douyin_sources(job_dir: Path, key: str, predicate: Any) -> list[str]:
    path = job_dir / "douyin_page_playwright.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    sources = payload.get(key) or []
    if not isinstance(sources, list):
        return []
    deduped: list[str] = []
    seen: set[str] = set()
    for item in sources:
        source = str(item or "").strip()
        if source and source not in seen and predicate(source):
            seen.add(source)
            deduped.append(source)
    return deduped


def load_douyin_video_sources(job_dir: Path) -> list[str]:
    deduped = load_douyin_sources(job_dir, "video_sources", safe_video_source)
    deduped.sort(key=video_source_rank)
    return deduped


def load_douyin_audio_sources(job_dir: Path) -> list[str]:
    return load_douyin_sources(job_dir, "audio_sources", safe_audio_source)


def fetch_douyin_video_sources(url: str, job_dir: Path, timeout_sec: int) -> list[str]:
    try:
        fetch_timeout = min(max(20, timeout_sec), 120)
        run_text([
            sys.executable,
            str(PROJECT_ROOT / "tools" / "fetch_douyin_page_playwright.py"),
            "--job-dir",
            str(job_dir),
            "--url",
            url,
            "--timeout-sec",
            str(fetch_timeout),
        ], cwd=str(PROJECT_ROOT), capture_output=True, timeout=fetch_timeout + 30)
    except Exception:
        return []
    return load_douyin_video_sources(job_dir)


def fetch_douyin_audio_sources(url: str, job_dir: Path, timeout_sec: int) -> list[str]:
    try:
        fetch_timeout = min(max(20, timeout_sec), 120)
        run_text([
            sys.executable,
            str(PROJECT_ROOT / "tools" / "fetch_douyin_page_playwright.py"),
            "--job-dir",
            str(job_dir),
            "--url",
            url,
            "--timeout-sec",
            str(fetch_timeout),
        ], cwd=str(PROJECT_ROOT), capture_output=True, timeout=fetch_timeout + 30)
    except Exception:
        return []
    return load_douyin_audio_sources(job_dir)


def download_url_to_file(url: str, path: Path, timeout_sec: int, max_bytes: int = MAX_VIDEO_FALLBACK_BYTES) -> tuple[bool, str, int]:
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
        "Referer": "https://www.douyin.com/",
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            content_type = str(response.headers.get("Content-Type") or "").casefold()
            if "html" in content_type:
                return False, f"unexpected content type: {content_type}", total
            with path.open("wb") as fh:
                while True:
                    chunk = response.read(1024 * 512)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        return False, f"video exceeds max fallback size {max_bytes} bytes", total
                    fh.write(chunk)
        if path.exists() and path.stat().st_size > 0:
            return True, "", total
        return False, "downloaded file is empty", total
    except Exception as exc:
        return False, str(exc), total


def download_audio_fallback(url: str, job_dir: Path, run_dir: Path, timeout_sec: int, video_id: str) -> tuple[str, list[dict[str, Any]]]:
    sources = load_douyin_audio_sources(job_dir) or fetch_douyin_audio_sources(url, job_dir, timeout_sec)
    attempts: list[dict[str, Any]] = []
    if not sources:
        attempts.append({"status": "no_audio_sources"})
        return "", attempts

    audio_dir = run_dir / "audio"
    for index, source in enumerate(sources[:5], start=1):
        text = source.casefold()
        suffix = ".m4a"
        for candidate in (".mp3", ".aac", ".wav", ".ogg", ".m4a"):
            if candidate in text:
                suffix = candidate
                break
        output = audio_dir / f"{video_id or 'douyin'}-{index}{suffix}"
        ok, error, bytes_written = download_url_to_file(source, output, min(max(20, timeout_sec), 90), MAX_AUDIO_FALLBACK_BYTES)
        method = "urllib"
        if not ok:
            if output.exists():
                output.unlink(missing_ok=True)
            ok, ffmpeg_error, bytes_written = download_m3u8_with_ffmpeg(source, output, min(max(30, timeout_sec), 120))
            if ok:
                method = "ffmpeg"
                error = ""
            else:
                error = f"urllib: {error}; ffmpeg: {ffmpeg_error}"
        attempt = {
            "source_url": source,
            "output_path": str(output),
            "ok": ok,
            "bytes": bytes_written,
            "error": error,
            "method": method,
        }
        attempts.append(attempt)
        if ok:
            return str(output.resolve()), attempts
        if output.exists():
            output.unlink(missing_ok=True)
    return "", attempts


def download_m3u8_with_ffmpeg(url: str, path: Path, timeout_sec: int) -> tuple[bool, str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    completed = run_text([
        "ffmpeg",
        "-y",
        "-headers",
        "Referer: https://www.douyin.com/\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36\r\n",
        "-i",
        url,
        "-c",
        "copy",
        str(path),
    ], capture_output=True, timeout=timeout_sec)
    size = path.stat().st_size if path.exists() else 0
    if completed.returncode == 0 and size > 0:
        return True, "", size
    return False, tail((completed.stderr or completed.stdout or ""), 1000), size


def download_video_fallback(url: str, job_dir: Path, run_dir: Path, timeout_sec: int, video_id: str) -> tuple[str, str, list[dict[str, Any]]]:
    sources = load_douyin_video_sources(job_dir) or fetch_douyin_video_sources(url, job_dir, timeout_sec)
    return download_video_sources(sources, run_dir, timeout_sec, video_id)


def download_video_sources(sources: list[str], run_dir: Path, timeout_sec: int, video_id: str) -> tuple[str, str, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    if not sources:
        attempts.append({"status": "no_video_sources"})
        return "", "", attempts

    video_dir = run_dir / "video"
    for index, source in enumerate(sources[:5], start=1):
        suffix = ".mp4"
        if ".m3u8" in source.casefold():
            suffix = ".mp4"
        output = video_dir / f"{video_id or 'douyin'}-{index}{suffix}"
        if ".m3u8" in source.casefold():
            ok, error, bytes_written = download_m3u8_with_ffmpeg(source, output, min(max(30, timeout_sec), 120))
        else:
            ok, error, bytes_written = download_url_to_file(source, output, min(max(20, timeout_sec), 90))
        attempt = {
            "source_url": source,
            "output_path": str(output),
            "ok": ok,
            "bytes": bytes_written,
            "error": error,
        }
        attempts.append(attempt)
        if ok:
            return str(output.resolve()), "douyin_page_video_source", attempts
        if output.exists():
            output.unlink(missing_ok=True)
    return "", "", attempts


def recover_visual_video_after_speech(
    url: str,
    job_dir: Path,
    run_dir: Path,
    timeout_sec: int,
    video_id: str,
    *,
    attempts: list[dict[str, Any]] | None = None,
) -> tuple[str, str, list[dict[str, Any]], dict[str, Any]]:
    existing_attempts = list(attempts or [])
    recovery: dict[str, Any] = {
        "stage": "video_recovery_after_dyt_success",
        "trigger": "dyt_success_without_video_path",
        "ok": False,
        "reason": "",
        "attempts_before_recovery": existing_attempts,
        "retry_attempts": [],
    }

    retry_timeout = max(timeout_sec, 120)
    sources = fetch_douyin_video_sources(url, job_dir, retry_timeout)
    video_path, source, retry_attempts = download_video_sources(sources, run_dir, retry_timeout, video_id)
    recovery["retry_attempts"] = retry_attempts
    recovery["ok"] = bool(video_path)
    recovery["reason"] = "video_downloaded_for_ocr" if video_path else "video_source_still_unavailable"
    all_attempts = existing_attempts + retry_attempts
    return video_path, source, all_attempts, recovery


def extract_audio_from_video(video_path: Path, audio_path: Path, timeout_sec: int) -> tuple[bool, str, int]:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    completed = run_text([
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        str(audio_path),
    ], capture_output=True, timeout=timeout_sec)
    size = audio_path.stat().st_size if audio_path.exists() else 0
    if completed.returncode == 0 and size > 0:
        return True, "", size
    return False, tail((completed.stderr or completed.stdout or ""), 1200), size


def normalize_audio_to_wav(input_path: Path, output_path: Path, timeout_sec: int) -> tuple[bool, str, int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = run_text([
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        str(output_path),
    ], capture_output=True, timeout=timeout_sec)
    size = output_path.stat().st_size if output_path.exists() else 0
    if completed.returncode == 0 and size > 0:
        return True, "", size
    return False, tail((completed.stderr or completed.stdout or ""), 1200), size


def transcribe_audio_with_whisper(audio_path: Path, output_base: Path, timeout_sec: int) -> tuple[bool, str, str, str]:
    output_base.parent.mkdir(parents=True, exist_ok=True)
    completed = run_text([
        str(WHISPER_CLI),
        "-m",
        str(MODEL_PATH),
        "-f",
        str(audio_path),
        "-l",
        "zh",
        "-otxt",
        "-of",
        str(output_base),
        "-nt",
    ], cwd=str(PROJECT_ROOT), capture_output=True, timeout=timeout_sec, env=build_tool_env())
    transcript_path = output_base.with_suffix(".txt")
    transcript = ""
    if transcript_path.exists():
        transcript = clean_transcript(transcript_path.read_text(encoding="utf-8", errors="replace"))
    if completed.returncode == 0 and transcript:
        return True, transcript, str(transcript_path), ""
    detail = completed.stderr or completed.stdout or ""
    if not transcript_path.exists():
        detail = f"{detail}\nwhisper transcript file missing: {transcript_path}"
    elif not transcript:
        detail = f"{detail}\nwhisper transcript file empty after cleaning: {transcript_path}"
    return False, transcript, str(transcript_path), tail(detail, 1600)


def local_whisper_from_audio(audio_path: Path, output_base: Path, timeout_sec: int) -> tuple[dict[str, Any], str]:
    normalized_audio_path = output_base.parent / f"{output_base.name}.wav"
    fallback: dict[str, Any] = {
        "stage": "local_whisper_from_audio",
        "ok": False,
        "audio_path": str(audio_path),
        "normalized_audio_path": str(normalized_audio_path),
        "normalize_audio": {},
        "transcript_path": str(output_base.with_suffix(".txt")),
        "whisper": {},
        "error": "",
    }
    if not audio_path.exists():
        fallback["error"] = f"audio_path missing: {audio_path}"
        return fallback, ""
    if not command_available(WHISPER_CLI):
        fallback["error"] = f"whisper-cli missing: {WHISPER_CLI}"
        return fallback, ""
    if not MODEL_PATH.exists():
        fallback["error"] = f"whisper model missing: {MODEL_PATH}"
        return fallback, ""
    normalize_ok, normalize_error, normalize_bytes = normalize_audio_to_wav(
        audio_path,
        normalized_audio_path,
        min(max(30, timeout_sec), 180),
    )
    fallback["normalize_audio"] = {
        "ok": normalize_ok,
        "bytes": normalize_bytes,
        "error": normalize_error,
    }
    if not normalize_ok:
        fallback["error"] = f"normalize audio failed: {normalize_error}"
        return fallback, ""
    whisper_ok, transcript, transcript_path, whisper_error = transcribe_audio_with_whisper(
        normalized_audio_path,
        output_base,
        min(max(60, timeout_sec), timeout_sec + 300),
    )
    fallback["whisper"] = {
        "ok": whisper_ok,
        "command_sanitized": sanitized_local_whisper_command(audio_path, output_base),
        "transcript_path": transcript_path,
        "error": whisper_error,
    }
    fallback["transcript_path"] = transcript_path
    fallback["ok"] = bool(whisper_ok and not is_music_only(transcript))
    if not fallback["ok"]:
        fallback["error"] = whisper_error or "local whisper produced no speech"
        return fallback, transcript
    return fallback, transcript


def local_whisper_from_video(video_path: str, run_dir: Path, timeout_sec: int) -> tuple[dict[str, Any], str]:
    fallback_dir = run_dir / "local_whisper_fallback"
    audio_path = fallback_dir / "audio.wav"
    output_base = fallback_dir / "transcript"
    fallback: dict[str, Any] = {
        "stage": "local_whisper_from_video",
        "ok": False,
        "video_path": video_path,
        "audio_path": str(audio_path),
        "transcript_path": str(output_base.with_suffix(".txt")),
        "extract_audio": {},
        "whisper": {},
        "error": "",
    }

    if not video_path or not Path(video_path).exists():
        fallback["error"] = "video_path missing for local whisper fallback"
        return fallback, ""
    if not command_available(WHISPER_CLI):
        fallback["error"] = f"whisper-cli missing: {WHISPER_CLI}"
        return fallback, ""
    if not MODEL_PATH.exists():
        fallback["error"] = f"whisper model missing: {MODEL_PATH}"
        return fallback, ""

    audio_ok, audio_error, audio_bytes = extract_audio_from_video(
        Path(video_path),
        audio_path,
        min(max(30, timeout_sec), 180),
    )
    fallback["extract_audio"] = {
        "ok": audio_ok,
        "bytes": audio_bytes,
        "error": audio_error,
    }
    if not audio_ok:
        fallback["error"] = f"extract audio failed: {audio_error}"
        return fallback, ""

    whisper_ok, transcript, transcript_path, whisper_error = transcribe_audio_with_whisper(
        audio_path,
        output_base,
        min(max(60, timeout_sec), timeout_sec + 300),
    )
    fallback["whisper"] = {
        "ok": whisper_ok,
        "command_sanitized": sanitized_local_whisper_command(audio_path, output_base),
        "transcript_path": transcript_path,
        "error": whisper_error,
    }
    fallback["transcript_path"] = transcript_path
    fallback["ok"] = bool(whisper_ok and not is_music_only(transcript))
    if not fallback["ok"]:
        fallback["error"] = whisper_error or "local whisper produced no speech"
        return fallback, transcript
    return fallback, transcript


def local_whisper_fallback(
    *,
    video_path: str,
    source_url: str,
    job_dir: Path,
    run_dir: Path,
    timeout_sec: int,
    video_id: str,
) -> tuple[dict[str, Any], str]:
    result: dict[str, Any] = {
        "stage": "local_whisper_fallback",
        "ok": False,
        "video_path": video_path,
        "source_url": source_url,
        "video_audio_attempt": {},
        "audio_source_attempts": [],
        "audio_whisper_attempt": {},
        "audio_path": "",
        "transcript_path": "",
        "error": "",
    }

    if video_path:
        video_attempt, transcript = local_whisper_from_video(video_path, run_dir, timeout_sec)
        result["video_audio_attempt"] = video_attempt
        if video_attempt.get("ok"):
            result.update({
                "ok": True,
                "audio_path": video_attempt.get("audio_path", ""),
                "transcript_path": video_attempt.get("transcript_path", ""),
                "source": "video_embedded_audio",
            })
            return result, transcript

    audio_path, audio_attempts = download_audio_fallback(source_url, job_dir, run_dir, timeout_sec, video_id)
    result["audio_source_attempts"] = audio_attempts
    if audio_path:
        output_base = run_dir / "local_whisper_fallback" / "audio_source_transcript"
        audio_attempt, transcript = local_whisper_from_audio(Path(audio_path), output_base, timeout_sec)
        result["audio_whisper_attempt"] = audio_attempt
        if audio_attempt.get("ok"):
            result.update({
                "ok": True,
                "audio_path": audio_path,
                "transcript_path": audio_attempt.get("transcript_path", ""),
                "source": "douyin_page_audio_source",
            })
            return result, transcript

    errors = []
    video_error = (result.get("video_audio_attempt") or {}).get("error")
    if video_error:
        errors.append(f"video_audio: {video_error}")
    if audio_attempts and audio_attempts[0].get("status") == "no_audio_sources":
        errors.append("audio_source: no_audio_sources")
    audio_error = (result.get("audio_whisper_attempt") or {}).get("error")
    if audio_error:
        errors.append(f"audio_source_whisper: {audio_error}")
    result["error"] = "; ".join(errors) or "local whisper fallback failed"
    return result, ""


def run_dyt_direct(url: str, job_dir: Path, timeout_sec: int, label: str) -> tuple[dict[str, Any], str]:
    run_dir = Path(os.environ.get("TEMP") or str(job_dir)) / "LucasTranscribe" / f"{job_dir.name}-{label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = run_dir / "transcript.txt"
    transcript_path.unlink(missing_ok=True)
    env = build_tool_env()
    cmd = [
        str(DYT_EXE),
        url,
        "--local",
        "--language",
        "zh",
        "--model-path",
        str(MODEL_PATH),
        "--output",
        str(transcript_path),
    ]
    completed = run_text(cmd, cwd=str(PROJECT_ROOT), capture_output=True, timeout=timeout_sec, env=env)
    combined = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    info = parse_dyt_output(combined)
    transcript = ""
    if transcript_path.exists():
        transcript = clean_transcript(transcript_path.read_text(encoding="utf-8", errors="replace"))
    fallback_reason = dyt_fallback_reason(completed.returncode, transcript)
    video_path = find_video_file(run_dir, info)
    video_path_source = "dyt_output_or_temp_dir" if video_path else ""
    video_download_attempts: list[dict[str, Any]] = []
    if not video_path:
        video_id = video_id_from_url(url) or video_id_from_url(info.get("resolved_url", ""))
        video_path, video_path_source, video_download_attempts = download_video_fallback(
            info.get("resolved_url") or url,
            job_dir,
            run_dir,
            timeout_sec,
            video_id,
        )
    video_recovery_result: dict[str, Any] = {}
    if completed.returncode == 0 and transcript and not is_music_only(transcript) and not video_path:
        video_id = video_id_from_url(url) or video_id_from_url(info.get("resolved_url", ""))
        video_path, video_path_source, video_download_attempts, video_recovery_result = recover_visual_video_after_speech(
            info.get("resolved_url") or url,
            job_dir,
            run_dir,
            timeout_sec,
            video_id,
            attempts=video_download_attempts,
        )
    local_fallback_result: dict[str, Any] = {}
    if fallback_reason:
        local_fallback_result, fallback_transcript = local_whisper_fallback(
            video_path=video_path,
            source_url=info.get("resolved_url") or url,
            job_dir=job_dir,
            run_dir=run_dir,
            timeout_sec=timeout_sec,
            video_id=video_id_from_url(url) or video_id_from_url(info.get("resolved_url", "")),
        )
        local_fallback_result["trigger_reason"] = fallback_reason
        local_fallback_result["policy"] = speech_fallback_policy()
        if local_fallback_result.get("ok"):
            transcript = fallback_transcript
            if not video_path:
                retry_video_path, retry_video_source, retry_video_attempts = download_video_fallback(
                    info.get("resolved_url") or url,
                    job_dir,
                    run_dir,
                    timeout_sec,
                    video_id_from_url(url) or video_id_from_url(info.get("resolved_url", "")),
                )
                local_fallback_result["post_audio_video_download_attempts"] = retry_video_attempts
                if retry_video_path:
                    video_path = retry_video_path
                    video_path_source = retry_video_source or "douyin_page_video_source_after_audio_fallback"
                    video_download_attempts = retry_video_attempts
    attempt = {
        "label": label,
        "url": url,
        "video_id": video_id_from_url(url) or video_id_from_url(info.get("resolved_url", "")),
        "transcribe_source": "dyt",
        "dyt_command_sanitized": sanitized_command(url, transcript_path),
        "dyt_exit_code": completed.returncode,
        "dyt_stdout_tail": tail(completed.stdout or ""),
        "dyt_stderr_tail": tail(completed.stderr or ""),
        "resolved_url": info.get("resolved_url", ""),
        "audio_path": info.get("audio_path", ""),
        "video_path": video_path,
        "video_path_source": video_path_source,
        "video_download_attempts": video_download_attempts,
        "video_recovery_after_dyt_success": video_recovery_result,
        "local_whisper_fallback": local_fallback_result,
        "fallback_policy": speech_fallback_policy(),
        "fallback_triggered": bool(fallback_reason),
        "fallback_reason": fallback_reason,
        "fallback_ok": bool(local_fallback_result.get("ok")),
        "fallback_stage": local_fallback_stage(local_fallback_result),
        "transcript_path": str(transcript_path),
        "transcript_exists": transcript_path.exists(),
        "temp_dir": str(run_dir),
        "failed_reason": None if completed.returncode == 0 else classify_transcribe_failure(combined, ""),
    }
    if local_fallback_result.get("ok"):
        attempt["transcribe_source"] = str(local_fallback_result.get("source") or "local_whisper_fallback")
        attempt["transcript_path"] = local_fallback_result.get("transcript_path", str(transcript_path))
        attempt["transcript_exists"] = True
        attempt["audio_path"] = local_fallback_result.get("audio_path", "")
        attempt["failed_reason"] = None
        if video_path:
            attempt["video_path_source"] = f"{video_path_source}+local_whisper_fallback" if video_path_source else "local_whisper_fallback"
    return attempt, transcript


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe media into transcript.json without MCP.")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--url")
    parser.add_argument("--video-path")
    parser.add_argument("--timeout-sec", type=int, default=300)
    args = parser.parse_args()

    job_dir = Path(args.job_dir).resolve()
    result = base_result(args)
    result["dependency_check"]["ffmpeg_available"] = run_text(
        ["powershell", "-NoProfile", "-Command", "if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"],
        capture_output=True,
    ).returncode == 0

    if not args.url and not args.video_path:
        result.update({"ok": True, "status": "skipped_no_input", "error": None})
        write_json(job_dir, "transcript.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.video_path and not args.url:
        run_dir = Path(os.environ.get("TEMP") or str(job_dir)) / "LucasTranscribe" / f"{job_dir.name}-local_video"
        fallback, transcript = local_whisper_from_video(args.video_path, run_dir, args.timeout_sec)
        has_speech = bool(fallback.get("ok") and not is_music_only(transcript))
        result.update({
            "ok": has_speech,
            "dry_run_ok": True,
            "transcribe_source": "local_whisper_from_video",
            "local_whisper_fallback": fallback,
            "video_path": args.video_path,
            "video_path_source": "input_video_path",
            "audio_path": fallback.get("audio_path", ""),
            "transcript_path": fallback.get("transcript_path", ""),
            "transcript": transcript.strip(),
            "has_speech": has_speech,
            "status": "transcribed" if has_speech else "transcribe_failed",
            "confidence": "medium" if has_speech else "low",
            "failed_reason": None if has_speech else "local_whisper_from_video_failed",
            "error": None if has_speech else fallback.get("error", "local whisper fallback failed"),
            "temp_dir": str(run_dir),
        })
        write_json(job_dir, "transcript.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if has_speech else 1

    if args.url and "douyin" in args.url.casefold():
        if not TRANSCRIBE_SCRIPT.exists():
            result.update({"status": "transcribe_failed", "failed_reason": "script_missing", "error": f"Script not found: {TRANSCRIBE_SCRIPT}"})
            write_json(job_dir, "transcript.json", result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

        try:
            dry = run_powershell(args.url, args.timeout_sec, dry_run=True)
            result["dry_run_ok"] = dry.returncode == 0
            result["stderr_tail"] = tail(dry.stderr or "")
            result["stdout_tail"] = tail(dry.stdout or "")
            if dry.returncode != 0:
                result.update({
                    "ok": False,
                    "status": "transcribe_failed",
                    "failed_reason": "dry_run_failed",
                    "error": f"DryRun exited with code {dry.returncode}",
                })
                write_json(job_dir, "transcript.json", result)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1

            attempts = []
            final_attempt: dict[str, Any] | None = None
            final_transcript = ""
            first_success_attempt: dict[str, Any] | None = None
            first_success_transcript = ""
            for index, candidate in enumerate(douyin_candidates(args.url, job_dir), start=1):
                attempt, transcript = run_dyt_direct(candidate["url"], job_dir, args.timeout_sec, f"{index}-{candidate['label']}")
                attempt["candidate_label"] = candidate["label"]
                local_whisper_ok = bool((attempt.get("local_whisper_fallback") or {}).get("ok"))
                attempt["has_speech"] = (attempt["dyt_exit_code"] == 0 or local_whisper_ok) and not is_music_only(transcript)
                attempt["status"] = "transcribed" if attempt["has_speech"] else ("no_speech_detected" if attempt["dyt_exit_code"] == 0 and transcript else "transcribe_failed")
                attempts.append(attempt)
                if attempt["dyt_exit_code"] == 0 and first_success_attempt is None:
                    first_success_attempt = attempt
                    first_success_transcript = transcript
                if attempt["has_speech"]:
                    final_attempt = attempt
                    final_transcript = transcript
                    break

            if final_attempt is None and first_success_attempt is not None:
                final_attempt = first_success_attempt
                final_transcript = first_success_transcript
            elif final_attempt is None and attempts:
                final_attempt = attempts[-1]

            transcript = final_transcript.strip()
            final_local_whisper_ok = bool(final_attempt and (final_attempt.get("local_whisper_fallback") or {}).get("ok"))
            has_speech = final_attempt is not None and (final_attempt.get("dyt_exit_code") == 0 or final_local_whisper_ok) and not is_music_only(transcript)
            transcribe_ok = bool(final_attempt and (final_attempt.get("dyt_exit_code") == 0 or final_local_whisper_ok))
            result.update({
                "ok": transcribe_ok,
                "dry_run_ok": True,
                "resolver_attempts": attempts,
                "transcribe_source": final_attempt.get("transcribe_source", "") if final_attempt else "",
                "local_whisper_fallback": final_attempt.get("local_whisper_fallback", {}) if final_attempt else {},
                "fallback_policy": final_attempt.get("fallback_policy", speech_fallback_policy()) if final_attempt else speech_fallback_policy(),
                "fallback_triggered": final_attempt.get("fallback_triggered", False) if final_attempt else False,
                "fallback_reason": final_attempt.get("fallback_reason", "") if final_attempt else "",
                "fallback_ok": final_attempt.get("fallback_ok", False) if final_attempt else False,
                "fallback_stage": final_attempt.get("fallback_stage", "") if final_attempt else "",
                "dyt_command_sanitized": final_attempt.get("dyt_command_sanitized", "") if final_attempt else "",
                "dyt_exit_code": final_attempt.get("dyt_exit_code") if final_attempt else None,
                "dyt_stdout_tail": final_attempt.get("dyt_stdout_tail", "") if final_attempt else "",
                "dyt_stderr_tail": final_attempt.get("dyt_stderr_tail", "") if final_attempt else "",
                "resolved_url_used": final_attempt.get("resolved_url", "") if final_attempt else "",
                "video_id": final_attempt.get("video_id", "") if final_attempt else "",
                "video_path": final_attempt.get("video_path", "") if final_attempt else "",
                "video_path_source": final_attempt.get("video_path_source", "") if final_attempt else "",
                "video_download_attempts": final_attempt.get("video_download_attempts", []) if final_attempt else [],
                "video_recovery_after_dyt_success": final_attempt.get("video_recovery_after_dyt_success", {}) if final_attempt else {},
                "audio_path": final_attempt.get("audio_path", "") if final_attempt else "",
                "transcript_path": final_attempt.get("transcript_path", "") if final_attempt else "",
                "transcript": transcript,
                "has_speech": has_speech,
                "status": "transcribed" if has_speech else ("empty_transcript" if transcribe_ok and not transcript else ("no_speech_detected" if transcribe_ok else "transcribe_failed")),
                "confidence": "medium" if has_speech else "low",
                "stdout_tail": final_attempt.get("dyt_stdout_tail", "") if final_attempt else "",
                "stderr_tail": final_attempt.get("dyt_stderr_tail", "") if final_attempt else "",
                "temp_dir": final_attempt.get("temp_dir", "") if final_attempt else "",
            })
            if not result["ok"]:
                try:
                    run_text([
                        sys.executable,
                        str(PROJECT_ROOT / "tools" / "fetch_douyin_page_playwright.py"),
                        "--job-dir",
                        str(job_dir),
                        "--url",
                        args.url,
                    ], cwd=str(PROJECT_ROOT), capture_output=True, timeout=60)
                except Exception:
                    pass
                result.update({
                    "failed_reason": final_attempt.get("failed_reason", "url_resolution_failed") if final_attempt else "url_resolution_failed",
                    "error": f"dyt failed after {len(attempts)} resolver attempt(s)",
                })
                write_json(job_dir, "transcript.json", result)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1
            write_json(job_dir, "transcript.json", result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except subprocess.TimeoutExpired as exc:
            result.update({
                "ok": False,
                "status": "transcribe_failed",
                "failed_reason": "timeout",
                "error": f"transcribe timed out after {args.timeout_sec}s",
                "stdout_tail": tail(exc.stdout or ""),
                "stderr_tail": tail(exc.stderr or ""),
            })
            write_json(job_dir, "transcript.json", result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

    result.update({
        "ok": True,
        "status": "skipped_unsupported_source",
        "failed_reason": None,
        "error": "V1 only wraps the existing Douyin transcription script.",
    })
    write_json(job_dir, "transcript.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
