const COMMAND = /^パスワード[\s:：]+(\S{1,200})$/;
const KEY = "pending-password";
const TTL_SECONDS = 7 * 24 * 60 * 60;

const encoder = new TextEncoder();

function base64ToBytes(value) {
  const binary = atob(value);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

async function verifySignature(body, signature, secret) {
  if (!signature) return false;
  let signatureBytes;
  try {
    signatureBytes = base64ToBytes(signature);
  } catch {
    return false;
  }
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  return crypto.subtle.verify("HMAC", key, signatureBytes, encoder.encode(body));
}

async function sha256(value) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(value)));
}

async function safeEqual(a, b) {
  const [left, right] = await Promise.all([sha256(a), sha256(b)]);
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) diff |= left[i] ^ right[i];
  return diff === 0;
}

async function reply(env, replyToken, text) {
  await fetch("https://api.line.me/v2/bot/message/reply", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${env.LINE_CHANNEL_TOKEN}`,
    },
    body: JSON.stringify({ replyToken, messages: [{ type: "text", text }] }),
  });
}

async function handleWebhook(request, env) {
  const body = await request.text();
  const valid = await verifySignature(
    body,
    request.headers.get("x-line-signature"),
    env.LINE_CHANNEL_SECRET,
  );
  if (!valid) return new Response("invalid signature", { status: 401 });

  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    return new Response("bad request", { status: 400 });
  }

  for (const event of payload.events ?? []) {
    if (event.type !== "message" || event.message?.type !== "text") continue;
    if (event.source?.userId !== env.ALLOWED_USER_ID) continue;
    const match = COMMAND.exec(event.message.text.trim());
    if (!match) {
      await reply(env, event.replyToken, "「パスワード 新しいパスワード」の形式で送ってください。");
      continue;
    }
    await env.PASSWORDS.put(KEY, match[1], { expirationTtl: TTL_SECONDS });
    await reply(
      env,
      event.replyToken,
      "受け取りました。次回の定時実行で反映します。トーク履歴のパスワードは削除してください。",
    );
  }
  return new Response("ok");
}

async function handlePull(request, env) {
  const header = request.headers.get("authorization") ?? "";
  if (!(await safeEqual(header, `Bearer ${env.PULL_TOKEN}`))) {
    return new Response("unauthorized", { status: 401 });
  }
  const password = await env.PASSWORDS.get(KEY);
  if (password === null) return new Response(null, { status: 204 });
  await env.PASSWORDS.delete(KEY);
  return Response.json({ password });
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (request.method === "POST" && pathname === "/webhook") return handleWebhook(request, env);
    if (request.method === "POST" && pathname === "/pull") return handlePull(request, env);
    return new Response("not found", { status: 404 });
  },
};
