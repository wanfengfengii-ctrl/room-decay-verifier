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
