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

const FORM_HTML = `<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>パスワード更新</title>
<style>
body{font-family:sans-serif;margin:0;padding:24px;background:#f5f5f5}
h1{font-size:18px}input,button{width:100%;box-sizing:border-box;font-size:16px;padding:12px;margin-top:12px}
button{background:#06c755;color:#fff;border:0;border-radius:6px}#msg{margin-top:16px;white-space:pre-wrap}
</style></head><body>
<h1>関東・東海レポートのパスワード更新</h1>
<form id="f"><input id="pw" type="password" autocomplete="off" placeholder="新しいパスワード" required maxlength="200">
<button id="b" type="submit">送信</button></form><div id="msg"></div>
<script src="https://static.line-scdn.net/liff/edge/2/sdk.js"></script>
<script>
const msg = document.getElementById("msg");
const ready = liff.init({ liffId: "__LIFF_ID__" }).then(() => {
  if (!liff.isLoggedIn()) liff.login();
});
document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = document.getElementById("b");
  button.disabled = true;
  try {
    await ready;
    const res = await fetch("/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + liff.getAccessToken() },
      body: JSON.stringify({ password: document.getElementById("pw").value }),
    });
    if (!res.ok) throw new Error(await res.text());
    msg.textContent = "受け取りました。次回の定時実行で反映します。";
    document.getElementById("pw").value = "";
    setTimeout(() => liff.closeWindow(), 1500);
  } catch (err) {
    msg.textContent = "失敗しました: " + err.message;
    button.disabled = false;
  }
});
</script></body></html>`;

function handleForm(env) {
  return new Response(FORM_HTML.replace("__LIFF_ID__", env.LIFF_ID), {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Frame-Options": "DENY",
    },
  });
}

async function handleSubmit(request, env) {
  const accessToken = (request.headers.get("authorization") ?? "").replace(/^Bearer /, "");
  if (!accessToken) return new Response("unauthorized", { status: 401 });

  const verify = await fetch(
    `https://api.line.me/oauth2/v2.1/verify?access_token=${encodeURIComponent(accessToken)}`,
  );
  if (!verify.ok) return new Response("unauthorized", { status: 401 });
  const info = await verify.json();
  if (String(info.client_id) !== env.LIFF_CHANNEL_ID || info.expires_in <= 0) {
    return new Response("unauthorized", { status: 401 });
  }

  const profile = await fetch("https://api.line.me/v2/profile", {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!profile.ok) return new Response("unauthorized", { status: 401 });
  if ((await profile.json()).userId !== env.ALLOWED_USER_ID) {
    return new Response("forbidden", { status: 403 });
  }

  let password;
  try {
    ({ password } = await request.json());
  } catch {
    return new Response("bad request", { status: 400 });
  }
  if (typeof password !== "string" || !/^\S{1,200}$/.test(password)) {
    return new Response("パスワードは空白なしの1〜200文字にしてください", { status: 400 });
  }
  await env.PASSWORDS.put(KEY, password, { expirationTtl: TTL_SECONDS });
  return new Response("ok");
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (request.method === "GET" && pathname === "/form") return handleForm(env);
    if (request.method === "POST" && pathname === "/submit") return handleSubmit(request, env);
    if (request.method === "POST" && pathname === "/webhook") return handleWebhook(request, env);
    if (request.method === "POST" && pathname === "/pull") return handlePull(request, env);
    return new Response("not found", { status: 404 });
  },
};
