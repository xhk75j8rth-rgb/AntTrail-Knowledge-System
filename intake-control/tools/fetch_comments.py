#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BROWSER_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]


def write_json(job_dir: Path, payload: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "comments.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, indent=2))


def base_payload(url: str, max_comments: int) -> dict[str, Any]:
    return {
        "ok": True,
        "used_mcp": False,
        "stage": "comments",
        "status": "not_started",
        "login_available": False,
        "url": url,
        "final_url": "",
        "comment_count": 0,
        "max_comments": max_comments,
        "comments": [],
        "signals": [],
        "author_replies": [],
        "response_count": 0,
        "source": "local_browser_cdp",
        "error": None,
    }


def find_browser(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    for candidate in BROWSER_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def infer_login_available(visible_text: str, comments: list[dict[str, Any]]) -> bool:
    if comments:
        return True
    login_markers = ("扫码登录", "登录后", "请登录", "验证码", "手机登录")
    return not any(marker in visible_text for marker in login_markers)


def build_signals(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = [
        ("需求/求资源", ("求", "教程", "链接", "入口", "怎么", "哪里", "有没有", "想要", "能不能", "如何")),
        ("质疑/风险", ("假的", "骗人", "割韭菜", "不靠谱", "真的假的", "风险", "收费", "套路")),
        ("共鸣/认可", ("有用", "学到了", "厉害", "牛", "需要", "正好", "收藏", "感谢")),
        ("落地障碍", ("不会", "太难", "没时间", "做不到", "麻烦", "卡住", "报错")),
    ]
    signals: list[dict[str, Any]] = []
    for category, keywords in groups:
        examples = []
        for comment in comments:
            text = str(comment.get("text") or "")
            if any(keyword in text for keyword in keywords):
                examples.append(text)
        if examples:
            signals.append({"category": category, "count": len(examples), "examples": examples[:3]})
    if comments and not signals:
        signals.append({
            "category": "评论样本",
            "count": len(comments),
            "examples": [str(item.get("text") or "") for item in comments[:3] if item.get("text")],
        })
    return signals


def node_cdp_script() -> str:
    return r"""
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const browserPath = process.argv[1];
const targetUrl = process.argv[2];
const maxComments = Number(process.argv[3] || 30);
const profileDirArg = process.argv[4] || '';
const headed = process.argv[5] === '1';
const timeoutMs = Number(process.argv[6] || 60000);
const port = 9300 + Math.floor(Math.random() * 500);
const userDataDir = profileDirArg || path.join(os.tmpdir(), 'LucasCommentBrowser', `profile-${Date.now()}-${Math.random().toString(16).slice(2)}`);
fs.mkdirSync(userDataDir, { recursive: true });

const browserArgs = [
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${userDataDir}`,
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-background-networking',
  '--disable-sync',
  '--autoplay-policy=user-gesture-required',
  '--lang=zh-CN',
  targetUrl,
];
if (!headed) {
  browserArgs.push('--headless=new', '--disable-gpu');
}

const browser = spawn(browserPath, browserArgs, { stdio: ['ignore', 'ignore', 'pipe'] });
let browserStderr = '';
browser.stderr.on('data', chunk => {
  browserStderr += chunk.toString();
  if (browserStderr.length > 4000) browserStderr = browserStderr.slice(-4000);
});

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
    await sleep(300);
  }
  throw new Error(`CDP endpoint unavailable: ${endpoint}; ${lastError}`);
}

function makeCdp(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const pending = new Map();
    const listeners = [];
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
        onEvent(fn) {
          listeners.push(fn);
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
        return;
      }
      if (msg.method) {
        for (const listener of listeners) listener(msg);
      }
    };
  });
}

function mediaPlaybackGuardScript() {
  return `(() => {
    if (window.__lucasMediaPlaybackGuardInstalled) return true;
    window.__lucasMediaPlaybackGuardInstalled = true;
    const stopMedia = media => {
      try { media.muted = true; } catch (_) {}
      try { media.autoplay = false; } catch (_) {}
      try { media.removeAttribute('autoplay'); } catch (_) {}
      try { media.preload = 'metadata'; } catch (_) {}
      try { media.pause(); } catch (_) {}
    };
    const stopAllMedia = () => {
      for (const media of document.querySelectorAll('video,audio')) stopMedia(media);
    };
    const originalPlay = HTMLMediaElement.prototype.play;
    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value: function lucasBlockedPlay() {
        stopMedia(this);
        return Promise.resolve();
      },
    });
    document.addEventListener('play', event => {
      if (event && event.target instanceof HTMLMediaElement) stopMedia(event.target);
    }, true);
    const observer = new MutationObserver(stopAllMedia);
    observer.observe(document.documentElement || document, { childList: true, subtree: true });
    stopAllMedia();
    window.__lucasRestoreMediaPlay = () => {
      try { HTMLMediaElement.prototype.play = originalPlay; } catch (_) {}
      try { observer.disconnect(); } catch (_) {}
    };
    return true;
  })()`;
}

function isCommentUrl(url) {
  return /\/comment\/list|aweme\/v1\/web\/comment|comment\/reply/i.test(url || '');
}

function addComment(target, seen, raw) {
  if (!raw || typeof raw !== 'object') return;
  const user = raw.user || raw.author || {};
  const text = String(raw.text || raw.content || raw.comment_text || '').replace(/\s+/g, ' ').trim();
  if (!text || text.length < 2) return;
  const id = String(raw.cid || raw.comment_id || raw.id || `${user.uid || user.sec_uid || user.nickname || ''}:${text.slice(0, 60)}`);
  if (seen.has(id)) return;
  seen.add(id);
  target.push({
    id,
    text: text.slice(0, 500),
    author: String(user.nickname || user.unique_id || user.short_id || ''),
    like_count: Number(raw.digg_count || raw.like_count || 0),
    reply_count: Number(raw.reply_comment_total || raw.reply_count || 0),
    create_time: raw.create_time || raw.createTime || null,
  });
}

function collectComments(target, seen, value, depth = 0) {
  if (!value || depth > 5 || target.length >= maxComments) return;
  if (Array.isArray(value)) {
    for (const item of value) {
      addComment(target, seen, item);
      collectComments(target, seen, item, depth + 1);
      if (target.length >= maxComments) return;
    }
    return;
  }
  if (typeof value !== 'object') return;
  for (const [key, child] of Object.entries(value)) {
    if (key === 'comments' || key === 'comment_list' || key === 'reply_comments') {
      collectComments(target, seen, child, depth + 1);
    }
  }
}

(async () => {
  const comments = [];
  const seen = new Set();
  const commentRequests = new Map();
  const responseUrls = [];
  let cdp;
  try {
    const targets = await fetchJson('/json/list', 15000);
    const pageTarget = targets.find(t => t.type === 'page' && t.webSocketDebuggerUrl);
    if (!pageTarget) throw new Error('No CDP page target found');
    cdp = await makeCdp(pageTarget.webSocketDebuggerUrl);
    cdp.onEvent(msg => {
      if (msg.method === 'Network.responseReceived') {
        const params = msg.params || {};
        const url = params.response && params.response.url || '';
        if (isCommentUrl(url)) {
          commentRequests.set(params.requestId, url);
          responseUrls.push(url);
        }
      }
      if (msg.method === 'Network.loadingFinished') {
        const requestId = msg.params && msg.params.requestId;
        if (!commentRequests.has(requestId)) return;
        cdp.send('Network.getResponseBody', { requestId }).then(body => {
          let text = body.body || '';
          if (body.base64Encoded) text = Buffer.from(text, 'base64').toString('utf8');
          try {
            const data = JSON.parse(text);
            collectComments(comments, seen, data);
          } catch (_) {}
        }).catch(() => {});
      }
    });

    await cdp.send('Network.enable');
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
      source: mediaPlaybackGuardScript(),
    }).catch(() => {});
    await cdp.send('Page.navigate', { url: targetUrl });
    await sleep(6000);
    await cdp.send('Runtime.evaluate', {
      expression: mediaPlaybackGuardScript(),
      returnByValue: true,
    }).catch(() => {});
    await cdp.send('Runtime.evaluate', {
      expression: `(() => {
        for (const el of document.querySelectorAll('button, div, span')) {
          const t = (el.innerText || '').trim();
          if (t === '评论' || t.startsWith('评论 ')) { try { el.click(); } catch (_) {} break; }
        }
        return true;
      })()`,
      awaitPromise: true,
    }).catch(() => {});

    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline && comments.length < maxComments) {
      await cdp.send('Runtime.evaluate', {
        expression: `(() => {
          window.scrollBy(0, 700);
          for (const el of document.querySelectorAll('*')) {
            const style = getComputedStyle(el);
            if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
              el.scrollTop += 700;
            }
          }
          return true;
        })()`,
        awaitPromise: true,
      }).catch(() => {});
      await sleep(1500);
    }

    await sleep(1000);
    const pageData = await cdp.send('Runtime.evaluate', {
      expression: `(() => ({
        final_url: location.href,
        title: document.title || '',
        visible_text: (document.body && document.body.innerText || '').slice(0, 3000)
      }))()`,
      returnByValue: true,
    }).catch(() => ({ result: { value: {} } }));
    await cdp.send('Runtime.evaluate', {
      expression: `(() => {
        for (const media of document.querySelectorAll('video,audio')) {
          try { media.pause(); media.muted = true; media.removeAttribute('autoplay'); } catch (_) {}
        }
        return true;
      })()`,
      returnByValue: true,
    }).catch(() => {});
    cdp.close();
    browser.kill();
    console.log(JSON.stringify({
      ok: true,
      final_url: (pageData.result && pageData.result.value && pageData.result.value.final_url) || '',
      title: (pageData.result && pageData.result.value && pageData.result.value.title) || '',
      visible_text: (pageData.result && pageData.result.value && pageData.result.value.visible_text) || '',
      response_count: responseUrls.length,
      response_url_samples: responseUrls.slice(0, 5),
      comments: comments.slice(0, maxComments),
      profile_dir_used: profileDirArg ? profileDirArg : '',
      temp_profile_used: !profileDirArg,
      browser_stderr_tail: browserStderr.slice(-1200),
    }));
  } catch (err) {
    if (cdp) cdp.close();
    browser.kill();
    console.log(JSON.stringify({
      ok: false,
      error: String(err && err.message || err),
      browser_stderr_tail: browserStderr.slice(-1200),
      profile_dir_used: profileDirArg ? profileDirArg : '',
      temp_profile_used: !profileDirArg,
    }));
    process.exit(1);
  }
})();
"""


def run_cdp_fetch(
    browser: Path,
    url: str,
    max_comments: int,
    timeout_sec: int,
    profile_dir: str,
    headed: bool,
) -> tuple[int, dict[str, Any], str]:
    completed = subprocess.run(
        [
            "node",
            "-e",
            node_cdp_script(),
            str(browser),
            url,
            str(max_comments),
            profile_dir,
            "1" if headed else "0",
            str(timeout_sec * 1000),
        ],
        cwd=str(PROJECT_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_sec + 20,
    )
    try:
        data = json.loads((completed.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        data = {"ok": False, "error": f"Invalid CDP JSON: {(completed.stdout or '')[-800:]}"}
    return completed.returncode, data, completed.stderr or ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch public Douyin comments with a local browser CDP session; no MCP.")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--max-comments", type=int, default=30)
    parser.add_argument("--timeout-sec", type=int, default=60)
    parser.add_argument("--browser-path")
    parser.add_argument("--profile-dir", default=os.environ.get("DOUYIN_BROWSER_PROFILE", ""))
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    job_dir = Path(args.job_dir).resolve()
    max_comments = max(1, min(args.max_comments, 50))
    payload = base_payload(args.url, max_comments)
    browser = find_browser(args.browser_path)
    if not browser:
        payload.update({"status": "browser_unavailable", "error": "No supported Edge/Chrome executable found."})
        write_json(job_dir, payload)
        emit_json(payload)
        return 0

    exit_code, data, stderr = run_cdp_fetch(browser, args.url, max_comments, args.timeout_sec, args.profile_dir, args.headed)
    comments = data.get("comments") if isinstance(data.get("comments"), list) else []
    visible_text = str(data.get("visible_text") or "")
    signals = build_signals(comments)
    status = "comments_fetched" if comments else (
        "skipped_no_comment_api_capture" if exit_code == 0 else "comment_fetch_failed"
    )
    payload.update({
        "ok": True,
        "status": status,
        "login_available": infer_login_available(visible_text, comments),
        "final_url": data.get("final_url") or "",
        "title": data.get("title") or "",
        "comment_count": len(comments),
        "comments": comments,
        "signals": signals,
        "response_count": int(data.get("response_count") or 0),
        "profile_dir_used": data.get("profile_dir_used") or "",
        "temp_profile_used": bool(data.get("temp_profile_used")),
        "browser": str(browser),
        "error": None if exit_code == 0 else data.get("error") or stderr[-1000:],
        "browser_stderr_tail": str(data.get("browser_stderr_tail") or "")[-1000:],
    })
    write_json(job_dir, payload)
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
