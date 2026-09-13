/** 展示格式化辅助：T20 三位小数，斜率六位小数。 */

export function formatT20(value) {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toFixed(3);
}

export function formatSlope(value) {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toFixed(6);
}

export function formatSeconds(value) {
  if (value == null || Number.isNaN(value)) return '—';
  return `${value.toFixed(3)} s`;
}
