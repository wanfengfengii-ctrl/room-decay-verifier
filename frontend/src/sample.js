/** 生成一份理想指数衰减示例采样，便于手工体验（计算仍在后端完成）。 */

export function generateExamplePayload() {
  const sampleIntervalMs = 1;
  const n = 5000;
  const t60 = 1.5;
  const pressure = Array.from({ length: n }, (_, i) => {
    const t = (i * sampleIntervalMs) / 1000;
    return 1000 * 10 ** ((-3 * t) / t60) + 0.25;
  });
  return {
    sample_interval_ms: sampleIntervalMs,
    pressure,
    limit_seconds: 1.0,
  };
}

export const EXAMPLE_JSON = JSON.stringify(generateExamplePayload());
