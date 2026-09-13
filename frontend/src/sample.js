/** 生成示例采样，便于手工体验（计算仍在后端完成）。 */

function decayPressure(t60, { sampleIntervalMs = 1, n = 5000, amplitude = 1000, offset = 0.25 } = {}) {
  return Array.from({ length: n }, (_, i) => {
    const t = (i * sampleIntervalMs) / 1000;
    return amplitude * 10 ** ((-3 * t) / t60) + offset;
  });
}

export function generateExamplePayload() {
  return {
    sample_interval_ms: 1,
    pressure: decayPressure(1.5),
    limit_seconds: 1.0,
  };
}

export const EXAMPLE_JSON = JSON.stringify(generateExamplePayload());

/**
 * 混合批次示例：合格、不合格、衰减异常各一间，
 * 外加一间字段缺失的房间，便于一次看全三种行状态。
 */
export function generateBatchExample() {
  return {
    items: [
      {
        room_id: 'ROOM-A（合格）',
        sample_interval_ms: 1,
        pressure: decayPressure(1.5),
        limit_seconds: 1.0,
      },
      {
        room_id: 'ROOM-B（超上限）',
        sample_interval_ms: 1,
        pressure: decayPressure(1.5),
        limit_seconds: 0.3,
      },
      {
        room_id: 'ROOM-C（衰减异常）',
        sample_interval_ms: 1,
        pressure: Array.from({ length: 1000 }, () => 5),
        limit_seconds: 1.0,
      },
      {
        room_id: 'ROOM-D（字段错误）',
        sample_interval_ms: 500,
        pressure: decayPressure(1.8, { sampleIntervalMs: 2 }),
        limit_seconds: 1.0,
      },
    ],
  };
}

export const BATCH_EXAMPLE_JSON = JSON.stringify(generateBatchExample(), null, 2);
