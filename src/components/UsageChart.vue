<script setup lang="ts">
/**
 * Cost curve, drawn as plain SVG.
 *
 * No chart library on purpose: the shapes needed here are a polyline, an area
 * fill and axis labels, and hand-rolled SVG keeps the bundle small and the
 * styling inside our own token system.
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { UsagePoint } from "../types";

const props = defineProps<{
  points: UsagePoint[];
  bucket: string;
  /** Fixed plot height; when omitted the chart fills its container. */
  height?: number;
}>();

const W = 1000;
const PAD_L = 62;
const PAD_R = 18;
const PAD_T = 18;
const PAD_B = 36;

/** Fill the container so the panel does not leave a blank strip underneath. */
const wrap = ref<HTMLElement | null>(null);
const measured = ref(300);
let ro: ResizeObserver | null = null;

onMounted(() => {
  ro = new ResizeObserver(() => {
    const h = wrap.value?.clientHeight ?? 0;
    if (h > 120) measured.value = h;
  });
  if (wrap.value) ro.observe(wrap.value);
});

onUnmounted(() => ro?.disconnect());

const H = computed(() => props.height ?? measured.value);
const innerW = W - PAD_L - PAD_R;
const innerH = computed(() => H.value - PAD_T - PAD_B);

/** Tight "nice" upper bound - a plain 1/2/5/10 ladder jumps to 50 for a 24.5 peak. */
const maxCost = computed(() => {
  const max = Math.max(...props.points.map((p) => p.cost), 0);
  if (max <= 0) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(max)));
  const norm = max / mag;
  const ladder = [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
  const step = ladder.find((s) => s >= norm) ?? 10;
  return step * mag;
});

const stepX = computed(() =>
  props.points.length > 1 ? innerW / (props.points.length - 1) : 0,
);

function x(i: number): number {
  return PAD_L + i * stepX.value;
}

function y(v: number): number {
  return PAD_T + innerH.value - (v / maxCost.value) * innerH.value;
}

const linePath = computed(() =>
  props.points
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.cost).toFixed(1)}`)
    .join(" "),
);

const areaPath = computed(() => {
  if (props.points.length === 0) return "";
  const last = props.points.length - 1;
  const base = PAD_T + innerH.value;
  return `${linePath.value} L${x(last).toFixed(1)},${base} L${PAD_L},${base} Z`;
});

const ticks = computed(() =>
  [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    value: maxCost.value * f,
    y: y(maxCost.value * f),
  })),
);

const hover = ref<number | null>(null);
const hoverPoint = computed(() =>
  hover.value === null ? null : (props.points[hover.value] ?? null),
);

/** Axis labels get dense fast; thin them out when there are many buckets. */
const labelStep = computed(() =>
  props.points.length > 14 ? Math.ceil(props.points.length / 12) : 1,
);

function formatYen(v: number): string {
  if (v >= 100) return `¥${v.toFixed(0)}`;
  if (v >= 10) return `¥${v.toFixed(1)}`;
  return `¥${v.toFixed(2)}`;
}

function formatTokens(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return String(v);
}

function onMove(e: MouseEvent) {
  if (props.points.length === 0) return;
  const rect = (e.currentTarget as SVGElement).getBoundingClientRect();
  const px = ((e.clientX - rect.left) / rect.width) * W;
  const i = Math.round((px - PAD_L) / (stepX.value || 1));
  hover.value = Math.max(0, Math.min(props.points.length - 1, i));
}
</script>

<template>
  <div class="chart" ref="wrap">
    <svg
      :viewBox="`0 0 ${W} ${H}`"
      preserveAspectRatio="none"
      class="chart-svg"
      :style="{ height: `${H}px` }"
      @mousemove="onMove"
      @mouseleave="hover = null"
    >
      <defs>
        <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#3b82f6" stop-opacity="0.22" />
          <stop offset="100%" stop-color="#3b82f6" stop-opacity="0.02" />
        </linearGradient>
      </defs>

      <!-- gridlines + y labels -->
      <g v-for="t in ticks" :key="t.y">
        <line
          :x1="PAD_L"
          :x2="W - PAD_R"
          :y1="t.y"
          :y2="t.y"
          stroke="#e4e4e7"
          stroke-width="1"
          :stroke-dasharray="t.value === 0 ? '0' : '4 4'"
        />
        <text :x="PAD_L - 10" :y="t.y + 4" text-anchor="end" class="axis-label">
          {{ formatYen(t.value) }}
        </text>
      </g>

      <!-- x labels -->
      <text
        v-for="(p, i) in points"
        v-show="i % labelStep === 0 || i === points.length - 1"
        :key="p.key"
        :x="x(i)"
        :y="H - PAD_B + 22"
        text-anchor="middle"
        class="axis-label"
      >
        {{ p.label }}
      </text>

      <path :d="areaPath" fill="url(#areaFill)" />
      <path
        :d="linePath"
        fill="none"
        stroke="#3b82f6"
        stroke-width="2.5"
        stroke-linejoin="round"
        stroke-linecap="round"
      />

      <!-- points: hollow when the bucket had no usage, so a gap never reads as a real zero -->
      <circle
        v-for="(p, i) in points"
        :key="`d-${p.key}`"
        :cx="x(i)"
        :cy="y(p.cost)"
        :r="hover === i ? 5.5 : 3.5"
        :fill="p.calls > 0 ? '#3b82f6' : '#ffffff'"
        stroke="#3b82f6"
        stroke-width="2"
      />

      <line
        v-if="hover !== null"
        :x1="x(hover)"
        :x2="x(hover)"
        :y1="PAD_T"
        :y2="PAD_T + innerH"
        stroke="#a1a1aa"
        stroke-width="1"
        stroke-dasharray="3 3"
      />
    </svg>

    <div
      v-if="hoverPoint"
      class="chart-tip"
      :style="{ left: `${(x(hover!) / W) * 100}%` }"
    >
      <strong>{{ hoverPoint.key }}</strong>
      <span>花费 ¥{{ hoverPoint.cost.toFixed(4) }}</span>
      <span>token {{ formatTokens(hoverPoint.tokens) }}</span>
      <span>调用 {{ hoverPoint.calls }}</span>
    </div>
  </div>
</template>
