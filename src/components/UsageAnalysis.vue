<script setup lang="ts">
/**
 * Usage analysis block: metric cards, model share donut, per-model table and
 * the top sessions. Data comes from the `usage_analysis` command, which folds
 * the local per-day store for a chosen range.
 */
import { computed, onMounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";

interface Bucket {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  reasoning_tokens: number;
  cost: number;
  peak_cost: number;
  offpeak_cost: number;
  peak_calls: number;
}

interface ModelRow {
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  reasoning_tokens: number;
  peak_cost: number;
  offpeak_cost: number;
  cost: number;
  cache_hit_pct: number;
  cost_share: number;
}

interface SessionRow {
  session: string;
  calls: number;
  tokens: number;
  cost: number;
}

interface Analysis {
  ok: boolean;
  range_days: number;
  range_label: string;
  total: Bucket;
  cache_hit_pct: number;
  today_cost: number;
  month_cost: number;
  month_elapsed_days: number;
  month_total_days: number;
  month_projection: number;
  models: ModelRow[];
  sessions: SessionRow[];
  first_date: string;
  last_date: string;
  error: string | null;
}

const props = defineProps<{ range: number }>();

const data = ref<Analysis | null>(null);
const failed = ref<string | null>(null);

async function load() {
  try {
    data.value = await invoke<Analysis>("usage_analysis", { days: props.range });
    failed.value = null;
  } catch (e) {
    failed.value = String(e);
  }
}

defineExpose({ load });
onMounted(load);

function fmtTokens(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function fmtCost(n: number): string {
  return `¥${n.toFixed(2)}`;
}

/** Donut geometry: strokes on a circle, one dash per model. */
const DONUT_R = 54;
const DONUT_C = 2 * Math.PI * DONUT_R;
const PALETTE = ["#3b82f6", "#f59e0b", "#10b981", "#8b5cf6", "#f43f5e", "#06b6d4", "#a1a1aa"];

const donut = computed(() => {
  const models = data.value?.models ?? [];
  const totalCost = models.reduce((s, m) => s + m.cost, 0);
  let offset = 0;
  return models.slice(0, 7).map((m, i) => {
    const frac = totalCost > 0 ? m.cost / totalCost : 0;
    const seg = {
      model: m.model,
      color: PALETTE[i % PALETTE.length],
      pct: frac * 100,
      cost: m.cost,
      dash: `${(frac * DONUT_C).toFixed(2)} ${DONUT_C.toFixed(2)}`,
      offset: (-offset * DONUT_C).toFixed(2),
    };
    offset += frac;
    return seg;
  });
});

const maxSessionCost = computed(() =>
  Math.max(...(data.value?.sessions ?? []).map((s) => s.cost), 0.0001),
);

/** Shorten a session id for the table; keep the tail which is the unique part. */
function shortSession(id: string): string {
  const tail = id.split("-").slice(-2).join("-");
  return `…${tail}`;
}
</script>

<template>
  <div class="analysis">
    <p v-if="!data?.ok" class="empty">
      {{ failed ?? data?.error ?? "还没有用量数据。" }}
    </p>

    <template v-else>
      <!-- metric cards -->
      <section class="metric-row">
        <div class="metric metric-accent">
          <span class="metric-label">{{ data.range_label }}总花费</span>
          <span class="metric-value">{{ fmtCost(data.total.cost) }}</span>
          <span class="metric-sub">
            高峰 {{ fmtCost(data.total.peak_cost) }} · 低谷 {{ fmtCost(data.total.offpeak_cost) }}
          </span>
        </div>
        <div class="metric">
          <span class="metric-label">调用次数</span>
          <span class="metric-value">{{ data.total.calls.toLocaleString() }}</span>
          <span class="metric-sub">
            高峰 {{ data.total.peak_calls.toLocaleString() }} · 低谷
            {{ (data.total.calls - data.total.peak_calls).toLocaleString() }}
          </span>
        </div>
        <div class="metric">
          <span class="metric-label">缓存命中率</span>
          <span class="metric-value">{{ data.cache_hit_pct.toFixed(1) }}%</span>
          <span class="metric-sub">命中 {{ fmtTokens(data.total.cache_read_tokens) }} token</span>
        </div>
        <div class="metric">
          <span class="metric-label">本月预测</span>
          <span class="metric-value">{{ fmtCost(data.month_projection) }}</span>
          <span class="metric-sub">
            已花 {{ fmtCost(data.month_cost) }} · 第 {{ data.month_elapsed_days }}/{{
              data.month_total_days
            }} 天
          </span>
        </div>
      </section>

      <!-- token buckets -->
      <section class="panel token-panel">
        <div class="token-grid">
          <div class="token-cell">
            <span class="token-label">输入（未命中）</span>
            <span class="token-value mono">{{ data.total.input_tokens.toLocaleString() }}</span>
          </div>
          <div class="token-cell">
            <span class="token-label">缓存命中</span>
            <span class="token-value mono">{{ data.total.cache_read_tokens.toLocaleString() }}</span>
          </div>
          <div class="token-cell">
            <span class="token-label">输出</span>
            <span class="token-value mono">{{ data.total.output_tokens.toLocaleString() }}</span>
          </div>
          <div class="token-cell">
            <span class="token-label">缓存写入</span>
            <span class="token-value mono">{{ data.total.cache_write_tokens.toLocaleString() }}</span>
          </div>
          <div class="token-cell">
            <span class="token-label">推理</span>
            <span class="token-value mono">{{ data.total.reasoning_tokens.toLocaleString() }}</span>
          </div>
          <div class="token-cell">
            <span class="token-label">今日花费</span>
            <span class="token-value mono">{{ fmtCost(data.today_cost) }}</span>
          </div>
        </div>
        <p class="hint">
          缓存写入不计入合计：记录器把它与「未命中」记成同一个值（未命中的部分正是被写入缓存的），
          相加会重复计算。
        </p>
      </section>

      <!-- model split: donut + table -->
      <section class="split">
        <div class="panel donut-panel">
          <h3>模型消耗占比</h3>
          <div class="donut-wrap">
            <svg viewBox="0 0 140 140" class="donut">
              <circle cx="70" cy="70" :r="DONUT_R" fill="none" stroke="#f4f4f5" stroke-width="18" />
              <circle
                v-for="seg in donut"
                :key="seg.model"
                cx="70"
                cy="70"
                :r="DONUT_R"
                fill="none"
                :stroke="seg.color"
                stroke-width="18"
                :stroke-dasharray="seg.dash"
                :stroke-dashoffset="seg.offset"
                transform="rotate(-90 70 70)"
              />
              <text x="70" y="66" text-anchor="middle" class="donut-title">
                {{ fmtCost(data.total.cost) }}
              </text>
              <text x="70" y="84" text-anchor="middle" class="donut-sub">合计</text>
            </svg>
            <ul class="donut-legend">
              <li v-for="seg in donut" :key="seg.model">
                <i :style="{ background: seg.color }"></i>
                <span class="mono ellipsis" :title="seg.model">{{ seg.model }}</span>
                <b>{{ seg.pct.toFixed(1) }}%</b>
              </li>
            </ul>
          </div>
        </div>

        <div class="panel table-panel">
          <h3>模型消耗明细（{{ data.models.length }} 个模型）</h3>
          <div class="table-scroll">
            <table class="data-table">
              <thead>
                <tr>
                  <th>模型</th>
                  <th class="num">调用</th>
                  <th class="num">输入</th>
                  <th class="num">命中</th>
                  <th class="num">输出</th>
                  <th class="num">推理</th>
                  <th class="num">高峰</th>
                  <th class="num">低谷</th>
                  <th class="num">总花费</th>
                  <th class="share-col">占比</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="m in data.models" :key="m.model">
                  <td class="mono ellipsis" :title="m.model">{{ m.model }}</td>
                  <td class="num">{{ m.calls.toLocaleString() }}</td>
                  <td class="num">{{ fmtTokens(m.input_tokens) }}</td>
                  <td class="num">{{ fmtTokens(m.cache_read_tokens) }}</td>
                  <td class="num">{{ fmtTokens(m.output_tokens) }}</td>
                  <td class="num">{{ fmtTokens(m.reasoning_tokens) }}</td>
                  <td class="num">{{ fmtCost(m.peak_cost) }}</td>
                  <td class="num">{{ fmtCost(m.offpeak_cost) }}</td>
                  <td class="num strong">{{ fmtCost(m.cost) }}</td>
                  <td class="share-col">
                    <div class="share-bar">
                      <span :style="{ width: `${m.cost_share}%` }"></span>
                    </div>
                    <span class="share-text">{{ m.cost_share.toFixed(1) }}%</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- top sessions -->
      <section v-if="data.sessions.length" class="panel">
        <h3>会话消耗排行 Top {{ data.sessions.length }}</h3>
        <div v-for="s in data.sessions" :key="s.session" class="session-row">
          <span class="session-id mono ellipsis" :title="s.session">{{ shortSession(s.session) }}</span>
          <div class="session-bar">
            <span :style="{ width: `${(s.cost / maxSessionCost) * 100}%` }"></span>
          </div>
          <span class="session-meta mono">{{ s.calls }} 次 · {{ fmtTokens(s.tokens) }}</span>
          <span class="session-cost mono">{{ fmtCost(s.cost) }}</span>
        </div>
      </section>
    </template>
  </div>
</template>
