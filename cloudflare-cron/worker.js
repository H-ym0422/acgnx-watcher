import { recordAttempt } from './scheduler-health.js';

const worker = {
  async scheduled(event, env, ctx) {
    const r = await wake(env);
    console.log(`${new Date().toISOString()} cron=${event.cron} -> ${r.status}`);
    try {
      await recordAttempt(env, r, event.scheduledTime, sendFeishu);
    } catch {
      console.error("Scheduler health could not be saved; dispatch results must be checked in logs.");
    }
  },
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/feishu") {
      return handleFeishu(request, env);
    }
    if (url.pathname === "/ping") {
      return new Response("pong " + new Date().toISOString() + "\n", { status: 200 });
    }
    if (!env.SECRET || url.searchParams.get("k") !== env.SECRET) {
      return new Response("要带上 ?k=你的密钥 才会触发\n", { status: 403 });
    }
    const r = await wake(env);
    return new Response(`${r.status} ${r.body}
`, { status: 200 });
  }
};
async function handleFeishu(request, env) {
  if (request.method !== "POST") {
    return new Response("ok\n", { status: 200 });
  }
  let payload;
  try {
    payload = await request.json();
  } catch {
    return new Response("bad json\n", { status: 400 });
  }
  if (payload.challenge) {
    console.log("URL 校验 challenge:", payload.challenge);
    return json({ challenge: payload.challenge });
  }
  const hdr = payload.header || {};
  const token = hdr.token || payload.token || "";
  if (env.FEISHU_VERIFY_TOKEN && token !== env.FEISHU_VERIFY_TOKEN) {
    console.log("拒绝：Verification Token 不对");
    return new Response("bad token\n", { status: 401 });
  }
  const et = hdr.event_type || "(未知)";
  console.log(`收到事件: ${et}`);
  if (et === "im.message.message_read_v1") {
    const ids = (payload.event || {}).message_id_list || [];
    console.log(`已读回执 ${ids.length} 条: ${ids.join(", ")}`);
    const res = await markReadOnGitHub(env, ids);
    console.log("写回仓库:", JSON.stringify(res));
  }
  return new Response("ok\n", { status: 200 });
}
function json(obj) {
  return new Response(
    JSON.stringify(obj),
    { headers: { "Content-Type": "application/json" } }
  );
}
async function markReadOnGitHub(env, ids) {
  if (!ids.length) return { ok: false, why: "没有 message_id" };
  if (!env.GH_TOKEN) return { ok: false, why: "GH_TOKEN 没配" };
  const [owner, repo] = (env.REPO || "").split("/");
  if (!owner || !repo) return { ok: false, why: "REPO 格式不对" };
  const api = "https://api.github.com";
  const H = {
    Authorization: `Bearer ${env.GH_TOKEN.trim()}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "acgnx-readreceipt",
    "Content-Type": "application/json"
  };
  try {
    let r = await fetch(`${api}/repos/${owner}/${repo}/contents/state.json`, { headers: H });
    if (r.status === 404) return { ok: false, why: "仓库里没有 state.json" };
    if (r.status === 403) return { ok: false, why: "令牌没有 contents:write 权限" };
    if (!r.ok) return { ok: false, why: `读文件 HTTP ${r.status}` };
    const file = await r.json();
    const st = JSON.parse(atob(file.content).replace(/\n/g, ""));
    let hit = 0;
    for (const [key, v] of Object.entries(st.unread || {})) {
      if (ids.includes(v.mid)) {
        delete st.unread[key];
        hit++;
      }
    }
    if (!hit) return { ok: true, hit: 0, why: "没有匹配的未读项" };
    r = await fetch(`${api}/repos/${owner}/${repo}/contents/state.json`, {
      method: "PUT",
      headers: H,
      body: JSON.stringify({
        message: `已读回执 ${hit} 条`,
        content: btoa(unescape(encodeURIComponent(JSON.stringify(st, null, 2)))),
        sha: file.sha,
        branch: "main"
      })
    });
    return r.ok ? { ok: true, hit } : { ok: false, why: `写回 HTTP ${r.status}` };
  } catch (e) {
    return { ok: false, why: String(e).slice(0, 120) };
  }
}
export async function wake(env) {
  if (!env.GH_TOKEN?.trim() || !env.REPO || !env.WORKFLOW) {
    return {
      ok: false,
      status: 0,
      body: "环境变量没配全：GH_TOKEN / REPO / WORKFLOW 三个都要填"
    };
  }
  try {
    const res = await fetch(
      `https://api.github.com/repos/${env.REPO}/actions/workflows/${env.WORKFLOW}/dispatches`,
      {
        method: "POST",
        signal: AbortSignal.timeout(15000),
        headers: {
          Authorization: `Bearer ${env.GH_TOKEN.trim()}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "acgnx-cron",
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ ref: "main" })
      }
    );
    if (res.status === 204) return { ok: true, status: 204, body: "已触发" };
    return { ok: false, status: res.status, body: (await res.text()).slice(0, 300) };
  } catch (e) {
    return { ok: false, status: -1, body: `请求失败：${e}` };
  }
}
async function sendFeishu(env, text) {
  if (!env.FEISHU_WEBHOOK) return false;
  const base = { msg_type: "text", content: { text } };
  if (!env.FEISHU_SECRET) {
    const code = await postFeishu(env.FEISHU_WEBHOOK, base);
    if (code !== 0) {
      console.log(`飞书推送返回 code=${code}。如果机器人开了签名校验，请把密钥填到 FEISHU_SECRET`);
    }
    return code === 0;
  }
  const ts = Math.floor(Date.now() / 1e3);
  const stringToSign = `${ts}
${env.FEISHU_SECRET}`;
  for (const [label, key, msg] of [
    ["A(飞书官方写法)", stringToSign, ""],
    ["B(常规写法)", env.FEISHU_SECRET, stringToSign]
  ]) {
    const sign = await hmacB64(key, msg);
    const payload = { timestamp: String(ts), sign, ...base };
    const code = await postFeishu(env.FEISHU_WEBHOOK, payload);
    console.log(`飞书推送（签名方式${label}）-> code=${code}`);
    if (code === 0) return true;
  }
  console.log("两种签名方式都被拒了：检查 FEISHU_SECRET 是否填错");
  return false;
}
async function postFeishu(url, payload) {
  try {
    const res = await fetch(url, {
      method: "POST",
      signal: AbortSignal.timeout(10000),
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const txt = await res.text();
    try {
      const j = JSON.parse(txt);
      return typeof j.code === "number" ? j.code : res.ok ? 0 : res.status;
    } catch {
      return res.ok ? 0 : res.status;
    }
  } catch (e) {
    console.log(`飞书请求本身失败了：${e}`);
    return -1;
  }
}
async function hmacB64(key, message) {
  const enc = new TextEncoder();
  const k = await crypto.subtle.importKey(
    "raw",
    enc.encode(key),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", k, enc.encode(message));
  let s = "";
  for (const b of new Uint8Array(sig)) s += String.fromCharCode(b);
  return btoa(s);
}
export default worker;
