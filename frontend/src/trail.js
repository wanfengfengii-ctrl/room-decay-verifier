/**
 * 衰减轨迹的数据解析与图表几何（纯函数，组件只负责渲染）。
 *
 * 后端在单间与批量成功响应中附带 decay_trail：
 *   { total_points, sampled_points: [{time_seconds, db}],
 *     fit_line: {t_start, db_start, t_end, db_end} }
 * 斜率 / R² / T20 一律按完整窗口计算，取样点仅用于画图，
 * 因此这里的任何处理都不得回写计算证据。
 */

/** 与后端 TRAIL_MAX_PLOT_POINTS 同一口径：展示点上限。 */
export const TRAIL_MAX_PLOT_POINTS = 200;

export const CHART_LAYOUT = {
  width: 640,
  height: 300,
  paddingLeft: 48,
  paddingRight: 16,
  paddingTop: 16,
  paddingBottom: 38,
};

function isFiniteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * 归一化成功响应中的衰减轨迹：
 * - 旧服务不返回 decay_trail（或字段类型错误）：返回 null，
 *   入口显示「暂无衰减轨迹」，绝不凭半截字段构造图形数据；
 * - 合法时返回 { totalPoints, points: [{t, db}], line: {t0, db0, t1, db1} }。
 *
 * 拒绝响应与字段错误不渲染成功面板，自然不会生成图形；
 * 这里额外兜住成功响应里轨迹字段本身损坏的情况。
 */
export function readDecayTrail(result) {
  const trail = result?.decay_trail;
  if (!trail || typeof trail !== 'object') return null;

  const { total_points: totalPoints, sampled_points: rawPoints, fit_line: line } = trail;
  if (!Number.isInteger(totalPoints) || totalPoints <= 0) return null;
  if (!Array.isArray(rawPoints) || rawPoints.length === 0) return null;
  if (rawPoints.length > TRAIL_MAX_PLOT_POINTS) return null;

  const points = [];
  for (const raw of rawPoints) {
    if (!raw || !isFiniteNumber(raw.time_seconds) || !isFiniteNumber(raw.db)) return null;
    points.push({ t: raw.time_seconds, db: raw.db });
  }
  // 等距取样自严格递增的峰值后时间：时间必须严格递增
  for (let i = 1; i < points.length; i += 1) {
    if (!(points[i].t > points[i - 1].t)) return null;
  }

  if (
    !line ||
    typeof line !== 'object' ||
    !isFiniteNumber(line.t_start) ||
    !isFiniteNumber(line.db_start) ||
    !isFiniteNumber(line.t_end) ||
    !isFiniteNumber(line.db_end)
  ) {
    return null;
  }
  if (!(line.t_end > line.t_start)) return null;
  // 完整取点数不得少于展示点数（后端口径：取样只删不增）
  if (totalPoints < points.length) return null;

  return {
    totalPoints,
    points,
    line: { t0: line.t_start, db0: line.db_start, t1: line.t_end, db1: line.db_end },
  };
}

const Y_TICKS = [-5, -10, -15, -20, -25];
const X_TICK_COUNT = 5;

/**
 * 由归一化轨迹构造 SVG 几何：取样点像素坐标、完整回归线两端像素坐标、
 * 坐标轴刻度与文本。线性缩放，y 轴向上为 dB 增大方向。
 */
export function buildTrailChart(trail, layout = CHART_LAYOUT) {
  const xValues = trail.points.map((p) => p.t).concat([trail.line.t0, trail.line.t1]);
  const yValues = trail.points.map((p) => p.db).concat([trail.line.db0, trail.line.db1]);
  const xMin = Math.min(...xValues);
  const xMax = Math.max(...xValues);
  const dataYMin = Math.min(...yValues);
  const dataYMax = Math.max(...yValues);
  // 纵向留 1 dB 余量，避免首末点贴在边框上
  const yMin = dataYMin - 1;
  const yMax = dataYMax + 1;

  const innerWidth = layout.width - layout.paddingLeft - layout.paddingRight;
  const innerHeight = layout.height - layout.paddingTop - layout.paddingBottom;
  const xSpan = xMax - xMin || 1;
  const ySpan = yMax - yMin || 1;

  const scaleX = (t) => layout.paddingLeft + ((t - xMin) / xSpan) * innerWidth;
  const scaleY = (db) => layout.paddingTop + ((yMax - db) / ySpan) * innerHeight;

  const dots = trail.points.map((p) => ({ x: scaleX(p.t), y: scaleY(p.db), t: p.t, db: p.db }));
  const fitLine = {
    x1: scaleX(trail.line.t0),
    y1: scaleY(trail.line.db0),
    x2: scaleX(trail.line.t1),
    y2: scaleY(trail.line.db1),
  };

  const yTicks = Y_TICKS.filter((db) => db >= dataYMin && db <= dataYMax).map((db) => ({
    value: db,
    y: scaleY(db),
    label: String(db),
  }));
  const xTicks = Array.from({ length: X_TICK_COUNT }, (_, i) => {
    const t = xMin + (i / (X_TICK_COUNT - 1)) * (xMax - xMin);
    return { value: t, x: scaleX(t), label: `${Number(t.toFixed(3))}` };
  });

  return {
    width: layout.width,
    height: layout.height,
    plot: {
      x: layout.paddingLeft,
      y: layout.paddingTop,
      width: innerWidth,
      height: innerHeight,
    },
    dots,
    fitLine,
    yTicks,
    xTicks,
    domain: { xMin, xMax, yMin, yMax },
  };
}
