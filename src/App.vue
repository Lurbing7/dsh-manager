<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import UsageChart from "./components/UsageChart.vue";
import type { UsagePoint } from "./types";

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

interface UsageSeries {
  ok: boolean;
  bucket: string;
  points: UsagePoint[];
  total_cost: number;
  total_tokens: number;
  total_calls: number;
  month_cost: number;
  month_tokens: number;
  month_calls: number;
  filled: number;
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
const balance = ref<BalanceInfo | null>(null);
const series = ref<UsageSeries | null>(null);
const bucket = ref<"day" | "week" | "month">("day");
const sourceDir = ref("");
const busy = ref<string | null>(null);
const toast = ref<{ kind: "info" | "ok" | "err"; text: string } | null>(null);
const logText = ref("");
const harnessUp = ref<boolean | null>(null);
const desktopUp = ref(false);
const termLines = ref<string[]>([]);
const termEl = ref<HTMLElement | null>(null);
const drawerOpen = ref(false);
const profiles = ref<ProfilePlugins[]>([]);
const activeProfile = ref("web");
const newPlugin = ref("");
const bridgeRunning = ref(false);
const bridgeUsers = ref("");
const hermes = ref<HermesStatus | null>(null);
let usageTimer: number | null = null;

let unlisten: UnlistenFn | null = null;
let unlistenHarness: UnlistenFn | null = null;
let unlistenTerm: UnlistenFn | null = null;

/** Transient notice. Success/info vanish after ~3s, errors linger a bit longer. */
let toastTimer: number | null = null;
function say(kind: "info" | "ok" | "err", text: string) {
  toast.value = { kind, text };
  if (toastTimer !== null) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(
    () => {
      toast.value = null;
      toastTimer = null;
    },
    kind === "err" ? 6000 : 3000,
  );
}

async function appendTerm(line: string) {
  termLines.value.push(line);
  if (termLines.value.length > 400) {
    termLines.value.splice(0, termLines.value.length - 400);
  }
  await nextTick();
  if (termEl.value) termEl.value.scrollTop = termEl.value.scrollHeight;
}

/* ---------- data ---------- */

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

async function refreshBalance() {
  try {
    // Native query: reads the API key from the harness credential store, so it
    // keeps working after the usage plugin is uninstalled.
    balance.value = await invoke<BalanceInfo>("get_balance_native", { provider: "deepseek" });
  } catch {
    balance.value = null;
  }
}

async function refreshSeries() {
  try {
    series.value = await invoke<UsageSeries>("usage_series", {
      bucket: bucket.value,
      count: bucket.value === "day" ? 14 : bucket.value === "week" ? 8 : 12,
    });
  } catch (e) {
    series.value = null;
    say("err", String(e));
  }
}

async function setBucket(next: "day" | "week" | "month") {
  bucket.value = next;
  await refreshSeries();
}

async function importUsage() {
  busy.value = "import";
  try {
    const r = await invoke<ActionResult>("import_plugin_usage");
    say(r.ok ? "ok" : "err", r.message);
    await refreshSeries();
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

const checkedText = computed(() => {
  const t = update.value?.checked_at;
  if (!t) return "从未检查";
  return new Date(t * 1000).toLocaleString("zh-CN", { hour12: false });
});

const statusText = computed(() => {
  if (!update.value) return "未检查";
  if (!update.value.ok) return "检查失败";
  return update.value.has_update ? `可更新 ${update.value.latest_version}` : "已是最新";
});

const statusClass = computed(() => {
  if (!update.value) return "unknown";
  if (!update.value.ok) return "err";
  return update.value.has_update ? "warn" : "ok";
});

const harnessText = computed(() =>
  harnessUp.value === null ? "检测中…" : harnessUp.value ? "运行中" : "未启动",
);

const balanceText = computed(() => {
  if (!balance.value) return "—";
  if (!balance.value.ok) return "查询失败";
  return `${balance.value.currency} ${balance.value.total}`;
});

/** Curve summary line under the chart. */
const rangeText = computed(() => {
  if (!series.value?.ok) return "";
  const label = bucket.value === "day" ? "近 14 天" : bucket.value === "week" ? "近 8 周" : "近 12 个月";
  return `${label}合计 ¥${series.value.total_cost.toFixed(2)} · ${series.value.total_calls.toLocaleString()} 次调用`;
});

const currentProfile = computed(
  () => profiles.value.find((p) => p.profile === activeProfile.value) ?? null,
);

/* ---------- actions ---------- */

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

async function startHarnessWeb() {
  busy.value = "harness";
  try {
    const r = await invoke<ActionResult>("start_harness_inline", { port: HARNESS_PORT });
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

async function stopHarnessWeb() {
  busy.value = "stop";
  try {
    const r = await invoke<ActionResult>("stop_harness", { port: HARNESS_PORT });
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  } finally {
    busy.value = null;
  }
}

/** The floating action: one button that is start-or-stop depending on state. */
async function toggleHarness() {
  if (harnessUp.value) await stopHarnessWeb();
  else await startHarnessWeb();
}

async function openPage() {
  try {
    const r = await invoke<ActionResult>("open_harness_page", { port: HARNESS_PORT });
    say(r.ok ? "ok" : "err", r.message);
  } catch (e) {
    say("err", String(e));
  }
}

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
    /* label only */
  }
}

async function startDesktop() {
  busy.value = "desktop";
  await saveSourceDir();
  if (!sourceDir.value.trim()) {
    busy.value = null;
    say("err", "先填源码目录，或点「浏览…」选一个");
    return;
  }
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

async function refreshBridge() {
  try {
    bridgeRunning.value = (await invoke<BridgeStatus>("feishu_bridge_status")).running;
  } catch {
    /* label only */
  }
}

async function startBridge() {
  busy.value = "feishu";
  try {
    const r = await invoke<ActionResult>("feishu_bridge_start", {
      allowedUsers: bridgeUsers.value,
      profile: "headless",
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

async function refreshHermes() {
  try {
    hermes.value = await invoke<HermesStatus>("hermes_status");
  } catch {
    hermes.value = null;
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

async function quit() {
  await invoke("quit_app");
}

onMounted(async () => {
  await refreshLocal();
  await loadSettings();
  await refreshDesktopState();
  await refreshBalance();
  await refreshSeries();

  // First run: seed the local store from the plugin while it is still installed.
  try {
    if ((series.value?.filled ?? 0) === 0) {
      const r = await invoke<ActionResult>("import_plugin_usage");
      if (r.ok) {
        say("ok", r.message);
        await refreshSeries();
      }
    }
  } catch {
    /* no plugin / no harness - the chart just stays empty */
  }

  unlisten = await listen<UpdateInfo>("update-checked", (e) => {
    update.value = e.payload;
  });
  unlistenHarness = await listen<boolean>("harness-state", (e) => {
    harnessUp.value = e.payload;
  });
  unlistenTerm = await listen<string>("harness-output", (e) => {
    void appendTerm(e.payload);
  });

  try {
    harnessUp.value = await invoke<boolean>("harness_running");
  } catch {
    /* poller will fill it */
  }

  await checkUpdate();
  await refreshPlugins();
  await refreshBridge();
  await refreshHermes();

  // Balance costs a real provider call; the chart only needs local files.
  usageTimer = window.setInterval(() => {
    void refreshSeries();
    if (harnessUp.value) void refreshBalance();
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
  <div class="shell">
    <!-- ============ dashboard ============ -->
    <main class="dash">
      <header class="dash-head">
        <div class="brand">
          <span class="dot" :class="statusClass"></span>
          <h1>DSH Panel</h1>
          <span class="status-pill">{{ statusText }}</span>
        </div>
        <button class="ghost" title="重新载入数据" @click="refreshSeries">刷新</button>
      </header>

      <section class="stats">
        <div class="stat stat-primary">
          <span class="stat-label">本月花费</span>
          <span class="stat-value">
            ¥{{ (series?.month_cost ?? 0).toFixed(2) }}
          </span>
          <span class="stat-sub">{{ series?.month_calls ?? 0 }} 次调用 · {{ ((series?.month_tokens ?? 0) / 1000).toFixed(0) }}K 令牌</span>
        </div>
        <div class="stat">
          <span class="stat-label">剩余余额</span>
          <span class="stat-value">{{ balanceText }}</span>
          <span class="stat-sub">
            <template v-if="balance?.ok">
              充值 {{ balance.topped_up }} · 赠送 {{ balance.granted }}
            </template>
            <template v-else>{{ balance?.error ?? "—" }}</template>
          </span>
        </div>
        <div class="stat">
          <span class="stat-label">Web 端</span>
          <span class="stat-value" :class="{ 'is-off': harnessUp === false }">{{ harnessText }}</span>
          <span class="stat-sub">
            <button v-if="harnessUp" class="link" @click="openPage">打开网页 →</button>
            <template v-else>端口 {{ HARNESS_PORT }}</template>
          </span>
        </div>
      </section>

      <section class="panel chart-panel">
        <div class="panel-head">
          <div>
            <h2>消耗趋势</h2>
            <p class="panel-sub">{{ rangeText }}</p>
          </div>
          <div class="segmented">
            <button :class="{ active: bucket === 'day' }" @click="setBucket('day')">近 14 天</button>
            <button :class="{ active: bucket === 'week' }" @click="setBucket('week')">近 8 周</button>
            <button :class="{ active: bucket === 'month' }" @click="setBucket('month')">近 12 月</button>
          </div>
        </div>

        <UsageChart v-if="series?.ok && series.points.length" :points="series.points" :bucket="bucket" />
        <p v-else class="empty">
          {{ series?.error ?? "还没有用量数据。装插件时点左侧设置里的「导入历史」，或让 harness 跑一段时间。" }}
        </p>

        <p v-if="series?.ok && series.filled < series.points.length" class="gap-note">
          这 {{ series.points.length }} 个区间里有 {{ series.filled }} 个有用量记录，其余为<strong>空档</strong>（不是零消耗）——空心圆点表示该区间没有数据。
        </p>
      </section>
    </main>

    <!-- ============ floating buttons ============ -->
    <button
      class="fab fab-left"
      :class="{ open: drawerOpen }"
      title="设置"
      @click="drawerOpen = !drawerOpen"
    >
      <span class="fab-icon">⚙</span>
    </button>

    <button
      class="fab fab-right"
      :class="{ stop: harnessUp === true }"
      :disabled="!!busy"
      :title="harnessUp ? '停止 Web 端' : '启动 Web 端'"
      @click="toggleHarness"
    >
      <span v-if="busy === 'harness' || busy === 'stop'" class="spinner"></span>
      <span v-else-if="harnessUp" class="icon-stop" aria-hidden="true"></span>
      <span v-else class="icon-play" aria-hidden="true"></span>
    </button>

    <!-- ============ settings drawer ============ -->
    <div v-if="drawerOpen" class="scrim" @click="drawerOpen = false"></div>
    <aside class="drawer" :class="{ open: drawerOpen }">
      <header class="drawer-head">
        <h2>设置</h2>
        <button class="ghost" @click="drawerOpen = false">关闭</button>
      </header>

      <div class="drawer-body">
        <!-- runtime -->
        <section class="panel">
          <h3>运行环境</h3>
          <div class="row"><span class="label">本地 dsh</span><span class="value mono">{{ local?.dsh_version ?? "未检测到" }}</span></div>
          <div class="row"><span class="label">Node</span><span class="value mono">{{ local?.node_version ?? "—" }}</span></div>
          <div class="row"><span class="label">最新版本</span><span class="value mono" :class="{ hl: update?.has_update }">{{ update?.latest_version ?? "—" }}</span></div>
          <div class="row"><span class="label">上次检查</span><span class="value">{{ checkedText }}</span></div>
          <p v-if="local?.error" class="hint err">{{ local.error }}</p>
          <div class="btn-row">
            <button :disabled="!!busy" @click="checkUpdate">
              <span v-if="busy === 'check'" class="spinner"></span>检查更新
            </button>
            <button class="accent" :class="{ attention: update?.has_update }" :disabled="!!busy || !update?.has_update" @click="upgrade">
              <span v-if="busy === 'upgrade'" class="spinner"></span>一键升级
            </button>
          </div>
        </section>

        <!-- desktop app -->
        <section class="panel">
          <h3>桌面端（Electron）</h3>
          <p class="hint">桌面端不随 npm 包分发，需一份源码；该目录要已 <code>pnpm install</code>，首次启动会构建。</p>
          <div class="field">
            <input v-model="sourceDir" class="input mono" type="text" spellcheck="false" placeholder="源码根目录" @change="saveSourceDir" />
            <button class="btn-sm" @click="pickSourceDir">浏览…</button>
          </div>
          <div class="btn-row">
            <button class="danger-outline" :disabled="!!busy" @click="desktopUp ? stopDesktop() : startDesktop()">
              {{ desktopUp ? "停止桌面端" : "启动桌面端" }}
            </button>
          </div>
        </section>

        <!-- plugins -->
        <section class="panel">
          <h3>插件管理</h3>
          <div class="field">
            <select v-model="activeProfile" class="input mono" @change="refreshPlugins">
              <option v-for="p in profiles" :key="p.profile" :value="p.profile">{{ p.profile }}</option>
            </select>
            <button class="btn-sm" :disabled="!!busy" @click="upgradePlugins">全部升级</button>
          </div>
          <div v-for="p in currentProfile?.plugins ?? []" :key="p.name" class="row">
            <span class="label mono ellipsis" :title="p.name">{{ p.name }}</span>
            <span class="value mono">
              {{ p.installed || "未安装" }}
              <button class="btn-sm danger-outline" :disabled="!!busy" @click="removePlugin(p.name)">卸载</button>
            </span>
          </div>
          <p v-if="currentProfile && currentProfile.plugins.length === 0" class="hint">该 profile 没有第三方插件。</p>
          <div class="field">
            <input v-model="newPlugin" class="input mono" type="text" spellcheck="false" placeholder="要安装的包名" @keyup.enter="installPlugin" />
            <button class="btn-sm" :disabled="!!busy" @click="installPlugin">安装</button>
          </div>
          <p class="hint">
            装/卸后需重启 Web 端才生效。面板的用量统计不再依赖插件（内核自带 token 计量），
            可以安全卸载。
          </p>
          <div class="btn-row">
            <button :disabled="!!busy" @click="importUsage">
              <span v-if="busy === 'import'" class="spinner"></span>导入插件历史用量
            </button>
          </div>
        </section>

        <!-- feishu -->
        <section class="panel">
          <h3>飞书远程控制</h3>
          <p class="hint">
            在飞书里给机器人发消息 → 本机执行 <code>dsh --profile headless</code> → 结果回飞书。
            需要先建好飞书应用并 <code>lark-cli config init</code>。
          </p>
          <div class="field">
            <input v-model="bridgeUsers" class="input mono" type="text" spellcheck="false" placeholder="白名单 open_id（ou_ 开头，逗号分隔）" />
          </div>
          <div class="btn-row">
            <button :class="{ 'danger-outline': bridgeRunning }" :disabled="!!busy" @click="bridgeRunning ? stopBridge() : startBridge()">
              {{ bridgeRunning ? "停止桥" : "启动桥" }}
            </button>
            <span class="muted">{{ bridgeRunning ? "运行中" : "已停止" }}</span>
          </div>
        </section>

        <!-- hermes -->
        <section class="panel">
          <h3>Hermes 网关</h3>
          <div v-if="hermes?.ok">
            <div class="row"><span class="label">状态</span><span class="value" :class="{ hl: hermes.gateway_state === 'running' }">{{ hermes.gateway_state }}</span></div>
            <div class="row"><span class="label">版本 / PID</span><span class="value mono">{{ hermes.code_version }} · {{ hermes.pid }}</span></div>
            <div class="row"><span class="label">活跃 Agent</span><span class="value mono">{{ hermes.active_agents }}</span></div>
            <div v-for="p in hermes.platforms" :key="p.name" class="row">
              <span class="label">{{ p.name }}</span>
              <span class="value" :class="{ hl: p.state === 'connected', 'is-off': p.state !== 'connected' }">
                {{ p.state }}{{ p.needs_attention ? " · 需注意" : "" }}
              </span>
            </div>
          </div>
          <p v-else class="hint err">{{ hermes?.error ?? "读不到 Hermes 状态" }}</p>
          <div class="btn-row">
            <button @click="refreshHermes">刷新</button>
            <button @click="openHermes">打开面板</button>
          </div>
          <p class="hint">只读状态。启停请用 <code>docker compose --profile ai</code>。</p>
        </section>

        <!-- terminal -->
        <section class="panel">
          <h3>终端输出</h3>
          <pre ref="termEl" class="term">{{ termLines.length ? termLines.join("\n") : "（暂无输出）" }}</pre>
        </section>

        <section class="panel">
          <div class="btn-row">
            <button class="danger-outline" @click="quit">退出 DSH Panel</button>
          </div>
        </section>
      </div>
    </aside>

    <p v-if="toast" class="toast" :class="toast.kind">{{ toast.text }}</p>
  </div>
</template>
