<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";

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

interface Settings {
  desktop_source_dir: string | null;
}

/** Where this machine serves the harness web UI. */
const HARNESS_PORT = 3080;

const local = ref<LocalInfo | null>(null);
const update = ref<UpdateInfo | null>(null);
/** Desktop app source checkout, bound to the input box. */
const sourceDir = ref("");
const busy = ref<string | null>(null);
const toast = ref<{ kind: "info" | "ok" | "err"; text: string } | null>(null);
const logText = ref("");
/** null = not known yet (the poller fills it in within one tick). */
const harnessUp = ref<boolean | null>(null);
/** Live console output from whatever the panel launched. */
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
  // keep the buffer bounded - a desktop build is chatty
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

async function loadSettings() {
  try {
    const s = await invoke<Settings>("get_settings");
    sourceDir.value = s.desktop_source_dir ?? "";
  } catch (e) {
    say("err", String(e));
  }
}

/** Persist whatever the input box currently holds (empty clears it). */
async function saveSourceDir() {
  try {
    const r = await invoke<ActionResult>("set_desktop_source_dir", {
      dir: sourceDir.value.trim() || null,
    });
    if (!r.ok) say("err", r.message);
    await loadSettings();
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

/** Start the dsh WEB harness inside the panel (no separate console window). */
async function openHarness() {
  busy.value = "harness";
  say("info", "正在确认 Web 端是否在运行…");
  try {
    const r = await invoke<ActionResult>("start_harness_inline", { port: HARNESS_PORT });
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

/** Pick the source root with a folder dialog, then persist it. */
async function pickSourceDir() {
  try {
    const picked = await open({
      directory: true,
      multiple: false,
      title: "选择 DeepSeek Harness 源码根目录",
    });
    if (typeof picked !== "string") return;
    sourceDir.value = picked;
    await saveSourceDir();
  } catch (e) {
    say("err", String(e));
  }
}

/** Start the Electron DESKTOP app from the configured source checkout. */
async function startDesktop() {
  busy.value = "desktop";
  // persist any edit made in the input box before launching
  await saveSourceDir();
  if (!sourceDir.value.trim()) {
    busy.value = null;
    say("err", "先填源码目录，或点「浏览…」选一个");
    return;
  }
  say("info", "正在从源码启动 Electron 桌面端…");
  try {
    const r = await invoke<ActionResult>("start_desktop_app");
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

async function stopChild(which: "harness" | "desktop") {
  busy.value = "stop";
  try {
    const r = await invoke<ActionResult>(which === "harness" ? "stop_harness" : "stop_desktop_app");
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

const harnessText = computed(() => (harnessUp.value ? "运行中" : "未启动"));

onMounted(async () => {
  await refreshLocal();
  await loadSettings();
  // Fired by the background daily sweep and the tray menu.
  unlisten = await listen<UpdateInfo>("update-checked", (e) => {
    update.value = e.payload;
  });
  // Fired by the harness port poller (the same one that switches the tray icon).
  unlistenHarness = await listen<boolean>("harness-state", (e) => {
    harnessUp.value = e.payload;
  });
  // stdout/stderr of whatever the panel started.
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
        <span class="label">Web 端</span>
        <span class="value" :class="{ off: harnessUp === false }">
          {{ harnessUp === null ? "检测中…" : harnessText }}
        </span>
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
      <!-- 1. web harness -->
      <button
        class="primary"
        :disabled="!!busy"
        title="在面板内启动 dsh web --port 3080"
        @click="openHarness"
      >
        <span v-if="busy === 'harness'" class="spinner"></span>
        {{ busy === "harness" ? "启动中…" : "启动 Web 端" }}
      </button>

      <!-- 2. desktop source path + browse -->
      <div class="desktop-row">
        <input
          v-model="sourceDir"
          class="path mono"
          type="text"
          spellcheck="false"
          placeholder="桌面端源码根目录（可粘贴路径）"
          title="DeepSeek Harness 源码根目录：可直接粘贴路径，或点右侧「浏览…」选择"
          @change="saveSourceDir"
        />
        <button class="browse" :disabled="!!busy" title="选择源码根目录" @click="pickSourceDir">
          浏览…
        </button>
      </div>

      <!-- 3. desktop launch -->
      <button
        class="wide"
        :disabled="!!busy"
        title="在源码目录执行 pnpm run dev:desktop"
        @click="startDesktop"
      >
        <span v-if="busy === 'desktop'" class="spinner"></span>
        {{ busy === "desktop" ? "启动中…" : "启动桌面端" }}
      </button>

      <p class="hint desktop-hint">
        桌面端（Electron）从源码启动：该目录需已 <code>pnpm install</code>，首次启动会构建，较慢。
      </p>

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
        <span>终端 · {{ termLines.length }} 行</span>
        <span class="term-actions">
          <button class="tiny" :disabled="!!busy" @click.prevent="stopChild('harness')">停 Web</button>
          <button class="tiny" :disabled="!!busy" @click.prevent="stopChild('desktop')">停桌面</button>
        </span>
      </summary>
      <pre ref="termEl">{{ termLines.join("\n") }}</pre>
    </details>

    <details v-if="logText" class="log">
      <summary>npm 输出</summary>
      <pre>{{ logText }}</pre>
    </details>
  </main>
</template>
