import { expect, test } from '@playwright/test';

/**
 * 端到端联调：前端真实调用 FastAPI 后端，
 * 采样在测试内按公式现算，不使用任何固定响应。
 */

function makeDecayPayload({ t60 = 1.5, dtMs = 1, n = 5000, limit = 1.0 } = {}) {
  const pressure = Array.from({ length: n }, (_, i) => {
    const t = (i * dtMs) / 1000;
    return 1000 * 10 ** ((-3 * t) / t60) + 0.25;
  });
  return { sample_interval_ms: dtMs, pressure, limit_seconds: limit };
}

async function submitPayload(page, payload) {
  await page.goto('/');
  await page.getByTestId('payload-input').fill(JSON.stringify(payload));
  await page.getByTestId('submit-btn').click();
}

test('理想衰减样本给出稳定 T20、取点数与斜率', async ({ page }) => {
  await submitPayload(page, makeDecayPayload());

  await expect(page.getByTestId('result-panel')).toBeVisible();
  await expect(page.getByTestId('t20-value')).toHaveText(/^0\.500 s$/);
  await expect(page.getByTestId('slope-value')).toHaveText(/^-40\.00/);
  const points = Number(await page.getByTestId('points-value').innerText());
  expect(points).toBeGreaterThanOrEqual(30);
  await expect(page.getByTestId('verdict')).toHaveText('合格');
});

test('同一采样重复上传得到完全相同的证据', async ({ page }) => {
  const payload = makeDecayPayload({ t60: 1.8, limit: 1.0 });

  await submitPayload(page, payload);
  await expect(page.getByTestId('result-panel')).toBeVisible();
  const firstT20 = await page.getByTestId('t20-value').innerText();
  const firstSlope = await page.getByTestId('slope-value').innerText();
  const firstPoints = await page.getByTestId('points-value').innerText();

  // 重新加载页面后再次提交同一采样
  await submitPayload(page, payload);
  await expect(page.getByTestId('result-panel')).toBeVisible();
  await expect(page.getByTestId('t20-value')).toHaveText(firstT20);
  await expect(page.getByTestId('slope-value')).toHaveText(firstSlope);
  await expect(page.getByTestId('points-value')).toHaveText(firstPoints);
});

test('T20 超过上限判定为不合格', async ({ page }) => {
  await submitPayload(page, makeDecayPayload({ t60: 1.5, limit: 0.3 }));
  await expect(page.getByTestId('result-panel')).toBeVisible();
  await expect(page.getByTestId('verdict')).toHaveText('不合格');
});

test('异常衰减只展示拒绝原因', async ({ page }) => {
  // 恒定声压：背景扣除后无有效衰减段
  const payload = {
    sample_interval_ms: 1,
    pressure: Array.from({ length: 1000 }, () => 5),
    limit_seconds: 1.0,
  };
  await submitPayload(page, payload);

  await expect(page.getByTestId('rejection-panel')).toBeVisible();
  await expect(page.getByTestId('rejection-reason')).toContainText('不足 30');
  await expect(page.getByTestId('result-panel')).toHaveCount(0);
  await expect(page.getByTestId('t20-value')).toHaveCount(0);
});

test('出错后不得保留旧结果', async ({ page }) => {
  // 先得到一次成功结论
  await submitPayload(page, makeDecayPayload());
  await expect(page.getByTestId('result-panel')).toBeVisible();

  // 再提交非法输入（采样数不足 200）
  await page.getByTestId('payload-input').fill(
    JSON.stringify({ sample_interval_ms: 1, pressure: [1, 2, 3], limit_seconds: 1 }),
  );
  await page.getByTestId('submit-btn').click();

  await expect(page.getByTestId('error-panel')).toBeVisible();
  await expect(page.getByTestId('result-panel')).toHaveCount(0);
  await expect(page.getByTestId('t20-value')).toHaveCount(0);
});

test('非 JSON 输入提示解析错误且清空旧结果', async ({ page }) => {
  await submitPayload(page, makeDecayPayload());
  await expect(page.getByTestId('result-panel')).toBeVisible();

  await page.getByTestId('payload-input').fill('这不是 JSON');
  await page.getByTestId('submit-btn').click();

  await expect(page.getByTestId('error-panel')).toBeVisible();
  await expect(page.getByTestId('error-message')).toContainText('JSON');
  await expect(page.getByTestId('result-panel')).toHaveCount(0);
});
