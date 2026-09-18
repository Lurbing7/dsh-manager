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

interface BalanceInfo {
  ok: boolean;
  provider: string;
  currency: string;
  total: string;
  topped_up: string;
  granted: string;
  queried_at: number;
  error: string | null;
}

interface ModelUsage {
  model: string;
  tokens: number;
  cost: number;
}

interface UsageSummary {
  ok: boolean;
  day_tokens: number;
  day_cost: number;
  week_tokens: number;
  week_cost: number;
  total_cost: number;
  requests: number;
  by_model: ModelUsage[];
  error: string | null;
}

interface PluginInfo {
  name: string;
  installed: string;
  spec: string;
}

interface ProfilePlugins {
  profile: string;
  bundles: string[];
  plugins: PluginInfo[];
  error: string | null;
}

interface BridgeStatus {
  running: boolean;
  script: string;
}

interface HermesPlatform {
  name: string;
  state: string;
  needs_attention: boolean;
}

interface HermesStatus {
  ok: boolean;
  gateway_state: string;
  code_version: string;
  active_agents: number;
  pid: number;
  platforms: HermesPlatform[];
  source: string;
  error: string | null;
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
/** Whether the desktop app the panel launched is still alive. */
const desktopUp = ref(false);
/** Live console output from whatever the panel launched. */
const termLines = ref<string[]>([]);
const termEl = ref<HTMLElement | null>(null);
/** Usage-plugin data (balance + token/cost summary). */
const balance = ref<BalanceInfo | null>(null);
const usage = ref<UsageSummary | null>(null);
let usageTimer: number | null = null;
/** Plugin management: profiles and their third-party packages. */
const profiles = ref<ProfilePlugins[]>([]);
const activeProfile = ref("web");
const newPlugin = ref("");
/** Feishu → dsh bridge. */
const bridgeRunning = ref(false);
const bridgeUsers = ref("");
const bridgeProfile = ref("headless");
/** Hermes gateway (read-only). */
const hermes = ref<HermesStatus | null>(null);

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

/** Balance from the usage plugin (it queries the provider, e.g. DeepSeek). */
async function refreshBalance() {
  try {
    balance.value = await invoke<BalanceInfo>("get_balance", { provider: "deepseek" });
  } catch (e) {
    balance.value = { ok: false, provider: "deepseek", currency: "", total: "", topped_up: "", granted: "", queried_at: 0, error: String(e) };
  }
}

/** Token/cost summary, aggregated in Rust (the raw record list is ~1.9 MB). */
async function refreshUsage() {
  try {
    usage.value = await invoke<UsageSummary>("get_usage_summary", { days: 7 });
  } catch (e) {
    usage.value = { ok: false, day_tokens: 0, day_cost: 0, week_tokens: 0, week_cost: 0, total_cost: 0, requests: 0, by_model: [], error: String(e) };
  }
}

/** Read every profile's installed third-party plugins. */
async function refreshPlugins() {
  try {
    profiles.value = await invoke<ProfilePlugins[]>("list_plugins");
    if (!profiles.value.some((p) => p.profile === activeProfile.value)) {
      const first = profiles.value.find((p) => p.profile === "web") ?? profiles.value[0];
      if (first) activeProfile.value = first.profile;
    }
  } catch (e) {
    say("err", String(e));
  }
}

const currentProfile = computed(
  () => profiles.value.find((p) => p.profile === activeProfile.value) ?? null,
);

/** Whether the Feishu → dsh bridge process is alive. */
async function refreshBridge() {
  try {
    bridgeRunning.value = (await invoke<BridgeStatus>("feishu_bridge_status")).running;
  } catch {
    // only affects the button label
  }
}

async function startBridge() {
  busy.value = "feishu";
  try {
    const r = await invoke<ActionResult>("feishu_bridge_start", {
      allowedUsers: bridgeUsers.value,
      profile: bridgeProfile.value,
    });
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
    await refreshBridge();
  }
}

async function stopBridge() {
  busy.value = "feishu";
  try {
    const r = await invoke<ActionResult>("feishu_bridge_stop");
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
    await refreshBridge();
  }
}

async function installPlugin() {
  const name = newPlugin.value.trim();
  if (!name) {
    say("err", "请填写要安装的包名");
    return;
  }
  busy.value = "plugin";
  try {
    const r = await invoke<ActionResult>("install_plugin", {
      profile: activeProfile.value,
      package: name,
    });
    say(r.ok ? "ok" : "err", r.message);
    if (r.ok) newPlugin.value = "";
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

async function removePlugin(name: string) {
  busy.value = "plugin";
  try {
    const r = await invoke<ActionResult>("remove_plugin", {
      profile: activeProfile.value,
      package: name,
    });
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

async function upgradePlugins() {
  busy.value = "plugin";
  try {
    const r = await invoke<ActionResult>("upgrade_plugins", { profile: activeProfile.value });
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

/** One-shot: pull the plugin's full record list into our own per-day store.
 *  Must run while the plugin is still installed - it is the only source of
 *  history that already carries cost figures. */
async function importUsage() {
  busy.value = "import";
  say("info", "正在从插件导入历史用量…");
  try {
    const r = await invoke<ActionResult>("import_plugin_usage");
    say(r.ok ? "ok" : "err", r.message);
    await refreshUsage();
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
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

/** Read the Hermes gateway state file (never writes to it). */
async function refreshHermes() {
  try {
    hermes.value = await invoke<HermesStatus>("hermes_status");
  } catch (e) {
    hermes.value = null;
    say("err", String(e));
  }
}

async function openHermes() {
  try {
    const r = await invoke<ActionResult>("open_hermes_page");
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  }
}

/** Reopen the harness web UI - the way back after closing the browser tab. */
async function openPage() {
  try {
    const r = await invoke<ActionResult>("open_harness_page", { port: HARNESS_PORT });
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  }
}

/** Start the dsh WEB harness inside the panel (no separate console window). */
async function startHarnessWeb() {
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

/** Stop whatever serves the harness port - panel-launched or not. */
async function stopHarnessWeb() {
  busy.value = "stop";
  say("info", "正在停止 Web 端…");
  try {
    const r = await invoke<ActionResult>("stop_harness", { port: HARNESS_PORT });
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

async function toggleHarness() {
  if (harnessUp.value) await stopHarnessWeb();
  else await startHarnessWeb();
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

async function refreshDesktopState() {
  try {
    desktopUp.value = await invoke<boolean>("desktop_running");
  } catch {
    // ignore - only affects the button label
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
    if (r.ok) desktopUp.value = true;
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

async function stopDesktop() {
  busy.value = "stop";
  try {
    const r = await invoke<ActionResult>("stop_desktop_app");
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
    await refreshDesktopState();
  }
}

async function toggleDesktop() {
  if (desktopUp.value) await stopDesktop();
  else await startDesktop();
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
const webBusy = computed(() => busy.value === "harness" || busy.value === "stop");

onMounted(async () => {
  await refreshLocal();
  await loadSettings();
  await refreshDesktopState();
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
  // Usage/balance come from the harness plugin; refresh on open and every 60s
  // (a balance query hits the provider API, so don't hammer it).
  await refreshBalance();
  await refreshUsage();
  // First run (or after the plugins get uninstalled): seed our own store from
  // the plugin once, while it is still there. Silently skipped when unavailable.
  try {
    const probe = await invoke<{ filled: number }>("usage_series", { bucket: "day", count: 7 });
    if (probe.filled === 0) {
      const r = await invoke<ActionResult>("import_plugin_usage");
      if (r.ok) say("ok", r.message);
    }
  } catch {
    // no harness / no plugin - the chart simply stays empty
  }
  await refreshPlugins();
  await refreshBridge();
  await refreshHermes();
  usageTimer = window.setInterval(() => {
    if (harnessUp.value) {
      void refreshBalance();
      void refreshUsage();
    }
  }, 60000);
});

onUnmounted(() => {
  unlisten?.();
  unlistenHarness?.();
  unlistenTerm?.();
  if (usageTimer !== null) window.clearInterval(usageTimer);
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
          <button
            v-if="harnessUp"
            class="tiny"
            :disabled="!!busy"
            title="在默认浏览器里打开 http://127.0.0.1:3080"
            @click="openPage"
          >
            打开网页
          </button>
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
      <div class="row">
        <span class="label">余额</span>
        <span class="value mono" :class="{ hl: balance?.ok, off: balance && !balance.ok }">
          {{ balance?.ok ? `${balance.currency} ${balance.total}` : balance?.error ? "查询失败" : "—" }}
        </span>
      </div>
      <p v-if="local?.error" class="hint err">{{ local.error }}</p>
      <p v-else-if="update?.registry" class="hint">源：{{ update.registry }}</p>
    </section>

    <section class="actions">
      <!-- 1. web harness: start when down, red stop when up -->
      <button
        class="primary"
        :class="{ danger: harnessUp === true }"
        :disabled="!!busy"
        :title="
          harnessUp === true
            ? '停止占用 3080 端口的 harness（会中断正在使用它的会话）'
            : '在面板内启动 dsh web --port 3080'
        "
        @click="toggleHarness"
      >
        <span v-if="webBusy" class="spinner"></span>
        {{ busy === "stop" ? "停止中…" : harnessUp === true ? "停止 Web 端" : busy === "harness" ? "启动中…" : "启动 Web 端" }}
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

      <!-- 3. desktop launch: start / red stop -->
      <button
        class="wide"
        :class="{ danger: desktopUp }"
        :disabled="!!busy"
        :title="desktopUp ? '停止面板启动的桌面端' : '在源码目录执行 pnpm run dev:desktop'"
        @click="toggleDesktop"
      >
        <span v-if="busy === 'desktop' || (desktopUp && busy === 'stop')" class="spinner"></span>
        {{ desktopUp ? "停止桌面端" : busy === "desktop" ? "启动中…" : "启动桌面端" }}
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

    <details v-if="usage?.ok" class="usage">
      <summary>
        用量与消耗 · 近 24h / 7 天
        <span class="term-actions">
          <button class="tiny" :disabled="!!busy" @click.prevent="importUsage">导入历史</button>
          <button class="tiny" :disabled="!!busy" @click.prevent="refreshUsage">刷新</button>
        </span>
      </summary>
      <div class="usage-grid">
        <div class="usage-cell">
          <span class="label">近 24 小时</span>
          <span class="value mono">{{ usage.day_tokens.toLocaleString() }} tok</span>
          <span class="value mono hl">¥{{ usage.day_cost.toFixed(4) }}</span>
        </div>
        <div class="usage-cell">
          <span class="label">近 7 天</span>
          <span class="value mono">{{ usage.week_tokens.toLocaleString() }} tok</span>
          <span class="value mono hl">¥{{ usage.week_cost.toFixed(4) }}</span>
        </div>
      </div>
      <div v-for="m in usage.by_model.slice(0, 6)" :key="m.model" class="row usage-row">
        <span class="label mono">{{ m.model }}</span>
        <span class="value mono">{{ m.tokens.toLocaleString() }} tok · ¥{{ m.cost.toFixed(4) }}</span>
      </div>
      <p class="hint">累计 {{ usage.requests.toLocaleString() }} 次调用 · 总花费 ¥{{ usage.total_cost.toFixed(4) }}（数据来自 harness 的 dsh-usage-plugin）</p>
    </details>

    <details class="plugins">
      <summary>
        插件管理 · {{ currentProfile?.plugins.length ?? 0 }} 个第三方插件
        <span class="term-actions">
          <button class="tiny" :disabled="!!busy" @click.prevent="refreshPlugins">刷新</button>
        </span>
      </summary>

      <div class="setting-row">
        <select v-model="activeProfile" class="path mono" @change="refreshPlugins">
          <option v-for="p in profiles" :key="p.profile" :value="p.profile">{{ p.profile }}</option>
        </select>
        <button class="tiny" :disabled="!!busy" @click.prevent="upgradePlugins">全部升级</button>
      </div>

      <div v-for="p in currentProfile?.plugins ?? []" :key="p.name" class="row plugin-row">
        <span class="label mono plugin-name" :title="p.name">{{ p.name }}</span>
        <span class="value mono">
          {{ p.installed || "未安装" }}
          <button class="tiny danger" :disabled="!!busy" @click.prevent="removePlugin(p.name)">卸载</button>
        </span>
      </div>
      <p v-if="currentProfile && currentProfile.plugins.length === 0" class="hint">
        这个 profile 没有第三方插件。
      </p>

      <div class="setting-row">
        <input
          v-model="newPlugin"
          class="path mono"
          type="text"
          spellcheck="false"
          placeholder="要安装的包名，如 @scope/dsh-xxx"
          @keyup.enter="installPlugin"
        />
        <button class="tiny" :disabled="!!busy" @click.prevent="installPlugin">安装</button>
      </div>
      <p class="hint">
        等价于在该 profile 目录执行 <code>dsh plugin --profile {{ activeProfile }} add &lt;包名&gt;</code>
        （本质是 pnpm）。<b>装/卸后需要重启 Web 端才生效</b>，进度见下方终端。
      </p>
    </details>

    <details class="plugins">
      <summary>
        飞书远程控制 · {{ bridgeRunning ? "运行中" : "已停止" }}
        <span class="term-actions">
          <button class="tiny" :disabled="!!busy" @click.prevent="refreshBridge">刷新</button>
        </span>
      </summary>

      <div class="setting-row">
        <input
          v-model="bridgeUsers"
          class="path mono"
          type="text"
          spellcheck="false"
          placeholder="白名单 open_id（ou_ 开头，逗号分隔）"
        />
      </div>
      <div class="setting-row">
        <input
          v-model="bridgeProfile"
          class="path mono"
          type="text"
          spellcheck="false"
          placeholder="dsh profile"
          style="max-width: 110px"
        />
        <button
          class="tiny"
          :class="{ danger: bridgeRunning }"
          :disabled="!!busy"
          @click.prevent="bridgeRunning ? stopBridge() : startBridge()"
        >
          {{ bridgeRunning ? "停止桥" : "启动桥" }}
        </button>
      </div>
      <p class="hint">
        在飞书里给机器人发消息 → 本机执行
        <code>dsh --profile {{ bridgeProfile || "headless" }}</code> → 结果回飞书。运行日志见下方终端。
      </p>
      <p class="hint">
        ⚠️ 白名单必填：飞书消息等于本机执行权限。另外每条消息是<b>独立任务</b>（headless 无会话延续），
        且需要先用 <code>lark-cli config init</code> 绑定飞书应用。
      </p>
    </details>

    <details class="plugins">
      <summary>
        Hermes 网关 · {{ hermes?.ok ? hermes.gateway_state : "不可用" }}
        <span class="term-actions">
          <button class="tiny" :disabled="!!busy" @click.prevent="refreshHermes">刷新</button>
          <button class="tiny" :disabled="!!busy" @click.prevent="openHermes">打开面板</button>
        </span>
      </summary>

      <div v-if="hermes?.ok">
        <div class="row">
          <span class="label">版本 / PID</span>
          <span class="value mono">{{ hermes.code_version }} · {{ hermes.pid }}</span>
        </div>
        <div class="row">
          <span class="label">活跃 Agent</span>
          <span class="value mono">{{ hermes.active_agents }}</span>
        </div>
        <div v-for="p in hermes.platforms" :key="p.name" class="row">
          <span class="label">{{ p.name }}</span>
          <span
            class="value"
            :class="{ off: p.state !== 'connected', hl: p.state === 'connected' }"
          >
            {{ p.state }}{{ p.needs_attention ? " · 需要注意" : "" }}
          </span>
        </div>
      </div>
      <p v-else class="hint err">{{ hermes?.error ?? "读不到 Hermes 状态" }}</p>

      <p class="hint">
        Hermes 是跑在 Docker 里的另一个 AI Agent（自带多平台网关，包括飞书）。这里**只读**它的状态，
        不做启停 —— 要改配置请用 <code>docker compose --profile ai</code>。
      </p>
    </details>

    <details v-if="termLines.length" class="term">
      <summary>
        <span>终端 · {{ termLines.length }} 行</span>
        <span class="term-actions">
          <button class="tiny" :disabled="!!busy" @click.prevent="stopHarnessWeb">停 Web</button>
          <button class="tiny" :disabled="!!busy" @click.prevent="stopDesktop">停桌面</button>
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
