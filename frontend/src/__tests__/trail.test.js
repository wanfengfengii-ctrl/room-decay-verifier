import { describe, expect, it } from 'vitest';
import { TRAIL_MAX_PLOT_POINTS, buildTrailChart, readDecayTrail } from '../trail.js';

/** 构造线性衰减轨迹：db = -5 - 40t，t ∈ [0, 0.5]，step 个取样点。 */
function makeTrail({ count = 51, totalPoints = 500 } = {}) {
  const points = Array.from({ length: count }, (_, i) => {
    const t = (i / (count - 1)) * 0.5;
    return { time_seconds: t, db: -5 - 40 * t };
  });
  return {
    total_points: totalPoints,
    sampled_points: points,
    fit_line: { t_start: 0, db_start: -5, t_end: 0.5, db_end: -25 },
  };
}

describe('readDecayTrail', () => {
  it('合法轨迹归一化为展示模型', () => {
    const model = readDecayTrail({ status: 'ok', decay_trail: makeTrail() });
    expect(model.totalPoints).toBe(500);
    expect(model.points).toHaveLength(51);
    expect(model.points[0]).toEqual({ t: 0, db: -5 });
    expect(model.points.at(-1)).toEqual({ t: 0.5, db: -25 });
    expect(model.line).toEqual({ t0: 0, db0: -5, t1: 0.5, db1: -25 });
  });

  it('旧服务缺少 decay_trail 时返回 null（页面显示占位提示）', () => {
    expect(readDecayTrail({ status: 'ok', t20_seconds: 0.5 })).toBeNull();
    expect(readDecayTrail(null)).toBeNull();
    expect(readDecayTrail(undefined)).toBeNull();
  });

  it.each([
    ['total_points 缺失', { fit_line: {}, sampled_points: [] }],
    ['total_points 为字符串', { total_points: '500', sampled_points: [], fit_line: {} }],
    ['total_points 为 0', { total_points: 0, sampled_points: [], fit_line: {} }],
    ['sampled_points 不是数组', { total_points: 1, sampled_points: {}, fit_line: {} }],
    ['sampled_points 为空', { total_points: 1, sampled_points: [], fit_line: {} }],
    [
      '点字段不是有限数',
      { total_points: 1, sampled_points: [{ time_seconds: 0, db: 'x' }], fit_line: {} },
    ],
    [
      '时间非严格递增',
      {
        total_points: 2,
        sampled_points: [
          { time_seconds: 0, db: -5 },
          { time_seconds: 0, db: -6 },
        ],
        fit_line: { t_start: 0, db_start: -5, t_end: 0, db_end: -6 },
      },
    ],
    [
      '回归线端点非有限数',
      {
        total_points: 1,
        sampled_points: [{ time_seconds: 0, db: -5 }],
        fit_line: { t_start: 0, db_start: -5, t_end: 0.5, db_end: NaN },
      },
    ],
    [
      '回归线 t_end 不大于 t_start',
      {
        total_points: 1,
        sampled_points: [{ time_seconds: 0, db: -5 }],
        fit_line: { t_start: 0.5, db_start: -5, t_end: 0.5, db_end: -25 },
      },
    ],
    ['完整取点数少于展示点数', makeTrail({ count: 51, totalPoints: 50 })],
    ['展示点数超过 200', makeTrail({ count: TRAIL_MAX_PLOT_POINTS + 1, totalPoints: 1000 })],
  ])('字段损坏时不生成图形数据：%s', (_name, trail) => {
    expect(readDecayTrail({ decay_trail: trail })).toBeNull();
  });

  it('恰好 200 个展示点合法', () => {
    const model = readDecayTrail({
      decay_trail: makeTrail({ count: TRAIL_MAX_PLOT_POINTS, totalPoints: 500 }),
    });
    expect(model.points).toHaveLength(200);
  });
});

describe('buildTrailChart', () => {
  it('取样点与回归线映射到像素坐标，纵向 dB 向上增大', () => {
    const trail = readDecayTrail({ decay_trail: makeTrail() });
    const chart = buildTrailChart(trail);

    expect(chart.dots).toHaveLength(51);

    // 全部点落在绘图区内
    for (const dot of chart.dots) {
      expect(dot.x).toBeGreaterThanOrEqual(chart.plot.x - 1e-9);
      expect(dot.x).toBeLessThanOrEqual(chart.plot.x + chart.plot.width + 1e-9);
      expect(dot.y).toBeGreaterThanOrEqual(chart.plot.y - 1e-9);
      expect(dot.y).toBeLessThanOrEqual(chart.plot.y + chart.plot.height + 1e-9);
    }

    // 首末点贴在绘图区左右边界
    expect(chart.dots[0].x).toBeCloseTo(chart.plot.x, 10);
    expect(chart.dots.at(-1).x).toBeCloseTo(chart.plot.x + chart.plot.width, 10);

    // dB 越大 y 越小（-5 在上方、-25 在下方）
    expect(chart.dots[0].y).toBeLessThan(chart.dots.at(-1).y);

    // 回归线两端点与首末取样点重合（构造的线恰好穿过首末点）
    expect(chart.fitLine.x1).toBeCloseTo(chart.dots[0].x, 10);
    expect(chart.fitLine.y1).toBeCloseTo(chart.dots[0].y, 10);
    expect(chart.fitLine.x2).toBeCloseTo(chart.dots.at(-1).x, 10);
    expect(chart.fitLine.y2).toBeCloseTo(chart.dots.at(-1).y, 10);

    // 线中点对应 t=0.25、db=-15，恰在首末像素的中点
    const midIndex = 25;
    expect(chart.dots[midIndex].x).toBeCloseTo((chart.fitLine.x1 + chart.fitLine.x2) / 2, 10);
    expect(chart.dots[midIndex].y).toBeCloseTo((chart.fitLine.y1 + chart.fitLine.y2) / 2, 10);
  });

  it('回归线端点可以偏离取样点（端点严格来自完整回归线）', () => {
    // 服务端口径：即便取样首点不等于线端点，图上也必须画出服务端给的端点
    const trail = readDecayTrail({
      decay_trail: {
        total_points: 500,
        sampled_points: [
          { time_seconds: 0.01, db: -5.4 },
          { time_seconds: 0.49, db: -24.6 },
        ],
        fit_line: { t_start: 0, db_start: -5, t_end: 0.5, db_end: -25 },
      },
    });
    const chart = buildTrailChart(trail);
    // 线端点 x 由 t_start/t_end 决定，在首末取样点外侧
    expect(chart.fitLine.x1).toBeLessThan(chart.dots[0].x);
    expect(chart.fitLine.x2).toBeGreaterThan(chart.dots.at(-1).x);
    // 线端点 y 由 db_start/db_end 决定，高于/低于相邻取样点
    expect(chart.fitLine.y1).toBeLessThan(chart.dots[0].y);
    expect(chart.fitLine.y2).toBeGreaterThan(chart.dots.at(-1).y);
  });
});
