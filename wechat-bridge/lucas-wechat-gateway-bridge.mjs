#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const DEFAULT_GATEWAY_URL =
  process.env.LUCAS_CHAT_GATEWAY_URL ||
  "http://127.0.0.1:3963/api/chat/messages/async";
const BRIDGE_NAME = process.env.LUCAS_WECHAT_BRIDGE_NAME || "lucas-wechat-gateway-bridge";
const GATEWAY_TIMEOUT_SEC = parsePositiveInt(process.env.LUCAS_CHAT_GATEWAY_TIMEOUT_SEC, 3600);
const GATEWAY_HTTP_TIMEOUT_MS = parsePositiveInt(process.env.LUCAS_CHAT_GATEWAY_HTTP_TIMEOUT_MS, 300_000);
const BATCH_POLL_INTERVAL_MS = parsePositiveInt(process.env.LUCAS_WECHAT_BATCH_POLL_INTERVAL_MS, 2_500);
const BATCH_TIMEOUT_MS = parsePositiveInt(
  process.env.LUCAS_WECHAT_BATCH_TIMEOUT_MS,
  Math.max(60_000, (GATEWAY_TIMEOUT_SEC + 60) * 1000),
);
const MAX_REPLY_CHARS = parsePositiveInt(process.env.LUCAS_WECHAT_REPLY_MAX_CHARS, 3500);
const MAX_CONCURRENT_MESSAGES = parsePositiveInt(process.env.LUCAS_WECHAT_MAX_CONCURRENT_MESSAGES, 3);
const MESSAGE_START_GRACE_MS = parsePositiveInt(process.env.LUCAS_WECHAT_MESSAGE_START_GRACE_MS, 5000);
const POLL_RETRY_BASE_MS = 1000;
const POLL_RETRY_MAX_MS = 30_000;
const SEND_RETRY_ATTEMPTS = 3;
const currentFile = fileURLToPath(import.meta.url);
const bridgeRoot = path.dirname(currentFile);
const require = createRequire(import.meta.url);
const CLI_WECHAT_BRIDGE_ROOT = resolveCliWechatBridgeRoot();

const {
  ensureWechatCredentials,
  loadExistingCredentials,
  runWechatLogin,
} = await importCliWechatBridgeModule("dist/wechat/setup.js");
const {
  DEFAULT_LONG_POLL_TIMEOUT_MS,
  WeChatTransport,
  classifyWechatTransportError,
  describeWechatTransportError,
  isWechatContextTokenStaleError,
} = await importCliWechatBridgeModule("dist/wechat/wechat-transport.js");

function addCliWechatBridgeRootCandidate(candidates, value) {
  const text = String(value || "").trim();
  if (!text) return;
  candidates.push(text);
  if (!text.toLowerCase().endsWith(`${path.sep}cli-wechat-bridge`) && !text.toLowerCase().endsWith("/cli-wechat-bridge")) {
    candidates.push(path.join(text, "cli-wechat-bridge"));
  }
}

function cliWechatBridgeRootCandidates() {
  const candidates = [];
  for (const value of [
    process.env.LUCAS_CLI_WECHAT_BRIDGE_ROOT,
    process.env.CLI_WECHAT_BRIDGE_ROOT,
    path.join(bridgeRoot, "vendor", "cli-wechat-bridge"),
  ]) {
    addCliWechatBridgeRootCandidate(candidates, value);
  }

  try {
    addCliWechatBridgeRootCandidate(candidates, path.dirname(require.resolve("cli-wechat-bridge/package.json")));
  } catch {
    // The package is often installed globally and therefore not visible to normal ESM resolution.
  }

  addCliWechatBridgeRootCandidate(candidates, path.join(path.dirname(process.execPath), "node_modules", "cli-wechat-bridge"));
  for (const value of [process.env.NVM_SYMLINK, process.env.NVM_HOME]) {
    if (value) {
      addCliWechatBridgeRootCandidate(candidates, path.join(value, "node_modules", "cli-wechat-bridge"));
    }
  }
  if (process.env.APPDATA) {
    addCliWechatBridgeRootCandidate(candidates, path.join(process.env.APPDATA, "npm", "node_modules", "cli-wechat-bridge"));
  }
  if (process.env.LOCALAPPDATA) {
    addCliWechatBridgeRootCandidate(candidates, path.join(process.env.LOCALAPPDATA, "nvm", `v${process.versions.node}`, "node_modules", "cli-wechat-bridge"));
  }
  for (const nodePathEntry of String(process.env.NODE_PATH || "").split(path.delimiter)) {
    addCliWechatBridgeRootCandidate(candidates, nodePathEntry);
  }
  return candidates;
}

function resolveCliWechatBridgeRoot() {
  const checked = [];
  const seen = new Set();
  for (const candidate of cliWechatBridgeRootCandidates()) {
    const root = path.resolve(candidate);
    const key = root.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    const setupPath = path.join(root, "dist", "wechat", "setup.js");
    const transportPath = path.join(root, "dist", "wechat", "wechat-transport.js");
    checked.push(root);
    if (fs.existsSync(setupPath) && fs.existsSync(transportPath)) {
      return root;
    }
  }

  throw new Error(
    [
      "CLI-WeChat-Bridge runtime not found.",
      "Expected dist/wechat/setup.js and dist/wechat/wechat-transport.js under one of:",
      ...checked.map((item) => `  - ${item}`),
      "Install cli-wechat-bridge globally, restore wechat-bridge/vendor/cli-wechat-bridge, or set LUCAS_CLI_WECHAT_BRIDGE_ROOT.",
    ].join("\n"),
  );
}

async function importCliWechatBridgeModule(relativePath) {
  return import(pathToFileURL(path.join(CLI_WECHAT_BRIDGE_ROOT, relativePath)).href);
}

function parsePositiveInt(value, fallback) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function envBool(name, fallback = false) {
  const value = process.env[name];
  if (value === undefined || value === "") return fallback;
  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function log(message) {
  process.stdout.write(`[lucas-wechat] ${message}\n`);
}

function logError(message) {
  process.stderr.write(`[lucas-wechat] ERROR: ${message}\n`);
}

function loadQrTerminal() {
  const candidates = [
    process.env.LUCAS_QRCODE_TERMINAL_MODULE,
    "qrcode-terminal",
    path.join(bridgeRoot, "vendor", "cli-wechat-bridge", "node_modules", "qrcode-terminal"),
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      const loaded = require(candidate);
      return loaded.default || loaded;
    } catch {
      // Try the next known install location.
    }
  }
  return null;
}

function writeQrToStdout(qrContent) {
  const qrterm = loadQrTerminal();
  if (!qrterm?.generate) {
    process.stdout.write(`Open this QR code URL in a browser: ${qrContent}\n\n`);
    return;
  }
  qrterm.generate(qrContent, { small: true }, (qr) => {
    process.stdout.write(`${qr}\n`);
  });
}

function truncateReply(text) {
  const value = String(text ?? "").trim();
  if (!value) return "";
  if (Array.from(value).length <= MAX_REPLY_CHARS) return value;
  return `${Array.from(value).slice(0, Math.max(0, MAX_REPLY_CHARS - 20)).join("")}\n...(truncated)`;
}

function responseTextForGatewayError(status, body) {
  const detail = body?.detail || body?.error || body?.message || "";
  return `微信桥已收到消息，但 Lucas Chat Gateway 返回 HTTP ${status}${detail ? `：${detail}` : ""}`;
}

function normalizeMessageForGateway(message) {
  const text = String(message.text ?? "").trim();
  return {
    channel: "wechat",
    conversation_id: String(message.sessionId || message.senderId || "wechat-bridge"),
    sender_id: String(message.senderId || "wechat-user"),
    sender_name: String(message.sender || "WeChat User"),
    message_type: "text",
    text,
    raw_payload: {
      created_at: message.createdAt ?? null,
      created_at_ms: message.createdAtMs ?? null,
      session_id: message.sessionId ?? null,
      attachment_count: Array.isArray(message.attachments) ? message.attachments.length : 0,
    },
    metadata: {
      wechat_bridge: BRIDGE_NAME,
      bridge_project: "CLI-WeChat-Bridge transport",
      bridge_mode: "chat_gateway",
      source_message_created_at: message.createdAt ?? null,
      has_attachments: Array.isArray(message.attachments) && message.attachments.length > 0,
    },
    timeout_sec: GATEWAY_TIMEOUT_SEC,
  };
}

async function fetchJsonWithTimeout(url, options = {}, timeoutMs = GATEWAY_HTTP_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
    });
    const text = await response.text();
    let body = {};
    try {
      body = text ? JSON.parse(text) : {};
    } catch {
      body = { ok: false, error: text || "Non-JSON response" };
    }
    return { response, body };
  } finally {
    clearTimeout(timer);
  }
}

async function postToGateway(message) {
  const payload = normalizeMessageForGateway(message);
  if (!payload.text) {
    return {
      ok: false,
      status: "ignored_empty_text",
      reply_text: "暂时只支持文本消息；图片和文件入口还没有接入微信桥。",
      data: { async: false },
    };
  }

  const { response, body } = await fetchJsonWithTimeout(DEFAULT_GATEWAY_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    return {
      ok: false,
      status: "gateway_http_error",
      reply_text: responseTextForGatewayError(response.status, body),
      data: { async: false },
      gateway: body,
    };
  }

  return body;
}

function resolvePollUrl(pollUrl) {
  try {
    return new URL(pollUrl, DEFAULT_GATEWAY_URL).toString();
  } catch {
    return "";
  }
}

function isBatchFinished(body) {
  const queue = body?.data?.queue;
  if (!queue || typeof queue !== "object") return false;
  const unfinished = Number(queue.unfinished ?? 0);
  const status = String(queue.status || body.status || "");
  return unfinished <= 0 || ["completed", "failed", "partial_failed"].includes(status);
}

async function pollGatewayBatch(pollUrl) {
  const resolvedUrl = resolvePollUrl(pollUrl);
  if (!resolvedUrl) {
    return {
      ok: false,
      status: "batch_poll_url_invalid",
      reply_text: "后台队列已创建，但微信桥拿到的轮询地址无效。",
    };
  }

  const deadline = Date.now() + BATCH_TIMEOUT_MS;
  let lastBody = null;
  let lastLogKey = "";
  while (Date.now() < deadline) {
    await delay(BATCH_POLL_INTERVAL_MS);
    try {
      const { response, body } = await fetchJsonWithTimeout(resolvedUrl, { method: "GET" }, 60_000);
      if (!response.ok) {
        lastBody = {
          ok: false,
          status: "batch_http_error",
          reply_text: responseTextForGatewayError(response.status, body),
        };
        continue;
      }
      lastBody = body;
      const queue = body?.data?.queue || {};
      const logKey = `${queue.status || body.status || "unknown"}:${queue.completed || 0}:${queue.unfinished || 0}:${queue.failed || 0}`;
      if (logKey !== lastLogKey) {
        lastLogKey = logKey;
        log(`batch ${logKey}`);
      }
      if (isBatchFinished(body)) {
        return body;
      }
    } catch (error) {
      lastBody = {
        ok: false,
        status: "batch_poll_error",
        reply_text: `微信桥轮询后台队列失败：${error instanceof Error ? error.message : String(error)}`,
      };
    }
  }

  return {
    ok: false,
    status: "batch_poll_timeout",
    reply_text:
      lastBody?.reply_text ||
      "链接任务还在后台运行，但微信桥等待最终回执超时。可以在 Lucas 页面查看任务队列或稍后再问我结果。",
    data: lastBody?.data || {},
  };
}

async function safeSendText(transport, senderId, text, context = "reply") {
  const reply = truncateReply(text);
  if (!reply) return false;
  for (let attempt = 1; attempt <= SEND_RETRY_ATTEMPTS; attempt += 1) {
    try {
      await transport.sendText(senderId, reply);
      return true;
    } catch (error) {
      if (isWechatContextTokenStaleError(error)) {
        transport.clearCachedContextToken(senderId);
        logError(
          `Failed to send ${context}: WeChat conversation context is stale. Ask the WeChat owner to send any message first, then retry.`,
        );
        return false;
      }
      const classification = classifyWechatTransportError(error);
      if (attempt < SEND_RETRY_ATTEMPTS && classification.retryable) {
        await delay(750 * attempt);
        continue;
      }
      logError(`Failed to send ${context}: ${describeWechatTransportError(error)}`);
      return false;
    }
  }
  return false;
}

function initialAsyncReply(body) {
  const urls = body?.data?.urls;
  const count = Array.isArray(urls) ? urls.length : 1;
  return count > 1
    ? `收到 ${count} 个链接，Lucas 正在后台整理和入库；完成后我会把结果发回来。`
    : "收到链接，Lucas 正在后台整理和入库；完成后我会把摘要和写入结果发回来。";
}

async function handleGatewayResult(transport, message, result) {
  const isAsync = Boolean(result?.data?.async);
  if (!isAsync) {
    await safeSendText(
      transport,
      message.senderId,
      result?.reply_text || result?.error || "Lucas 已收到消息，但没有返回可显示内容。",
      "chat_gateway_reply",
    );
    return;
  }

  const pollUrl = result?.data?.poll_url;
  await safeSendText(transport, message.senderId, initialAsyncReply(result), "chat_gateway_batch_started");
  if (!pollUrl) {
    await safeSendText(
      transport,
      message.senderId,
      result?.reply_text || "后台队列已创建，但没有返回轮询地址。",
      "chat_gateway_batch_missing_poll_url",
    );
    return;
  }

  const finalResult = await pollGatewayBatch(pollUrl);
  await safeSendText(
    transport,
    message.senderId,
    finalResult?.reply_text || finalResult?.error || "后台任务已结束，但没有返回可显示内容。",
    "chat_gateway_batch_final",
  );
}

async function handleMessage(transport, credentials, message) {
  if (message.senderId !== credentials.userId) {
    await safeSendText(transport, message.senderId, "Unauthorized. This bridge only accepts messages from the configured WeChat owner.", "unauthorized");
    return;
  }

  log(`received owner message chars=${Array.from(String(message.text || "")).length}`);
  try {
    const result = await postToGateway(message);
    await handleGatewayResult(transport, message, result);
  } catch (error) {
    const messageText = error instanceof Error ? error.message : String(error);
    logError(`Chat Gateway dispatch failed: ${messageText}`);
    await safeSendText(transport, message.senderId, `微信桥转发到 Lucas Chat Gateway 失败：${messageText}`, "gateway_dispatch_error");
  }
}

async function waitForCapacity(pendingTasks) {
  while (pendingTasks.size >= MAX_CONCURRENT_MESSAGES) {
    await Promise.race([...pendingTasks]);
  }
}

async function main() {
  const existing = loadExistingCredentials();
  const forceRelogin = envBool("LUCAS_WECHAT_FORCE_RELOGIN", false);
  log("Lucas WeChat Chat Gateway bridge starting.");
  log(`gateway_url=${DEFAULT_GATEWAY_URL}`);
  log("mode=chat_gateway");
  log("handles=links_and_plain_text");
  log(`force_relogin=${forceRelogin ? "yes" : "no"}`);
  log("note=Messages are sent to Lucas Chat Gateway. No terminal assistant or shell runtime is started.");
  if (forceRelogin) {
    log("saved_wechat_login=ignored_by_user_request; QR code login will start.");
  } else if (existing) {
    log("saved_wechat_login=present; QR will not be shown unless the saved login is invalid or expired.");
  } else {
    log("saved_wechat_login=missing; a QR code or QR URL should be printed below.");
  }

  const credentials = forceRelogin
    ? await runWechatLogin({
        requireUserId: true,
        log,
        write: (chunk) => {
          const text = String(chunk ?? "");
          const match = text.match(/Open this QR code URL in a browser:\s*(https?:\/\/\S+)/i);
          if (match) {
            writeQrToStdout(match[1]);
            return;
          }
          process.stdout.write(text);
        },
      })
    : await ensureWechatCredentials({
        requireUserId: true,
        validateExisting: true,
        log,
        write: (chunk) => process.stdout.write(chunk),
      });
  if (!credentials.userId) {
    throw new Error("Saved WeChat credentials are missing userId.");
  }

  const transport = new WeChatTransport({ log, logError });
  const startedAtMs = Date.now();
  const pendingTasks = new Set();
  let running = true;
  let consecutivePollFailures = 0;

  const requestShutdown = (reason) => {
    if (!running) return;
    running = false;
    log(`${reason} Stopping Lucas WeChat Chat Gateway bridge.`);
  };

  process.once("SIGINT", () => requestShutdown("Received SIGINT."));
  process.once("SIGTERM", () => requestShutdown("Received SIGTERM."));
  if (process.platform === "win32") {
    process.once("SIGBREAK", () => requestShutdown("Received SIGBREAK."));
  }

  log("WeChat Chat Gateway bridge is ready.");
  log("Send a normal message for database/RAG chat, or send a URL for automatic intake.");

  while (running) {
    let pollResult;
    try {
      pollResult = await transport.pollMessages({
        timeoutMs: DEFAULT_LONG_POLL_TIMEOUT_MS,
        minCreatedAtMs: startedAtMs - MESSAGE_START_GRACE_MS,
      });
      if (consecutivePollFailures > 0) {
        log(`WeChat long poll recovered after ${consecutivePollFailures} transient error(s).`);
        consecutivePollFailures = 0;
      }
    } catch (error) {
      const classification = classifyWechatTransportError(error);
      if (!classification.retryable) {
        throw error;
      }
      consecutivePollFailures += 1;
      const delayMs = Math.min(POLL_RETRY_MAX_MS, POLL_RETRY_BASE_MS * 2 ** Math.min(consecutivePollFailures - 1, 5));
      logError(`WeChat long poll failed (${classification.kind}); retrying in ${delayMs}ms. ${describeWechatTransportError(error)}`);
      await delay(delayMs);
      continue;
    }

    if (pollResult.ignoredBacklogCount > 0) {
      log(`ignored_startup_backlog=${pollResult.ignoredBacklogCount}`);
    }

    for (const message of pollResult.messages) {
      await waitForCapacity(pendingTasks);
      const task = handleMessage(transport, credentials, message)
        .catch((error) => {
          logError(`Unhandled message task failure: ${describeWechatTransportError(error)}`);
        })
        .finally(() => {
          pendingTasks.delete(task);
        });
      pendingTasks.add(task);
    }
  }

  if (pendingTasks.size > 0) {
    log(`Waiting for ${pendingTasks.size} in-flight message task(s) to finish.`);
    await Promise.allSettled([...pendingTasks]);
  }
}

const invokedFile = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invokedFile && path.resolve(currentFile) === invokedFile) {
  main().catch((error) => {
    logError(describeWechatTransportError(error));
    process.exit(1);
  });
}
