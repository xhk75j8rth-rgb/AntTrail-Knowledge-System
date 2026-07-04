const DEFAULT_GATEWAY_URL =
  process.env.LUCAS_CHAT_GATEWAY_URL ||
  "http://127.0.0.1:3963/api/chat/messages/async";

function envBool(name, fallback = false) {
  const value = process.env[name];
  if (value === undefined || value === "") return fallback;
  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
}

function normalizeMessage(message) {
  const text = String(message.text ?? message.content ?? "").trim();
  return {
    channel: "wechat",
    conversation_id:
      String(message.conversation_id ?? message.room_id ?? message.from ?? "wechat-bridge"),
    sender_id: String(message.sender_id ?? message.from_user ?? message.from ?? "wechat-user"),
    sender_name: String(message.sender_name ?? message.nickname ?? "WeChat User"),
    message_type: "text",
    text,
    raw_payload: message.raw_payload ?? {},
    metadata: {
      adapter: "external-wechat-bridge",
      bridge_project: process.env.LUCAS_WECHAT_BRIDGE_NAME || "external-wechat-bridge",
      source_message_id: message.message_id ?? message.id ?? null
    },
    timeout_sec: Number(process.env.LUCAS_CHAT_GATEWAY_TIMEOUT_SEC || 3600),
    dry_run: envBool("LUCAS_WECHAT_DRY_RUN", false)
  };
}

export async function forwardWeChatMessage(message, options = {}) {
  const payload = normalizeMessage(message);
  if (!payload.text) {
    return {
      ok: false,
      status: "ignored_empty_text",
      reply_text: "暂时只支持文本或链接消息。"
    };
  }

  const gatewayUrl = options.gatewayUrl || DEFAULT_GATEWAY_URL;
  const response = await fetch(gatewayUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload)
  });

  const body = await response.json().catch(() => ({
    ok: false,
    reply_text: "",
    error: "Gateway returned non-JSON response."
  }));

  if (!response.ok) {
    return {
      ok: false,
      status: "gateway_http_error",
      reply_text: `微信桥已收到消息，但 Chat Gateway 返回 HTTP ${response.status}。`,
      gateway: body
    };
  }

  return {
    ok: Boolean(body.ok),
    status: body.status || "unknown",
    reply_text: body.reply_text || "已收到，正在处理。",
    gateway: body
  };
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const input = process.argv[2] || (await readStdin());
  const message = input.trim().startsWith("{") ? JSON.parse(input) : { text: input };
  const result = await forwardWeChatMessage(message);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  process.exit(result.ok ? 0 : 1);
}
