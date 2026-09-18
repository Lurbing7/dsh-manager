<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

interface LocalInfo {
  ok: boolean;
  dsh_version: string | null;
  node_dir: string | null;
  node_version: string | null;
  error: string | null;
}

interface UpdateInfo {
  ok: boolean;
  local_version: string | null;
  latest_version: string | null;
  has_update: boolean;
  registry: string;
  checked_at: number;
  error: string | null;
}

interface ActionResult {
  ok: boolean;
  action: string;
  message: string;
  output: string;
}

/** Where this machine serves the harness UI. */
const HARNESS_PORT = 3080;

const local = ref<LocalInfo | null>(null);
const update = ref<UpdateInfo | null>(null);
const busy = ref<string | null>(null);
const toast = ref<{ kind: "info" | "ok" | "err"; text: string } | null>(null);
const logText = ref("");
/** null = not known yet (the poller fills it in within one tick). */
const harnessUp = ref<boolean | null>(null);
/** Live console output from the harness the panel started. */
const termLines = ref<string[]>([]);
const termEl = ref<HTMLElement | null>(null);

let unlisten: UnlistenFn | null = null;
let unlistenHarness: UnlistenFn | null = null;
let unlistenTerm: UnlistenFn | null = null;

function say(kind: "info" | "ok" | "err", text: string) {
  toast.value = { kind, text };
}

async function appendTerm(line: string) {
  termLines.value.push(line);
  // keep the buffer bounded - dsh web can be chatty over a long session
  if (termLines.value.length > 400) {
    termLines.value.splice(0, termLines.value.length - 400);
  }
  await nextTick();
  if (termEl.value) termEl.value.scrollTop = termEl.value.scrollHeight;
}

async function refreshLocal() {
  try {
    local.value = await invoke<LocalInfo>("get_local_info");
  } catch (e) {
    say("err", String(e));
  }
}

async function checkUpdate() {
  busy.value = "check";
  say("info", "正在查询 registry…");
  try {
    const r = await invoke<UpdateInfo>("check_update");
    update.value = r;
    if (!r.ok) say("err", r.error ?? "检查失败");
    else if (r.has_update) say("ok", `发现新版本 ${r.latest_version}`);
    else say("ok", "已是最新版本");
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

async function openHarness() {
  busy.value = "harness";
  say("info", "正在确认 harness 是否在运行…");
  try {
    // Starts it INSIDE the panel - output streams into the console below
    // instead of a separate terminal window popping up.
    const r = await invoke<ActionResult>("start_harness_inline", { port: HARNESS_PORT });
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

async function stopHarness() {
  busy.value = "stop";
  try {
    const r = await invoke<ActionResult>("stop_harness");
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

async function upgrade() {
  busy.value = "upgrade";
  say("info", "正在升级 dsh，请稍候…（npm 装包通常要几十秒）");
  try {
    const r = await invoke<ActionResult>("upgrade_dsh");
    logText.value = r.output;
    say(r.ok ? "ok" : "err", r.message);
    await refreshLocal();
    if (r.ok) await checkUpdate();
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

async function quit() {
  await invoke("quit_app");
}

const checkedText = computed(() => {
  const t = update.value?.checked_at;
  if (!t) return "从未检查";
  return new Date(t * 1000).toLocaleString("zh-CN", { hour12: false });
});

const statusClass = computed(() => {
  if (!update.value) return "unknown";
  if (!update.value.ok) return "err";
  return update.value.has_update ? "warn" : "ok";
});

const statusText = computed(() => {
  if (!update.value) return "未检查";
  if (!update.value.ok) return "检查失败";
  return update.value.has_update ? "有可用更新" : "已是最新";
});

const harnessText = computed(() => {
  if (harnessUp.value === null) return "检测中…";
  return harnessUp.value ? "运行中" : "未启动";
});

onMounted(async () => {
  await refreshLocal();
  // Fired by the background daily sweep and the tray menu.
  unlisten = await listen<UpdateInfo>("update-checked", (e) => {
    update.value = e.payload;
  });
  // Fired by the harness port poller (the same one that switches the tray icon).
  unlistenHarness = await listen<boolean>("harness-state", (e) => {
    harnessUp.value = e.payload;
  });
  // stdout/stderr of the harness the panel started.
  unlistenTerm = await listen<string>("harness-output", (e) => {
    void appendTerm(e.payload);
  });
  try {
    harnessUp.value = await invoke<boolean>("harness_running");
  } catch {
    // the poller will fill this in on its next tick
  }
  await checkUpdate();
});

onUnmounted(() => {
  unlisten?.();
  unlistenHarness?.();
  unlistenTerm?.();
});
</script>

<template>
  <main class="app">
    <header class="head">
      <div class="brand">
        <span class="dot" :class="statusClass"></span>
        <h1>DSH Panel</h1>
        <span class="status">{{ statusText }}</span>
      </div>
      <button class="ghost" title="退出应用" @click="quit">退出</button>
    </header>

    <section class="card">
      <div class="row">
        <span class="label">本地 dsh</span>
        <span class="value mono">{{ local?.dsh_version ?? "未检测到" }}</span>
      </div>
      <div class="row">
        <span class="label">Node</span>
        <span class="value mono">{{ local?.node_version ?? "—" }}</span>
      </div>
      <div class="row">
        <span class="label">Harness</span>
        <span class="value" :class="{ off: harnessUp === false }">{{ harnessText }}</span>
      </div>
      <div class="row">
        <span class="label">最新版本</span>
        <span class="value mono" :class="{ hl: update?.has_update }">
          {{ update?.latest_version ?? "—" }}
        </span>
      </div>
      <div class="row">
        <span class="label">上次检查</span>
        <span class="value">{{ checkedText }}</span>
      </div>
      <p v-if="local?.error" class="hint err">{{ local.error }}</p>
      <p v-else-if="update?.registry" class="hint">源：{{ update.registry }}</p>
    </section>

    <section class="actions">
      <button class="primary" :disabled="!!busy" @click="openHarness">
        <span v-if="busy === 'harness'" class="spinner"></span>
        {{ busy === "harness" ? "启动中…" : "打开 Harness" }}
      </button>
      <button :disabled="!!busy" @click="checkUpdate">
        <span v-if="busy === 'check'" class="spinner"></span>
        {{ busy === "check" ? "检查中…" : "检查更新" }}
      </button>
      <button
        class="upgrade"
        :class="{ attention: update?.has_update }"
        :disabled="!!busy || !update?.has_update"
        @click="upgrade"
      >
        <span v-if="busy === 'upgrade'" class="spinner"></span>
        {{ busy === "upgrade" ? "升级中…" : "一键升级" }}
      </button>
    </section>

    <p v-if="toast" class="toast" :class="toast.kind">{{ toast.text }}</p>

    <details v-if="termLines.length" class="term">
      <summary>
        <span>Harness 终端 · {{ termLines.length }} 行</span>
        <button class="ghost tiny" :disabled="!!busy" @click.prevent="stopHarness">停止</button>
      </summary>
      <pre ref="termEl">{{ termLines.join("\n") }}</pre>
    </details>

    <details v-if="logText" class="log">
      <summary>npm 输出</summary>
      <pre>{{ logText }}</pre>
    </details>
  </main>
</template>
