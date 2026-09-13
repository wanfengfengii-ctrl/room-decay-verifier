import { expect, test } from '@playwright/test';

/**
 * 批量复核端到端联调：前端真实调用 FastAPI 后端，
 * 覆盖混合批次顺序、部分失败不遮蔽、汇总口径、新提交清空旧批次、重复标识整批拒绝。
 */

function decayPressure({ t60 = 1.5, dtMs = 1, n = 5000, offset = 0.25 } = {}) {
  return Array.from({ length: n }, (_, i) => {
    const t = (i * dtMs) / 1000;
    return 1000 * 10 ** ((-3 * t) / t60) + offset;
  });
}

function okRoom(roomId, { limit = 1.0, t60 = 1.5 } = {}) {
  return {
    room_id: roomId,
    sample_interval_ms: 1,
    pressure: decayPressure({ t60 }),
    limit_seconds: limit,
  };
}

async function gotoBatch(page) {
  await page.goto('/');
  await page.getByTestId('mode-batch').click();
}

async function submitBatch(page, payload) {
  await page.getByTestId('batch-input').fill(JSON.stringify(payload));
  await page.getByTestId('batch-submit-btn').click();
}

async function gotoSingleAndSubmit(page, payload) {
  await page.goto('/');
  await page.getByTestId('payload-input').fill(JSON.stringify(payload));
  await page.getByTestId('submit-btn').click();
  await expect(page.getByTestId('result-panel')).toBeVisible();
  return {
    t20: await page.getByTestId('t20-value').innerText(),
    slope: await page.getByTestId('slope-value').innerText(),
    points: await page.getByTestId('points-value').innerText(),
  };
}

test('混合批次按文件顺序展示三类行且只汇总正常结论', async ({ page }) => {
  const payload = {
    items: [
      okRoom('ROOM-OK-PASS', { limit: 1.0 }),
      okRoom('ROOM-OK-FAIL', { limit: 0.3 }),
      {
        room_id: 'ROOM-REJECTED',
        sample_interval_ms: 1,
        pressure: Array.from({ length: 1000 }, () => 5),
        limit_seconds: 1.0,
      },
      {
        room_id: 'ROOM-INVALID',
        sample_interval_ms: 0,
        pressure: decayPressure(),
        limit_seconds: 1.0,
      },
    ],
  };

  await gotoBatch(page);
  await submitBatch(page, payload);

  const rows = page.getByTestId('batch-row');
  await expect(rows).toHaveCount(4);

  // 顺序稳定：房间标识与状态一一对应
  await expect(page.getByTestId('row-room-id')).toHaveText([
    'ROOM-OK-PASS',
    'ROOM-OK-FAIL',
    'ROOM-REJECTED',
    'ROOM-INVALID',
  ]);
  await expect(page.getByTestId('row-status')).toHaveText(['正常', '正常', '衰减异常', '字段错误']);

  // 合格 / 不合格判定
  const verdicts = page.getByTestId('row-verdict');
  await expect(verdicts.nth(0)).toHaveText('合格');
  await expect(verdicts.nth(1)).toHaveText('不合格');
  await expect(verdicts.nth(2)).toHaveText('不计入');
  await expect(verdicts.nth(3)).toHaveText('不计入');

  // 衰减异常行只有拒绝原因；字段错误行有可定位说明
  await expect(page.getByTestId('row-reason')).toHaveText([/不足 30/]);
  await expect(page.getByTestId('row-errors')).toHaveText([/sample_interval_ms/]);

  // 汇总只统计正常项：2 间正常（1 合格 1 不合格），异常与错误各 1
  const summary = page.getByTestId('batch-summary');
  await expect(summary).toContainText('共 4 间');
  await expect(summary).toContainText('正常 2 间');
  await expect(summary.locator('.pass')).toHaveText('1');
  await expect(summary.locator('.fail')).toHaveText('1');
  await expect(summary).toContainText('衰减异常 1 间、字段错误 1 间');
});

test('正常行证据与单间入口完全一致', async ({ page }) => {
  const room = okRoom('ROOM-EVIDENCE', { limit: 1.0, t60: 1.8 });
  const single = await gotoSingleAndSubmit(page, {
    sample_interval_ms: 1,
    pressure: room.pressure,
    limit_seconds: 1.0,
  });

  await page.getByTestId('mode-batch').click();
  await submitBatch(page, { items: [room] });

  await expect(page.getByTestId('row-t20')).toHaveText(single.t20);
  await expect(page.getByTestId('row-slope')).toHaveText(single.slope);
  await expect(page.getByTestId('row-points')).toHaveText(single.points);
});

test('错误行不会遮蔽其余房间的有效结果', async ({ page }) => {
  await gotoBatch(page);
  await submitBatch(page, {
    items: [
      okRoom('FIRST-OK'),
      { room_id: 'BROKEN', sample_interval_ms: 9, pressure: [1, 2, 3], limit_seconds: 1 },
      okRoom('LAST-OK', { limit: 0.3 }),
    ],
  });

  const rows = page.getByTestId('batch-row');
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toHaveClass(/row-ok/);
  await expect(rows.nth(1)).toHaveClass(/row-invalid/);
  await expect(rows.nth(2)).toHaveClass(/row-ok/);
  await expect(rows.nth(0).getByTestId('row-verdict')).toHaveText('合格');
  await expect(rows.nth(2).getByTestId('row-verdict')).toHaveText('不合格');
  await expect(rows.nth(1).getByTestId('row-errors')).toContainText('pressure');
});

test('room_id 重复时整批拒绝且不残留任何行', async ({ page }) => {
  await gotoBatch(page);
  // 先提交一组合法批次，确认旧数据存在
  await submitBatch(page, { items: [okRoom('OLD-1'), okRoom('OLD-2')] });
  await expect(page.getByTestId('batch-row')).toHaveCount(2);

  // 再提交带重复 room_id 的批次：请求级错误，旧批次被清空
  await submitBatch(page, { items: [okRoom('DUP'), okRoom('DUP')] });
  await expect(page.getByTestId('batch-error-panel')).toBeVisible();
  await expect(page.getByTestId('batch-error-message')).toContainText('重复');
  await expect(page.getByTestId('batch-table')).toHaveCount(0);
  await expect(page.getByTestId('batch-summary')).toHaveCount(0);
});

test('新批次提交即清空上一批结果', async ({ page }) => {
  await gotoBatch(page);
  await submitBatch(page, { items: [okRoom('OLD-A'), okRoom('OLD-B'), okRoom('OLD-C')] });
  await expect(page.getByTestId('batch-row')).toHaveCount(3);

  await submitBatch(page, { items: [okRoom('NEW-A')] });
  const rows = page.getByTestId('batch-row');
  await expect(rows).toHaveCount(1);
  await expect(page.getByTestId('row-room-id')).toHaveText(['NEW-A']);
  await expect(page.getByTestId('batch-summary')).toContainText('共 1 间');
});

test('非 JSON 输入提示解析错误并清空旧批次', async ({ page }) => {
  await gotoBatch(page);
  await submitBatch(page, { items: [okRoom('OLD-A')] });
  await expect(page.getByTestId('batch-row')).toHaveCount(1);

  await page.getByTestId('batch-input').fill('不是合法 JSON');
  await page.getByTestId('batch-submit-btn').click();

  await expect(page.getByTestId('batch-error-panel')).toBeVisible();
  await expect(page.getByTestId('batch-error-message')).toContainText('JSON');
  await expect(page.getByTestId('batch-table')).toHaveCount(0);
});

test('缺少 room_id 的行可定位且其余行正常', async ({ page }) => {
  await gotoBatch(page);
  const { room_id: _omitted, ...withoutId } = okRoom('SHOULD-NOT-APPEAR');
  await submitBatch(page, { items: [withoutId, okRoom('KEPT')] });

  const rows = page.getByTestId('batch-row');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toHaveClass(/row-invalid/);
  await expect(rows.nth(0).getByTestId('row-room-id')).toContainText('第 1 项缺少 room_id');
  await expect(rows.nth(0).getByTestId('row-errors')).toContainText('room_id');
  await expect(rows.nth(1)).toHaveClass(/row-ok/);
  await expect(rows.nth(1).getByTestId('row-room-id')).toHaveText('KEPT');
});

test('20 个房间的批次顺序稳定', async ({ page }) => {
  const rooms = Array.from({ length: 20 }, (_, i) =>
    okRoom(`ROOM-${String(i + 1).padStart(2, '0')}`, { limit: 5.0 }),
  );
  await gotoBatch(page);
  await submitBatch(page, { items: rooms });

  await expect(page.getByTestId('batch-row')).toHaveCount(20);
  const ids = await page.getByTestId('row-room-id').allInnerTexts();
  expect(ids).toEqual(rooms.map((r) => r.room_id));
});

test('切换入口不残留另一入口的结论', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('payload-input').fill(
    JSON.stringify({
      sample_interval_ms: 1,
      pressure: decayPressure(),
      limit_seconds: 1.0,
    }),
  );
  await page.getByTestId('submit-btn').click();
  await expect(page.getByTestId('result-panel')).toBeVisible();

  await page.getByTestId('mode-batch').click();
  await expect(page.getByTestId('result-panel')).toHaveCount(0);

  await page.getByTestId('mode-single').click();
  await expect(page.getByTestId('batch-result')).toHaveCount(0);
  // 单间组件已被重新挂载，旧结论同样不残留
  await expect(page.getByTestId('result-panel')).toHaveCount(0);
});
