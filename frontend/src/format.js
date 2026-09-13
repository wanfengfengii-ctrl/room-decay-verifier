/** 展示格式化辅助：T20 三位小数，斜率六位小数。 */

/** 拟合优度阈值，与后端 RSQUARED_STABLE_THRESHOLD 同一口径：R² >= 0.9000 稳定。 */
export const FIT_QUALITY_THRESHOLD = 0.9;

export function formatT20(value) {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toFixed(3);
}

export function formatSlope(value) {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toFixed(6);
}

/** R² 固定四位小数；非有限数（含旧服务缺字段）返回 null，由调用方走占位文案。 */
export function formatRSquared(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return value.toFixed(4);
}

/**
 * 拟合质量展示模型：
 * - 新服务正常项：返回 { available: true, r2Text, review, label }，
 *   标签优先采用服务端 fit_quality（stable/needs_review），缺半截时退回同一阈值口径；
 * - 旧服务（升级期间）不返回 r_squared：available=false，页面显示「暂无拟合质量」，
 *   不崩溃、也不擅自把旧结论标成需复查。
 * 标签只提示是否值得复查，合格 / 不合格仍以后端 passed 为准。
 */
export function describeFitQuality(result) {
  const r2Text = formatRSquared(result?.r_squared);
  if (r2Text === null) {
    return { available: false, r2Text: null, review: null, label: '暂无拟合质量' };
  }
  let review;
  if (result.fit_quality === 'stable' || result.fit_quality === 'needs_review') {
    review = result.fit_quality === 'needs_review';
  } else {
    review = result.r_squared < FIT_QUALITY_THRESHOLD;
  }
  return { available: true, r2Text, review, label: review ? '需复查' : '稳定' };
}

export function formatSeconds(value) {
  if (value == null || Number.isNaN(value)) return '—';
  return `${value.toFixed(3)} s`;
}

/**
 * 批量行「房间」列文案：任何情况下都要让工程师定位到项目位置。
 * - 合法标识原样展示；
 * - 标识仅含空白时不能渲染成空白单元格，显示项目位置并说明空白；
 * - 标识缺失与非字符串（如数字）响应里 room_id 都是 null，
 *   凭 room_id 字段错误区分：类型不符要明确指出，而非笼统的“缺少”。
 */
export function formatBatchRoomId(item) {
  const roomId = item?.room_id;
  if (typeof roomId === 'string' && roomId.trim()) return roomId;
  const position = `第 ${(item?.index ?? 0) + 1} 项`;
  if (typeof roomId === 'string') return `（${position} room_id 为空白）`;
  const idError = item?.errors?.find((e) => e.field === 'room_id');
  if (idError && !idError.message.includes('缺失')) {
    return `（${position} room_id 类型错误）`;
  }
  return `（${position}缺少 room_id）`;
}
