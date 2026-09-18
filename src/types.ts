/** One point of the usage curve (day / ISO week / calendar month bucket). */
export interface UsagePoint {
  /** Axis label, e.g. "09-18" · "第38周" · "09月" */
  label: string;
  /** Bucket key, e.g. "2026-09-18" · "2026-W38" · "2026-09" */
  key: string;
  cost: number;
  tokens: number;
  calls: number;
}
