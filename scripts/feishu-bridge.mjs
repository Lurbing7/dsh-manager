/**
 * feishu-bridge.mjs — 把飞书消息接到本机 dsh 上。
 *
 *   飞书 App
 *     ↓ im.message.receive_v1
 *   lark-cli event consume            (NDJSON → stdout)
 *     ↓ 去重 + 白名单
 *   dsh --profile headless "<消息>"   (宿主机，无容器隔离)
 *     ↓ 最终回复
 *   lark-cli im +messages-send        → 飞书 App
 *
 * 环境变量：
 *   FEISHU_ALLOWED_USERS   允许触发的 open_id，逗号分隔（**必须设置**，否则拒绝所有人）
 *   DSH_BRIDGE_PROFILE     dsh profile，默认 headless
 *   DSH_BRIDGE_TIMEOUT_MS  单条任务超时，默认 300000 (5min)
 *   DSH_BRIDGE_DRY_RUN     1 = 不真的执行 dsh，只回显（调试用）
 *
 * 用法：
 *   node scripts/feishu-bridge.mjs
 *
 * 注意：dsh --profile headless 是一问一答、无会话延续，所以每条飞书消息都是独立任务。
 */

import { spawn } from "node:child_process";
import { createInterface } from "node:readline";

const ALLOWED = (process.env.FEISHU_ALLOWED_USERS ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);
const PROFILE = process.env.DSH_BRIDGE_PROFILE ?? "headless";
const TIMEOUT_MS = Number(process.env.DSH_BRIDGE_TIMEOUT_MS ?? 300000);
const DRY_RUN = process.env.DSH_BRIDGE_DRY_RUN === "1";

/** message_id 去重（飞书会重投事件）。 */
const seen = new Set();

function log(...args) {
  console.log(`[${new Date().toISOString()}]`, ...args);
}

/** Windows 上 lark-cli / dsh 是 .cmd 包装，必须走 shell。 */
function run(cmd, args, { timeout = 0, input = null } = {}) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { shell: true, windowsHide: true });
    let out = "";
    let err = "";
    let timer = null;
    if (timeout > 0) {
      timer = setTimeout(() => {
        log(`!! ${cmd} 超时 ${timeout}ms，已终止`);
        child.kill();
      }, timeout);
    }
    child.stdout?.on("data", (d) => (out += d.toString("utf8")));
    child.stderr?.on("data", (d) => (err += d.toString("utf8")));
    child.on("close", (code) => {
      if (timer) clearTimeout(timer);
      resolve({ code, out, err });
    });
    child.on("error", (e) => {
      if (timer) clearTimeout(timer);
      resolve({ code: -1, out, err: String(e) });
    });
    if (input !== null) {
      child.stdin?.write(input);
      child.stdin?.end();
    }
  });
}

/** 发送回复：优先用 messages-send 到原会话。 */
async function sendReply(chatId, text) {
  const res = await run("lark-cli", [
    "im",
    "+messages-send",
    "--chat-id",
    chatId,
    "--text",
    text,
  ]);
  if (res.code !== 0) {
    log("!! 回复失败:", res.err.trim() || res.out.trim());
    return false;
  }
  return true;
}

/** 取消息的去重键、会话、正文、发送者。字段名做多重兼容。 */
function parseEvent(ev) {
  const messageId = ev.message_id ?? ev.id ?? "";
  const chatId = ev.chat_id ?? "";
  const chatType = ev.chat_type ?? "";
  const content = (ev.content ?? "").trim();
  const sender =
    ev.sender?.open_id ??
    ev.sender?.sender_id?.open_id ??
    ev.sender_id?.open_id ??
    ev.open_id ??
    "";
  return { messageId, chatId, chatType, content, sender };
}

async function handleEvent(ev) {
  const { messageId, chatId, chatType, content, sender } = parseEvent(ev);

  if (!messageId || !chatId) {
    log("跳过：事件缺少 message_id / chat_id");
    return;
  }
  if (seen.has(messageId)) return; // 重投
  seen.add(messageId);
  if (seen.size > 2000) {
    // 简单封顶，避免长跑内存膨胀
    const first = seen.values().next().value;
    seen.delete(first);
  }

  if (!content) {
    log(`跳过：空内容（${chatType}）`);
    return;
  }

  // 白名单：没配就拒绝所有人，避免变成公开的远程执行入口
  if (ALLOWED.length === 0) {
    log("拒绝：未配置 FEISHU_ALLOWED_USERS（安全默认：不处理任何消息）");
    return;
  }
  if (sender && !ALLOWED.includes(sender)) {
    log(`拒绝：发送者 ${sender} 不在白名单`);
    return;
  }
  if (!sender) {
    log("警告：事件里没有发送者 open_id，只能依赖 chat_id 白名单；本条放行");
  }

  log(`收到（${chatType}）：${content.slice(0, 120)}`);

  if (DRY_RUN) {
    await sendReply(chatId, `[dry-run] 收到：${content.slice(0, 200)}`);
    return;
  }

  const started = Date.now();
  const res = await run("dsh", ["--profile", PROFILE, content], { timeout: TIMEOUT_MS });
  const reply = (res.out ?? "").trim();
  const secs = ((Date.now() - started) / 1000).toFixed(1);

  if (res.code !== 0 && !reply) {
    log(`!! dsh 失败（${secs}s）:`, (res.err ?? "").trim().slice(0, 400));
    await sendReply(chatId, `执行失败（${secs}s）：${(res.err ?? "").trim().slice(0, 300) || "无输出"}`);
    return;
  }

  log(`dsh 完成（${secs}s，${reply.length} 字符）`);
  // 飞书单条消息有长度上限，超长就截断（完整内容在本地终端里）
  const body = reply.length > 3500 ? `${reply.slice(0, 3500)}\n\n…（已截断，完整输出见本机面板终端）` : reply;
  await sendReply(chatId, body || "（空回复）");
}

async function main() {
  log(`飞书 → dsh 桥启动：profile=${PROFILE} 白名单=${ALLOWED.length} 人 dryRun=${DRY_RUN}`);
  if (ALLOWED.length === 0) {
    log("!! 未配置 FEISHU_ALLOWED_USERS，所有消息都会被拒绝。");
    log("   从飞书事件里拿一次你的 open_id（ou_ 开头）再配上，或直接沿用 Hermes 的 FEISHU_ALLOWED_USERS。");
  }

  const consume = spawn("lark-cli", ["event", "consume", "im.message.receive_v1"], {
    shell: true,
    windowsHide: true,
  });

  const rl = createInterface({ input: consume.stdout });
  rl.on("line", (line) => {
    const text = line.trim();
    if (!text || !text.startsWith("{")) return;
    let ev;
    try {
      ev = JSON.parse(text);
    } catch {
      return; // 非 JSON 行（进度提示等）忽略
    }
    void handleEvent(ev).catch((e) => log("!! 处理异常:", String(e)));
  });

  consume.stderr?.on("data", (d) => {
    const s = d.toString("utf8").trim();
    if (s) log("lark-cli:", s.slice(0, 300));
  });

  consume.on("close", (code) => {
    log(`lark-cli event consume 退出（code=${code}）`);
    process.exit(code ?? 0);
  });
}

main().catch((e) => {
  console.error("桥启动失败:", e);
  process.exit(1);
});
