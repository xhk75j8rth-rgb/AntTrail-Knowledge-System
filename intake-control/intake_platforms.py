from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent
SESSION_DIR = PROJECT_ROOT / "runtime" / "browser_sessions"
BROWSER_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]
PLATFORM_DEBUG_PORTS = {
    "douyin": 11441,
    "toutiao": 11442,
    "xiaohongshu": 11443,
}
AUTH_WINDOW_TIMEOUT_SEC = 90
AUTH_WINDOW_POLL_SEC = 3
_AUTH_MONITOR_LOCK = threading.Lock()
_AUTH_MONITOR_IDS: dict[str, str] = {}
PLATFORM_AUTH_PROBE_MARKERS = {
    "douyin": {
        "login_prompt": ["扫码登录", "手机号登录", "密码登录", "请登录", "登录后"],
        "verification": ["验证码", "请完成安全验证", "访问频繁", "系统检测到"],
        "logged_in": ["发布视频", "创作者服务中心", "消息", "我的"],
        "platform": ["抖音", "推荐", "关注"],
    },
    "toutiao": {
        "login_prompt": ["扫码登录", "手机号登录", "密码登录", "请先登录", "登录后"],
        "verification": ["验证码", "请完成安全验证", "访问频繁", "系统检测到"],
        "logged_in": ["发布作品", "消息", "我的", "创作中心"],
        "platform": ["今日头条", "头条热榜", "推荐"],
    },
    "xiaohongshu": {
        "login_prompt": ["登录/注册", "扫码登录", "手机号登录", "密码登录", "验证码登录", "登录后"],
        "verification": ["验证码", "请完成安全验证", "访问频繁", "系统检测到", "安全验证"],
        "logged_in": ["发布", "通知", "消息", "创作中心"],
        "platform": ["小红书", "发现", "笔记"],
    },
}


@dataclass(frozen=True)
class PlatformRule:
    id: str
    label: str
    auth_required: bool
    auth_url: str
    profile_slug: str
    host_markers: tuple[str, ...]
    notes: str

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["host_markers"] = list(self.host_markers)
        return data


@dataclass(frozen=True)
class LinkRoute:
    id: str
    label: str
    source_type: str
    content_kind: str
    route_level: str
    strategy: str
    notes: str

    def public(self) -> dict[str, Any]:
        return asdict(self)


PLATFORM_RULES: dict[str, PlatformRule] = {
    "douyin": PlatformRule(
        id="douyin",
        label="抖音",
        auth_required=True,
        auth_url="https://www.douyin.com/",
        profile_slug="douyin",
        host_markers=("douyin.com", "iesdouyin.com"),
        notes="当前公开视频读取暂不强制依赖授权，但后续评论、互动和更稳定页面读取会复用本机授权会话。",
    ),
    "toutiao": PlatformRule(
        id="toutiao",
        label="今日头条",
        auth_required=True,
        auth_url="https://www.toutiao.com/",
        profile_slug="toutiao",
        host_markers=("toutiao.com",),
        notes="授权只提供本机浏览器会话；具体走文章、视频或普通网页路线由链接形态决定。",
    ),
    "xiaohongshu": PlatformRule(
        id="xiaohongshu",
        label="小红书",
        auth_required=True,
        auth_url="https://www.xiaohongshu.com/explore",
        profile_slug="xiaohongshu",
        host_markers=("xiaohongshu.com", "xhslink.com"),
        notes="授权只提供本机浏览器会话；图文/视频/普通页面由链接和页面材料判断。",
    ),
    "webpage": PlatformRule(
        id="webpage",
        label="普通网页",
        auth_required=False,
        auth_url="",
        profile_slug="webpage",
        host_markers=(),
        notes="普通网页不需要平台授权；正文不足时不能冒充正式知识卡。",
    ),
}


LINK_ROUTES: dict[str, LinkRoute] = {
    "video_highest_available": LinkRoute(
        id="video_highest_available",
        label="视频最高可达链路",
        source_type="video",
        content_kind="video",
        route_level="highest_available",
        strategy="页面元信息 -> 音频/口播转写 -> 条件 OCR -> 评论增强 -> 模型写卡 -> 质量门禁",
        notes="只要链接形态指向视频内容，就优先按视频材料读取；平台只是上下文。",
    ),
    "text_extract_then_analyze": LinkRoute(
        id="text_extract_then_analyze",
        label="文本正文分析链路",
        source_type="text",
        content_kind="text_article",
        route_level="text_extract_then_analyze",
        strategy="渲染页面 -> 提取正文纯文本与基础元信息 -> 模型分析写卡 -> 质量门禁",
        notes="适合文章、长文、公告等文本主体链接。",
    ),
    "hybrid_text_video_ocr": LinkRoute(
        id="hybrid_text_video_ocr",
        label="混合笔记链路",
        source_type="mixed",
        content_kind="mixed_note",
        route_level="hybrid_text_video_ocr",
        strategy="先判图文/视频笔记 -> 提取标题正文与公开元信息 -> 视频或关键信息在图中时条件 OCR -> 可用音轨再转写 -> 模型分析写卡 -> 质量门禁",
        notes="适合图文/视频混合笔记；不默认全量 OCR，也不默认纯文本。",
    ),
    "basic_webpage": LinkRoute(
        id="basic_webpage",
        label="普通网页正文链路",
        source_type="webpage",
        content_kind="webpage",
        route_level="basic_webpage",
        strategy="通用页面读取 -> 提取标题、正文与基础媒体信号 -> 材料门禁 -> 模型写卡或来源材料卡",
        notes="未知链接先进入来源材料审查，材料不足时降级；不再直接跳过审查。",
    ),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def default_profile_base() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "LucasKnowledgeDB" / "browser_profiles"


def profile_dir(platform_id: str) -> Path:
    rule = PLATFORM_RULES[platform_id]
    return default_profile_base() / rule.profile_slug


def session_status_path(platform_id: str) -> Path:
    rule = PLATFORM_RULES[platform_id]
    return SESSION_DIR / f"{rule.profile_slug}_session.json"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def find_browser(explicit: str | None = None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    for candidate in BROWSER_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def platform_for_host(host: str) -> PlatformRule:
    normalized = (host or "").casefold()
    for rule in PLATFORM_RULES.values():
        if rule.id == "webpage":
            continue
        if any(normalized == marker or normalized.endswith(f".{marker}") for marker in rule.host_markers):
            return rule
    return PLATFORM_RULES["webpage"]


def route_for_link(parsed: Any, platform: PlatformRule) -> tuple[LinkRoute, str]:
    host = (parsed.hostname or "").casefold()
    path = (parsed.path or "").casefold()

    if platform.id == "douyin" and any(marker in path for marker in ("/video/", "/share/video/")):
        return LINK_ROUTES["video_highest_available"], "douyin_video_path"
    if platform.id == "douyin":
        return LINK_ROUTES["video_highest_available"], "douyin_short_or_share_link"
    if any(path.endswith(ext) for ext in (".mp4", ".mov", ".m4v", ".webm", ".m3u8")):
        return LINK_ROUTES["video_highest_available"], "media_file_extension"
    if platform.id == "toutiao" and "/article/" in path:
        return LINK_ROUTES["text_extract_then_analyze"], "article_path"
    if platform.id == "xiaohongshu" and any(marker in path for marker in ("/discovery/item/", "/explore/", "/item/")):
        return LINK_ROUTES["hybrid_text_video_ocr"], "mixed_note_path"
    if host == "xhslink.com" or host.endswith(".xhslink.com"):
        return LINK_ROUTES["hybrid_text_video_ocr"], "short_mixed_note_link"
    return LINK_ROUTES["basic_webpage"], "generic_webpage"


def route_source_type(route: LinkRoute, platform: PlatformRule) -> str:
    if platform.id in {"douyin", "toutiao", "xiaohongshu"}:
        return f"{route.source_type}/{platform.id}"
    return route.source_type


def classify_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url or "")
    platform = platform_for_host(parsed.hostname or "")
    route, route_basis = route_for_link(parsed, platform)
    return {
        "ok": bool(parsed.scheme and parsed.netloc),
        "url": url,
        "route_id": route.id,
        "route_label": route.label,
        "route_basis": route_basis,
        "platform_id": platform.id,
        "platform_label": platform.label,
        "source_type": route_source_type(route, platform),
        "content_kind": route.content_kind,
        "route_level": route.route_level,
        "strategy": route.strategy,
        "auth_required": platform.auth_required,
        "used_mcp": False,
    }


def _profile_has_files(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return any(path.iterdir())
    except OSError:
        return True


def cdp_port_ready(port: int | None) -> bool:
    if not port:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json/list", timeout=1.5) as response:
            return response.status == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def active_debug_port(platform_id: str) -> int:
    configured = PLATFORM_DEBUG_PORTS.get(platform_id) or 0
    if configured and cdp_port_ready(configured):
        return configured
    return 0


def profile_browser_process_summary(platform_id: str) -> dict[str, Any]:
    """Report browser processes using this platform profile without closing them."""
    path = profile_dir(platform_id)
    if os.name != "nt":
        return {
            "ok": True,
            "platform_id": platform_id,
            "matched_processes": 0,
            "debug_ports": [],
            "headless_processes": 0,
            "error": "unsupported_platform",
        }
    script = r"""
$profile = [System.IO.Path]::GetFullPath($env:LUCAS_PROFILE_DIR)
$items = @(Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -ieq 'msedge.exe' -or $_.Name -ieq 'chrome.exe') -and
  $_.CommandLine -and
  $_.CommandLine.IndexOf($profile, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
})
$debugPorts = @($items | ForEach-Object {
  if ($_.CommandLine -match '--remote-debugging-port=(\d+)') { $Matches[1] }
})
@{
  ok = $true
  matched_processes = $items.Count
  debug_ports = $debugPorts
  headless_processes = @($items | Where-Object { $_.CommandLine -match '--headless' }).Count
} | ConvertTo-Json -Compress
"""
    env = dict(os.environ)
    env["LUCAS_PROFILE_DIR"] = str(path)
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            cwd=str(PROJECT_ROOT),
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=8,
            env=env,
        )
        data = json.loads((completed.stdout or "").strip() or "{}")
        if not isinstance(data, dict):
            data = {}
        data.setdefault("ok", completed.returncode == 0)
        data.setdefault("matched_processes", 0)
        data.setdefault("debug_ports", [])
        data.setdefault("headless_processes", 0)
        if completed.returncode != 0:
            data["error"] = (completed.stderr or "profile browser inspection failed")[-500:]
        data["platform_id"] = platform_id
        return data
    except Exception as exc:
        return {
            "ok": False,
            "platform_id": platform_id,
            "matched_processes": 0,
            "debug_ports": [],
            "headless_processes": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _public_last_test(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "tested_at",
        "status",
        "label",
        "auth_ok",
        "page_access_ok",
        "probe_completed",
        "debug_port",
        "profile_ready",
        "error",
        "next_step",
    }
    return {key: value.get(key) for key in allowed if key in value}


def authorization_status(platform_id: str) -> dict[str, Any]:
    rule = PLATFORM_RULES[platform_id]
    browser = find_browser()
    status_path = session_status_path(platform_id)
    status_payload = read_json(status_path)
    profile_path = profile_dir(platform_id)
    profile_ready = _profile_has_files(profile_path)
    configured_debug_port = PLATFORM_DEBUG_PORTS.get(platform_id)
    reported_debug_port = configured_debug_port or status_payload.get("debug_port")
    debug_port_is_ready = cdp_port_ready(reported_debug_port)
    session_ready = bool(profile_ready or status_payload.get("status") == "browser_opened")
    last_test = _public_last_test(status_payload.get("last_test"))

    if not rule.auth_required:
        state = "not_required"
        label = "无需授权"
        authorized = True
    elif last_test.get("auth_ok") is True:
        state = "verified"
        label = "授权可用"
        authorized = True
    elif last_test.get("probe_completed") and last_test.get("status") in {
        "not_authorized",
        "verification_required",
        "missing_profile",
        "cdp_unavailable",
        "authorization_timeout",
    }:
        state = "test_failed"
        label = last_test.get("label") or "授权待修复"
        authorized = False
    elif session_ready:
        state = "ready_unverified"
        label = "会话待测试"
        authorized = False
    else:
        state = "missing"
        label = "未授权"
        authorized = False

    return {
        "platform_id": rule.id,
        "platform_label": rule.label,
        "authorization_required": rule.auth_required,
        "authorized": authorized,
        "session_ready": session_ready,
        "state": state,
        "label": label,
        "can_authorize": bool(rule.auth_required and browser),
        "can_test": bool(rule.auth_required and browser),
        "browser_available": bool(browser),
        "browser": str(browser) if browser else "",
        "profile_ready": profile_ready,
        "profile_dir": str(profile_path),
        "status_path": str(status_path),
        "configured_debug_port": configured_debug_port,
        "debug_port": reported_debug_port or None,
        "debug_port_ready": debug_port_is_ready,
        "debug_url": status_payload.get("debug_url") or "",
        "last_opened_at": status_payload.get("opened_at") or "",
        "last_status": status_payload.get("status") or "",
        "last_test": last_test,
        "error": status_payload.get("error"),
    }


def platform_catalog(url: str | None = None) -> dict[str, Any]:
    platforms = []
    for rule in PLATFORM_RULES.values():
        item = rule.public()
        item["authorization"] = authorization_status(rule.id)
        platforms.append(item)
    payload: dict[str, Any] = {
        "ok": True,
        "platforms": platforms,
        "routes": [route.public() for route in LINK_ROUTES.values()],
        "used_mcp": False,
    }
    if url:
        payload["classification"] = classify_url(url)
    return payload


def safe_auth_url(platform_id: str, requested_url: str | None = None) -> str:
    rule = PLATFORM_RULES[platform_id]
    if not requested_url:
        return rule.auth_url
    parsed = urlparse(requested_url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"}:
        return rule.auth_url
    if any(host == marker or host.endswith(f".{marker}") for marker in rule.host_markers):
        return requested_url
    return rule.auth_url


def browser_args(browser: Path, platform_id: str, url: str) -> list[str]:
    path = profile_dir(platform_id)
    debug_port = PLATFORM_DEBUG_PORTS.get(platform_id)
    return [
        str(browser),
        *([f"--remote-debugging-port={debug_port}"] if debug_port else []),
        f"--user-data-dir={path}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--lang=zh-CN",
        "--new-window",
        url,
    ]


def close_profile_browser_processes(platform_id: str) -> dict[str, Any]:
    """Close only browser processes launched with this platform's local profile."""
    path = profile_dir(platform_id)
    if os.name != "nt":
        return {
            "ok": True,
            "platform_id": platform_id,
            "matched_processes": 0,
            "closed_processes": 0,
            "error": "unsupported_platform",
        }
    script = r"""
$profile = [System.IO.Path]::GetFullPath($env:LUCAS_PROFILE_DIR)
$items = @(Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -ieq 'msedge.exe' -or $_.Name -ieq 'chrome.exe') -and
  $_.CommandLine -and
  $_.CommandLine.IndexOf($profile, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
})
foreach ($item in $items) {
  Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue
}
@{
  ok = $true
  matched_processes = $items.Count
  closed_processes = $items.Count
  process_ids = @($items | ForEach-Object { $_.ProcessId })
} | ConvertTo-Json -Compress
"""
    env = dict(os.environ)
    env["LUCAS_PROFILE_DIR"] = str(path)
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            cwd=str(PROJECT_ROOT),
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=8,
            env=env,
        )
        data = json.loads((completed.stdout or "").strip() or "{}")
        if not isinstance(data, dict):
            data = {}
        data.setdefault("ok", completed.returncode == 0)
        data.setdefault("matched_processes", 0)
        data.setdefault("closed_processes", 0)
        if completed.returncode != 0:
            data["error"] = (completed.stderr or "profile browser cleanup failed")[-500:]
        data["platform_id"] = platform_id
        return data
    except Exception as exc:
        return {
            "ok": False,
            "platform_id": platform_id,
            "matched_processes": 0,
            "closed_processes": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def auth_probe_script() -> str:
    return r"""
const fs = require('fs');
const { spawn } = require('child_process');

const browserPath = process.argv[1];
const targetUrl = process.argv[2];
const profileDir = process.argv[3];
const timeoutMs = Number(process.argv[4] || 15000);
const portArg = Number(process.argv[5] || 0);
const markers = JSON.parse(process.argv[6] || '{}');
const mode = process.argv[7] || 'navigate';
const spawnRequested = mode === 'spawn_navigate';
const currentPageOnly = mode === 'current';
const existingPort = portArg && !spawnRequested ? portArg : 0;
const port = portArg || (11700 + Math.floor(Math.random() * 500));

fs.mkdirSync(profileDir, { recursive: true });

let browser = { kill() {} };
let browserStderr = '';
let spawned = false;
if (!existingPort) {
  spawned = true;
  browser = spawn(browserPath, [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profileDir}`,
    '--profile-directory=Default',
    '--no-first-run',
    '--no-default-browser-check',
    '--headless=new',
    '--disable-gpu',
    '--lang=zh-CN',
    'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  browser.stderr.on('data', chunk => {
    browserStderr += chunk.toString();
    if (browserStderr.length > 3000) browserStderr = browserStderr.slice(-3000);
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function fetchJson(endpoint, timeout) {
  const started = Date.now();
  let lastError = '';
  while (Date.now() - started < timeout) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}${endpoint}`);
      if (response.ok) return await response.json();
      lastError = `HTTP ${response.status}`;
    } catch (err) {
      lastError = String(err && err.message || err);
    }
    await sleep(250);
  }
  throw new Error(`CDP endpoint unavailable: ${endpoint}; ${lastError}`);
}

function makeCdp(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const pending = new Map();
    let nextId = 1;
    ws.onopen = () => {
      resolve({
        send(method, params = {}) {
          const id = nextId++;
          ws.send(JSON.stringify({ id, method, params }));
          return new Promise((res, rej) => {
            const timer = setTimeout(() => {
              pending.delete(id);
              rej(new Error(`CDP timeout: ${method}`));
            }, 10000);
            pending.set(id, { res, rej, timer });
          });
        },
        close() {
          ws.close();
        },
      });
    };
    ws.onerror = err => reject(new Error(String(err && err.message || err)));
    ws.onmessage = event => {
      const msg = JSON.parse(event.data);
      if (msg.id && pending.has(msg.id)) {
        const item = pending.get(msg.id);
        pending.delete(msg.id);
        clearTimeout(item.timer);
        if (msg.error) item.rej(new Error(msg.error.message || JSON.stringify(msg.error)));
        else item.res(msg.result || {});
      }
    };
  });
}

function authProbe(markersArg) {
  function clean(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }
  function hits(text, items) {
    return (items || []).filter(item => item && text.includes(item));
  }
  function storageCount(storage) {
    try { return storage ? Object.keys(storage).length : 0; } catch (_) { return 0; }
  }

  const visibleText = document.body ? clean(document.body.innerText || document.body.textContent || '') : '';
  const loginHits = hits(visibleText, markersArg.login_prompt);
  const verificationHits = hits(visibleText, markersArg.verification);
  const loggedInHits = hits(visibleText, markersArg.logged_in);
  const platformHits = hits(visibleText, markersArg.platform);
  const bodyLength = visibleText.length;
  const localCount = storageCount(window.localStorage);
  const sessionCount = storageCount(window.sessionStorage);
  let browserStorageSignal = false;
  try {
    browserStorageSignal = Boolean(document.cookie && document.cookie.length > 10);
  } catch (_) {}
  browserStorageSignal = browserStorageSignal || localCount > 0 || sessionCount > 0;

  const verificationDetected = verificationHits.length > 0;
  const loginPromptDetected = loginHits.length > 0;
  const loggedInMarkerDetected = loggedInHits.length > 0;
  const platformMarkerDetected = platformHits.length > 0 || bodyLength > 120;
  const pageAccessOk = platformMarkerDetected && !verificationDetected && !loginPromptDetected;
  const authOk = loggedInMarkerDetected && !verificationDetected && !loginPromptDetected;

  return {
    final_url: location.href,
    page_title: document.title || '',
    body_text_length: bodyLength,
    login_prompt_detected: loginPromptDetected,
    verification_detected: verificationDetected,
    logged_in_marker_detected: loggedInMarkerDetected,
    platform_marker_detected: platformMarkerDetected,
    page_access_ok: pageAccessOk,
    auth_ok: authOk,
    browser_storage_signal: browserStorageSignal,
    local_storage_signal_count: localCount,
    session_storage_signal_count: sessionCount,
    marker_hits: {
      login_prompt: loginHits,
      verification: verificationHits,
      logged_in: loggedInHits,
      platform: platformHits,
    },
  };
}

(async () => {
  let cdp;
  try {
    const targets = await fetchJson('/json/list', 12000);
    const pageTarget = targets.find(t => t.type === 'page' && t.webSocketDebuggerUrl);
    if (!pageTarget) throw new Error('No CDP page target found');
    cdp = await makeCdp(pageTarget.webSocketDebuggerUrl);
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    if (!currentPageOnly) {
      await cdp.send('Page.navigate', { url: targetUrl });
      await sleep(Math.min(Math.max(timeoutMs, 4000), 12000));
    } else {
      await sleep(Math.min(Math.max(timeoutMs, 1000), 3000));
    }
    const result = await cdp.send('Runtime.evaluate', {
      expression: `(${authProbe.toString()})(${JSON.stringify(markers)})`,
      returnByValue: true,
      awaitPromise: true,
    });
    const value = result.result && result.result.value || {};
    cdp.close();
    if (spawned) browser.kill();
    console.log(JSON.stringify({
      ok: true,
      status: 'probe_completed',
      debug_port: port,
      reused_authorization_browser: Boolean(existingPort),
      ...value,
      browser_stderr_tail: browserStderr.slice(-800),
    }));
  } catch (err) {
    if (cdp) cdp.close();
    if (spawned) browser.kill();
    console.log(JSON.stringify({
      ok: false,
      status: 'cdp_failed',
      debug_port: port,
      reused_authorization_browser: Boolean(existingPort),
      error: String(err && err.message || err),
      browser_stderr_tail: browserStderr.slice(-800),
    }));
    process.exit(1);
  }
})();
"""


def _probe_status(data: dict[str, Any], *, profile_ready: bool) -> tuple[str, str, str]:
    if data.get("auth_ok"):
        return "authorized", "授权可用", "该本机浏览器会话出现已登录信号，可以复用。"
    if data.get("verification_detected"):
        return "verification_required", "需要验证", "请在授权窗口完成验证码或安全验证后再测试。"
    if data.get("login_prompt_detected"):
        return "not_authorized", "未登录", "请点击重新授权，在打开的浏览器里完成登录后再测试。"
    if data.get("page_access_ok"):
        return "page_accessible_unverified", "页面可读，未确认登录", "页面可以打开，但没有检测到明确登录信号。"
    if profile_ready:
        return "uncertain", "未确认", "会话目录存在，但页面没有给出明确登录或未登录信号。"
    return "missing_profile", "未授权", "请先点击授权登录。"


def _write_last_auth_test(platform_id: str, payload: dict[str, Any]) -> None:
    status_path = session_status_path(platform_id)
    status_payload = read_json(status_path)
    status_payload["last_test"] = payload
    status_payload["last_tested_at"] = payload.get("tested_at") or now_iso()
    write_json(status_path, status_payload)


def _run_auth_probe_node(
    platform_id: str,
    *,
    browser: Path,
    target_url: str,
    profile_path: Path,
    debug_port: int,
    timeout_sec: int,
    current_page_only: bool = False,
    spawn_browser: bool = False,
) -> tuple[dict[str, Any], int, str]:
    mode = "current" if current_page_only else "spawn_navigate" if spawn_browser else "navigate"
    completed = subprocess.run(
        [
            "node",
            "-e",
            auth_probe_script(),
            str(browser),
            target_url,
            str(profile_path),
            str(timeout_sec * 1000),
            str(debug_port),
            json.dumps(PLATFORM_AUTH_PROBE_MARKERS.get(platform_id, {}), ensure_ascii=False),
            mode,
        ],
        cwd=str(PROJECT_ROOT),
        shell=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_sec + 20,
    )
    try:
        data = json.loads((completed.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        data = {
            "ok": False,
            "status": "invalid_probe_json",
            "error": f"Invalid probe JSON: {(completed.stdout or '')[-600:]}",
        }
    if completed.returncode != 0 and not data.get("error"):
        data["error"] = (completed.stderr or "")[-600:] or f"probe exited with code {completed.returncode}"
    return data, int(completed.returncode), completed.stderr or ""


def _probe_payload_from_data(
    base: dict[str, Any],
    data: dict[str, Any],
    *,
    profile_ready: bool,
) -> dict[str, Any]:
    if data.get("ok"):
        status, label, next_step = _probe_status(data, profile_ready=profile_ready)
        return {
            **base,
            "ok": True,
            "status": status,
            "label": label,
            "auth_ok": bool(data.get("auth_ok")),
            "page_access_ok": bool(data.get("page_access_ok")),
            "probe_completed": True,
            "debug_port": data.get("debug_port") or base["debug_port"],
            "debug_port_ready": True,
            "reused_authorization_browser": bool(data.get("reused_authorization_browser")),
            "body_text_length": int(data.get("body_text_length") or 0),
            "login_prompt_detected": bool(data.get("login_prompt_detected")),
            "verification_detected": bool(data.get("verification_detected")),
            "logged_in_marker_detected": bool(data.get("logged_in_marker_detected")),
            "platform_marker_detected": bool(data.get("platform_marker_detected")),
            "browser_storage_signal": bool(data.get("browser_storage_signal")),
            "local_storage_signal_count": int(data.get("local_storage_signal_count") or 0),
            "session_storage_signal_count": int(data.get("session_storage_signal_count") or 0),
            "final_url": data.get("final_url") or "",
            "page_title": data.get("page_title") or "",
            "marker_hits": data.get("marker_hits") if isinstance(data.get("marker_hits"), dict) else {},
            "next_step": next_step,
        }
    error = str(data.get("error") or "authorization probe failed")[:500]
    return {
        **base,
        "ok": True,
        "status": "cdp_unavailable",
        "label": "浏览器会话不可连接",
        "probe_completed": True,
        "error": error,
        "next_step": "请点击重新授权；系统会关闭旧授权窗口并打开新的授权窗口。",
    }


def test_authorization(
    platform_id: str,
    *,
    url: str | None = None,
    browser_path: str | None = None,
    timeout_sec: int = 12,
) -> dict[str, Any]:
    if platform_id not in PLATFORM_RULES:
        return {
            "ok": False,
            "used_mcp": False,
            "status": "unknown_platform",
            "error": f"Unknown platform: {platform_id}",
        }

    rule = PLATFORM_RULES[platform_id]
    tested_at = now_iso()
    if not rule.auth_required:
        payload = {
            "ok": True,
            "used_mcp": False,
            "platform_id": platform_id,
            "platform_label": rule.label,
            "status": "not_required",
            "label": "无需授权",
            "auth_ok": True,
            "page_access_ok": True,
            "probe_completed": True,
            "tested_at": tested_at,
            "next_step": f"{rule.label} 当前不需要授权会话。",
            "authorization": authorization_status(platform_id),
        }
        return payload

    browser = find_browser(browser_path)
    profile_path = profile_dir(platform_id)
    profile_ready = _profile_has_files(profile_path)
    debug_port = active_debug_port(platform_id)
    target_url = safe_auth_url(platform_id, url)
    base: dict[str, Any] = {
        "ok": False,
        "used_mcp": False,
        "stage": "platform_authorization_test",
        "platform_id": platform_id,
        "platform_label": rule.label,
        "url": target_url,
        "profile_dir": str(profile_path),
        "profile_ready": profile_ready,
        "debug_port": debug_port or PLATFORM_DEBUG_PORTS.get(platform_id),
        "debug_port_ready": bool(debug_port),
        "tested_at": tested_at,
        "auth_ok": False,
        "page_access_ok": False,
        "probe_completed": False,
        "write_attempted": False,
    }

    if not browser:
        payload = {
            **base,
            "status": "browser_unavailable",
            "label": "浏览器不可用",
            "error": "No supported Edge/Chrome executable found.",
            "next_step": "请安装或配置 Edge / Chrome 后再测试授权。",
        }
        _write_last_auth_test(platform_id, _public_last_test(payload))
        payload["authorization"] = authorization_status(platform_id)
        return payload
    if not profile_ready and not debug_port:
        payload = {
            **base,
            "ok": True,
            "status": "missing_profile",
            "label": "未授权",
            "probe_completed": True,
            "next_step": "请先点击授权登录。",
        }
        _write_last_auth_test(platform_id, _public_last_test(payload))
        payload["authorization"] = authorization_status(platform_id)
        return payload

    profile_path.mkdir(parents=True, exist_ok=True)
    timeout_sec = min(max(int(timeout_sec or 12), 4), 30)
    spawn_browser = False
    if not debug_port:
        process_summary = profile_browser_process_summary(platform_id)
        if int(process_summary.get("matched_processes") or 0) > 0:
            payload = {
                **base,
                "ok": True,
                "status": "cdp_unavailable",
                "label": "浏览器会话不可连接",
                "probe_completed": True,
                "profile_browser_processes": process_summary,
                "error": "Platform profile browser is running without the configured CDP port.",
                "next_step": "请点击重新授权；系统会关闭旧授权窗口并打开新的授权窗口。",
            }
            _write_last_auth_test(platform_id, _public_last_test(payload))
            payload["authorization"] = authorization_status(platform_id)
            return payload
        debug_port = PLATFORM_DEBUG_PORTS.get(platform_id) or 0
        if not debug_port:
            payload = {
                **base,
                "ok": True,
                "status": "cdp_unavailable",
                "label": "浏览器会话不可连接",
                "probe_completed": True,
                "error": "No configured CDP port for this platform.",
                "next_step": "请点击重新授权；系统会关闭旧授权窗口并打开新的授权窗口。",
            }
            _write_last_auth_test(platform_id, _public_last_test(payload))
            payload["authorization"] = authorization_status(platform_id)
            return payload
        spawn_browser = True
        base["debug_port"] = debug_port
        base["debug_port_ready"] = False

    data, _returncode, stderr = _run_auth_probe_node(
        platform_id,
        browser=browser,
        target_url=target_url,
        profile_path=profile_path,
        debug_port=debug_port,
        timeout_sec=timeout_sec,
        spawn_browser=spawn_browser,
    )
    if not data.get("ok") and stderr and not data.get("error"):
        data["error"] = stderr[-600:]
    payload = _probe_payload_from_data(base, data, profile_ready=profile_ready)
    if spawn_browser:
        payload["spawned_profile_probe"] = True
        payload["profile_browser_cleanup"] = close_profile_browser_processes(platform_id)
        if payload.get("auth_ok"):
            payload["next_step"] = "授权可用；临时测试浏览器已关闭。"
        elif payload.get("status") == "cdp_unavailable":
            payload["next_step"] = "临时测试浏览器不可连接；请点击重新授权打开新的授权窗口。"
    else:
        payload["spawned_profile_probe"] = False

    _write_last_auth_test(platform_id, _public_last_test(payload))
    payload["authorization"] = authorization_status(platform_id)
    return payload


def _monitor_is_current(platform_id: str, monitor_id: str) -> bool:
    with _AUTH_MONITOR_LOCK:
        if _AUTH_MONITOR_IDS.get(platform_id) != monitor_id:
            return False
    status_payload = read_json(session_status_path(platform_id))
    return status_payload.get("auth_monitor_id") == monitor_id


def _finish_authorization_monitor(
    platform_id: str,
    monitor_id: str,
    *,
    status: str,
    reason: str,
    cleanup_result: dict[str, Any],
) -> None:
    status_path = session_status_path(platform_id)
    status_payload = read_json(status_path)
    if status_payload.get("auth_monitor_id") != monitor_id:
        return
    auto_close = status_payload.get("auto_close") if isinstance(status_payload.get("auto_close"), dict) else {}
    auto_close.update({
        "enabled": True,
        "status": status,
        "reason": reason,
        "closed_at": now_iso(),
        "profile_browser_cleanup": cleanup_result,
    })
    status_payload["status"] = status
    status_payload["auto_close"] = auto_close
    write_json(status_path, status_payload)
    with _AUTH_MONITOR_LOCK:
        if _AUTH_MONITOR_IDS.get(platform_id) == monitor_id:
            _AUTH_MONITOR_IDS.pop(platform_id, None)


def _authorization_monitor_loop(
    platform_id: str,
    *,
    browser: Path,
    target_url: str,
    monitor_id: str,
    timeout_sec: int,
    poll_sec: int,
) -> None:
    deadline = time.monotonic() + max(5, int(timeout_sec))
    profile_path = profile_dir(platform_id)
    debug_port = PLATFORM_DEBUG_PORTS.get(platform_id) or 0
    while time.monotonic() < deadline:
        if not _monitor_is_current(platform_id, monitor_id):
            return
        if debug_port and cdp_port_ready(debug_port):
            tested_at = now_iso()
            base = {
                "ok": False,
                "used_mcp": False,
                "stage": "platform_authorization_auto_monitor",
                "platform_id": platform_id,
                "platform_label": PLATFORM_RULES[platform_id].label,
                "url": target_url,
                "profile_dir": str(profile_path),
                "profile_ready": _profile_has_files(profile_path),
                "debug_port": debug_port,
                "debug_port_ready": True,
                "tested_at": tested_at,
                "auth_ok": False,
                "page_access_ok": False,
                "probe_completed": False,
                "write_attempted": False,
            }
            try:
                data, _returncode, stderr = _run_auth_probe_node(
                    platform_id,
                    browser=browser,
                    target_url=target_url,
                    profile_path=profile_path,
                    debug_port=debug_port,
                    timeout_sec=4,
                    current_page_only=True,
                )
                if not data.get("ok") and stderr and not data.get("error"):
                    data["error"] = stderr[-600:]
                payload = _probe_payload_from_data(base, data, profile_ready=base["profile_ready"])
                payload["auto_close_monitor"] = True
                if _monitor_is_current(platform_id, monitor_id):
                    _write_last_auth_test(platform_id, _public_last_test(payload))
                if payload.get("auth_ok") and _monitor_is_current(platform_id, monitor_id):
                    cleanup_result = close_profile_browser_processes(platform_id)
                    _finish_authorization_monitor(
                        platform_id,
                        monitor_id,
                        status="authorization_verified_closed",
                        reason="authorized",
                        cleanup_result=cleanup_result,
                    )
                    return
            except Exception as exc:
                error_payload = {
                    "tested_at": now_iso(),
                    "status": "cdp_unavailable",
                    "label": "浏览器会话不可连接",
                    "auth_ok": False,
                    "page_access_ok": False,
                    "probe_completed": True,
                    "debug_port": debug_port,
                    "profile_ready": _profile_has_files(profile_path),
                    "error": f"{type(exc).__name__}: {exc}",
                    "next_step": "授权窗口监控暂时无法连接浏览器；仍会在超时后自动关闭。",
                }
                if _monitor_is_current(platform_id, monitor_id):
                    _write_last_auth_test(platform_id, _public_last_test(error_payload))
        time.sleep(max(1, int(poll_sec)))

    if not _monitor_is_current(platform_id, monitor_id):
        return
    timeout_payload = {
        "tested_at": now_iso(),
        "status": "authorization_timeout",
        "label": "授权超时",
        "auth_ok": False,
        "page_access_ok": False,
        "probe_completed": True,
        "debug_port": debug_port,
        "profile_ready": _profile_has_files(profile_path),
        "next_step": "90 秒内未检测到授权成功，授权窗口已自动关闭；需要时请重新授权。",
    }
    _write_last_auth_test(platform_id, _public_last_test(timeout_payload))
    cleanup_result = close_profile_browser_processes(platform_id)
    _finish_authorization_monitor(
        platform_id,
        monitor_id,
        status="authorization_timeout_closed",
        reason="timeout",
        cleanup_result=cleanup_result,
    )


def start_authorization_monitor(
    platform_id: str,
    *,
    browser: Path,
    target_url: str,
    monitor_id: str,
    timeout_sec: int = AUTH_WINDOW_TIMEOUT_SEC,
    poll_sec: int = AUTH_WINDOW_POLL_SEC,
) -> dict[str, Any]:
    with _AUTH_MONITOR_LOCK:
        _AUTH_MONITOR_IDS[platform_id] = monitor_id
    thread = threading.Thread(
        target=_authorization_monitor_loop,
        kwargs={
            "platform_id": platform_id,
            "browser": browser,
            "target_url": target_url,
            "monitor_id": monitor_id,
            "timeout_sec": timeout_sec,
            "poll_sec": poll_sec,
        },
        name=f"lucas-auth-monitor-{platform_id}",
        daemon=True,
    )
    thread.start()
    return {
        "enabled": True,
        "status": "watching",
        "timeout_sec": timeout_sec,
        "poll_sec": poll_sec,
        "close_on": "authorized_or_timeout",
        "monitor_id": monitor_id,
    }


def open_authorization(
    platform_id: str,
    *,
    url: str | None = None,
    browser_path: str | None = None,
    dry_run: bool = False,
    force_reopen: bool = False,
) -> dict[str, Any]:
    if platform_id not in PLATFORM_RULES:
        return {
            "ok": False,
            "used_mcp": False,
            "status": "unknown_platform",
            "error": f"Unknown platform: {platform_id}",
        }

    rule = PLATFORM_RULES[platform_id]
    if not rule.auth_required:
        return {
            "ok": True,
            "used_mcp": False,
            "status": "not_required",
            "platform_id": platform_id,
            "message": f"{rule.label} 当前不需要授权会话。",
            "authorization": authorization_status(platform_id),
        }

    browser = find_browser(browser_path)
    status_path = session_status_path(platform_id)
    target_url = safe_auth_url(platform_id, url)
    payload: dict[str, Any] = {
        "ok": False,
        "used_mcp": False,
        "stage": "platform_authorization_open",
        "platform_id": platform_id,
        "platform_label": rule.label,
        "status": "not_started",
        "url": target_url,
        "profile_dir": str(profile_dir(platform_id)),
        "status_path": str(status_path),
        "browser": str(browser) if browser else "",
        "debug_port": PLATFORM_DEBUG_PORTS.get(platform_id),
        "debug_url": f"http://127.0.0.1:{PLATFORM_DEBUG_PORTS[platform_id]}" if platform_id in PLATFORM_DEBUG_PORTS else "",
        "force_reopen": bool(force_reopen),
        "auto_close": {
            "enabled": False if dry_run else True,
            "timeout_sec": AUTH_WINDOW_TIMEOUT_SEC,
            "poll_sec": AUTH_WINDOW_POLL_SEC,
            "close_on": "authorized_or_timeout",
        },
        "error": None,
    }

    if not browser:
        payload.update({"status": "browser_unavailable", "error": "No supported Edge/Chrome executable found."})
        write_json(status_path, payload)
        return payload

    profile_dir(platform_id).mkdir(parents=True, exist_ok=True)
    cmd = browser_args(browser, platform_id, target_url)
    cleanup_result: dict[str, Any] = {
        "ok": True,
        "platform_id": platform_id,
        "matched_processes": 0,
        "closed_processes": 0,
    }
    if force_reopen and not dry_run:
        cleanup_result = close_profile_browser_processes(platform_id)
        if int(cleanup_result.get("closed_processes") or 0) > 0:
            time.sleep(0.8)
    monitor_id = f"{platform_id}-{int(time.time() * 1000)}"
    payload.update({
        "status": "dry_run" if dry_run else "opening_browser",
        "opened_at": "" if dry_run else now_iso(),
        "auth_monitor_id": "" if dry_run else monitor_id,
        "profile_browser_cleanup": cleanup_result,
        "command_preview": [
            "--user-data-dir=<local-platform-profile>" if item.startswith("--user-data-dir=") else item
            for item in cmd
        ],
    })

    if dry_run:
        payload["ok"] = True
        write_json(status_path, payload)
        payload["authorization"] = authorization_status(platform_id)
        return payload

    try:
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=False,
            creationflags=creationflags,
        )
    except Exception as exc:
        payload.update({
            "ok": False,
            "status": "browser_open_failed",
            "error": f"{type(exc).__name__}: {exc}",
        })
        write_json(status_path, payload)
        return payload

    payload.update({
        "ok": True,
        "status": "browser_opened",
        "next_step": f"请在打开的 {rule.label} 浏览器窗口中手动完成登录；授权成功会自动关闭，90 秒未完成也会自动关闭。",
    })
    payload["auto_close"] = {
        "enabled": True,
        "status": "watching",
        "timeout_sec": AUTH_WINDOW_TIMEOUT_SEC,
        "poll_sec": AUTH_WINDOW_POLL_SEC,
        "close_on": "authorized_or_timeout",
        "monitor_id": monitor_id,
    }
    write_json(status_path, payload)
    monitor_payload = start_authorization_monitor(
        platform_id,
        browser=browser,
        target_url=target_url,
        monitor_id=monitor_id,
        timeout_sec=AUTH_WINDOW_TIMEOUT_SEC,
        poll_sec=AUTH_WINDOW_POLL_SEC,
    ) or payload["auto_close"]
    payload["auto_close"] = monitor_payload
    payload["authorization"] = authorization_status(platform_id)
    return payload
