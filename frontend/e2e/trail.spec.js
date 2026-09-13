import { expect, test } from '@playwright/test';
import { CHART_LAYOUT, buildTrailChart, readDecayTrail } from '../src/trail.js';

/**
 * 衰减轨迹端到端：真实调用 FastAPI，采样在测试内按公式现算。
 * 与后端 / verify 同一复核口径的独立 oracle（不复用后端代码）。
 */

function makeDecayPayload({ t60 = 1.5, dtMs = 1, n = 5000, limit = 1.0, offset = 0.25 } = {}) {
  const pressure = Array.from({ length: n }, (_, i) => {
    const t = (i * dtMs) / 1000;
    return 1000 * 10 ** ((-3 * t) / t60) + offset;
  });
  return { sample_interval_ms: dtMs, pressure, limit_seconds: limit };
}

/** 独立复算完整 [-25,-5] dB 窗口、OLS 回归线与轨迹端点。 */
function oracleTrail(payload) {
  const { sample_interval_ms: dtMs, pressure } = payload;
  const n = pressure.length;
  const dt = dtMs / 1000;
  const tail = Math.ceil(n * 0.1);
  let background = 0;
  for (let i = n - tail; i < n; i += 1) background += pressure[i];
  background /= tail;

  let peak = 0;
  for (let i = 1; i < n; i += 1) {
    if (pressure[i] > pressure[peak]) peak = i;
  }
  const corrected = [];
  for (let i = peak; i < n; i += 1) {
    const value = pressure[i] - background;
    if (value > 0) corrected.push([(i - peak) * dt, value]);
  }
  const reference = Math.max(...corrected.map(([, v]) => v));
  const window = corrected
    .map(([t, v]) => [t, 20 * Math.log10(v / reference)])
    .filter(([, db]) => db >= -25 && db <= -5);

  const m = window.length;
  const xBar = window.reduce((s, [t]) => s + t, 0) / m;
  const yBar = window.reduce((s, [, db]) => s + db, 0) / m;
  let sxx = 0;
  let sxy = 0;
  for (const [t, db] of window) {
    sxx += (t - xBar) ** 2;
    sxy += (t - xBar) * (db - yBar);
  }
  const slope = sxy / sxx;
  const intercept = yBar - slope * xBar;
  const tStart = window[0][0];
  const tEnd = window[m - 1][0];
  return {
    window,
    slope,
    fitLine: {
      t_start: tStart,
      db_start: intercept + slope * tStart,
      t_end: tEnd,
      db_end: intercept + slope * tEnd,
    },
  };
}

/** 等距取样口径与后端 sample_trail_indices 一致：round(k*(total-1)/199)。 */
function expectedSampledIndices(total, limit = 200) {
  if (total <= limit) return Array.from({ length: total }, (_, i) => i);
  return Array.from({ length: limit }, (_, k) => Math.round((k * (total - 1)) / (limit - 1)));
}

async function submitPayload(page, payload) {
  await page.goto('/');
  await page.getByTestId('payload-input').fill(JSON.stringify(payload));
  await page.getByTestId('submit-btn').click();
}

async function submitText(page, text) {
  await page.getByTestId('payload-input').fill(text);
  await page.getByTestId('submit-btn').click();
}

test('超过 200 点时轨迹按等距索引取样且首尾必留、索引不重复', async ({ page }) => {
  const payload = makeDecayPayload(); // 窗口约 500 点
  const oracle = oracleTrail(payload);
  expect(oracle.window.length).toBeGreaterThan(200);

  const responsePromise = page.waitForResponse(
    (r) => r.url().includes('/api/evaluate') && r.request().method() === 'POST',
  );
  await submitPayload(page, payload);
  await expect(page.getByTestId('result-panel')).toBeVisible();
  const body = await (await responsePromise).json();

  const trail = body.decay_trail;
  expect(trail.total_points).toBe(oracle.window.length);
  expect(trail.sampled_points).toHaveLength(200);

  // 取样包含完整窗口首末点
  expect(trail.sampled_points[0].time_seconds).toBe(oracle.window[0][0]);
  expect(trail.sampled_points[0].db).toBeCloseTo(oracle.window[0][1], 10);
  expect(trail.sampled_points.at(-1).time_seconds).toBe(oracle.window.at(-1)[0]);
  expect(trail.sampled_points.at(-1).db).toBeCloseTo(oracle.window.at(-1)[1], 10);

  // 等距索引、不重复：每个展示点都能在完整窗口的预期索引上找到
  const indices = expectedSampledIndices(oracle.window.length);
  expect(new Set(indices).size).toBe(200);
  trail.sampled_points.forEach((point, k) => {
    const [t, db] = oracle.window[indices[k]];
    expect(point.time_seconds).toBe(t);
    // Python/JS libm 的 log10 末位可能有 1 ULP 差异，按容差比对
    expect(point.db).toBeCloseTo(db, 10);
  });

  // 图旁标明完整取点数与已展示点数
  await page.getByTestId('trail-toggle').click();
  await expect(page.getByTestId('trail-panel')).toBeVisible();
  await expect(page.getByTestId('trail-total')).toHaveText(String(oracle.window.length));
  await expect(page.getByTestId('trail-shown')).toHaveText('200');
  await expect(page.getByTestId('trail-dots').locator('circle')).toHaveCount(200);
});

test('图表拟合线端点来自完整回归线（非取样折线）', async ({ page }) => {
  const payload = makeDecayPayload();
  const oracle = oracleTrail(payload);

  const responsePromise = page.waitForResponse(
    (r) => r.url().includes('/api/evaluate') && r.request().method() === 'POST',
  );
  await submitPayload(page, payload);
  const body = await (await responsePromise).json();

  // 服务端 fit_line 与独立 oracle 的完整窗口回归线端点逐值一致
  const line = body.decay_trail.fit_line;
  expect(line.t_start).toBe(oracle.fitLine.t_start);
  expect(line.db_start).toBeCloseTo(oracle.fitLine.db_start, 10);
  expect(line.t_end).toBe(oracle.fitLine.t_end);
  expect(line.db_end).toBeCloseTo(oracle.fitLine.db_end, 10);

  await page.getByTestId('trail-toggle').click();
  await expect(page.getByTestId('trail-chart')).toBeVisible();

  // DOM 中拟合线像素端点 = 用服务端 fit_line 经同一几何映射得到的位置。
  // React 把数值属性序列化成最短字符串（如 "48" 而非 "48.000"），故读回数值比对。
  const chart = buildTrailChart(readDecayTrail(body), CHART_LAYOUT);
  const domLine = page.getByTestId('trail-fitline');
  for (const attr of ['x1', 'y1', 'x2', 'y2']) {
    const value = Number(await domLine.getAttribute(attr));
    expect(value).toBeCloseTo(chart.fitLine[attr], 6);
  }
});

test('展开与收起轨迹不触发重复请求，收起后本次结果不丢失', async ({ page }) => {
  const payload = makeDecayPayload();
  await submitPayload(page, payload);
  await expect(page.getByTestId('result-panel')).toBeVisible();

  const requests = [];
  page.on('request', (req) => {
    if (req.url().includes('/api/evaluate')) requests.push(req.method());
  });

  const toggle = page.getByTestId('trail-toggle');

  await toggle.click();
  await expect(page.getByTestId('trail-chart')).toBeVisible();
  await toggle.click(); // 收起
  await expect(page.getByTestId('trail-chart')).toHaveCount(0);
  await toggle.click(); // 再次展开
  await expect(page.getByTestId('trail-chart')).toBeVisible();
  await expect(page.getByTestId('trail-dots').locator('circle')).toHaveCount(200);

  // 原结论仍在
  await expect(page.getByTestId('verdict')).toHaveText('合格');
  // 展开 / 收起 / 再展开全程没有新请求
  expect(requests).toEqual([]);
});

test('不超过 200 点时展示全部窗口点', async ({ page }) => {
  // t60=15s、dt=100ms：dB 斜率 -4/s，20 dB 窗口约 50 个点
  const payload = makeDecayPayload({ t60: 15, dtMs: 100, n: 2000 });
  const oracle = oracleTrail(payload);
  expect(oracle.window.length).toBeGreaterThanOrEqual(30);
  expect(oracle.window.length).toBeLessThanOrEqual(200);

  await submitPayload(page, payload);
  await page.getByTestId('trail-toggle').click();
  await expect(page.getByTestId('trail-total')).toHaveText(String(oracle.window.length));
  await expect(page.getByTestId('trail-shown')).toHaveText(String(oracle.window.length));
  await expect(page.getByTestId('trail-dots').locator('circle')).toHaveCount(oracle.window.length);
});

test('旧服务缺少轨迹字段时入口显示暂无衰减轨迹，不生成图形', async ({ page }) => {
  await page.route('**/api/evaluate', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        t20_seconds: 0.5,
        points_used: 500,
        slope: -40,
        background: 0.25,
        peak_index: 0,
        limit_seconds: 1.0,
        passed: true,
        r_squared: 1.0,
        fit_quality: 'stable',
      }),
    }),
  );

  await submitPayload(page, makeDecayPayload());
  await expect(page.getByTestId('result-panel')).toBeVisible();
  await expect(page.getByTestId('decay-trail-empty')).toBeVisible();
  await expect(page.getByTestId('trail-placeholder')).toHaveText('暂无衰减轨迹');
  await expect(page.getByTestId('trail-chart')).toHaveCount(0);
  // 原结论不受影响
  await expect(page.getByTestId('verdict')).toHaveText('合格');
});

test('轨迹字段损坏时同样显示占位提示', async ({ page }) => {
  await page.route('**/api/evaluate', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        t20_seconds: 0.5,
        points_used: 500,
        slope: -40,
        background: 0.25,
        peak_index: 0,
        limit_seconds: 1.0,
        passed: true,
        r_squared: 1.0,
        fit_quality: 'stable',
        decay_trail: { total_points: 500, sampled_points: [], fit_line: null },
      }),
    }),
  );

  await submitPayload(page, makeDecayPayload());
  await expect(page.getByTestId('trail-placeholder')).toHaveText('暂无衰减轨迹');
  await expect(page.getByTestId('trail-chart')).toHaveCount(0);
});

test('拒绝后清除上一张轨迹图，且拒绝结论不生成图形', async ({ page }) => {
  // 先成功并展开轨迹
  await submitPayload(page, makeDecayPayload());
  await page.getByTestId('trail-toggle').click();
  await expect(page.getByTestId('trail-chart')).toBeVisible();

  // 再提交恒定声压：衰减异常被拒绝
  await submitText(
    page,
    JSON.stringify({ sample_interval_ms: 1, pressure: Array(1000).fill(5), limit_seconds: 1 }),
  );
  await expect(page.getByTestId('rejection-panel')).toBeVisible();
  await expect(page.getByTestId('result-panel')).toHaveCount(0);
  await expect(page.getByTestId('trail-chart')).toHaveCount(0);
  await expect(page.getByTestId('trail-toggle')).toHaveCount(0);
});

test('字段错误请求失败后清除上一张轨迹图', async ({ page }) => {
  await submitPayload(page, makeDecayPayload());
  await page.getByTestId('trail-toggle').click();
  await expect(page.getByTestId('trail-chart')).toBeVisible();

  // pressure 不足 200：后端 422，前端进入提交失败
  await submitText(
    page,
    JSON.stringify({ sample_interval_ms: 1, pressure: [1, 2, 3], limit_seconds: 1 }),
  );
  await expect(page.getByTestId('error-panel')).toBeVisible();
  await expect(page.getByTestId('result-panel')).toHaveCount(0);
  await expect(page.getByTestId('trail-chart')).toHaveCount(0);
});

test('新的成功提交替换旧轨迹', async ({ page }) => {
  // 第一次：约 500 窗口点 -> 展示 200
  await submitPayload(page, makeDecayPayload({ t60: 1.5 }));
  await page.getByTestId('trail-toggle').click();
  await expect(page.getByTestId('trail-shown')).toHaveText('200');

  // 第二次：慢衰减、粗采样，窗口不足 200 -> 展示全部点
  await submitPayload(page, makeDecayPayload({ t60: 15, dtMs: 100, n: 2000 }));
  await expect(page.getByTestId('result-panel')).toBeVisible();
  await page.getByTestId('trail-toggle').click();
  const shown = Number(await page.getByTestId('trail-shown').innerText());
  expect(shown).toBeGreaterThanOrEqual(30);
  expect(shown).toBeLessThanOrEqual(200);
  await expect(page.getByTestId('trail-dots').locator('circle')).toHaveCount(shown);
});
