/**
 * 批量复核「统一限值」：同一楼层采用统一验收标准时，全批共用一个限值，
 * 工程师不必为每个房间重复填写。口径与后端一致：0.30 至 5.00 的 JSON 数字。
 */

export const COMMON_LIMIT_MIN = 0.3;
export const COMMON_LIMIT_MAX = 5.0;
export const COMMON_LIMIT_MESSAGE = '统一限值必须是0.30至5.00的JSON数字';

/**
 * 解析页面输入的统一限值。
 * 返回 { ok: true, value } 或 { ok: false }：空串、非数字、非有限数不合法，
 * 页面直接提示且不发起请求；越界数字在此放行，由后端按同一口径整批拒绝，
 * 页面展示后端返回的 400 信息（与本地提示同一条文案）。
 */
export function parseCommonLimitInput(raw) {
  const text = String(raw ?? '').trim();
  if (!text) return { ok: false };
  const value = Number(text);
  if (!Number.isFinite(value)) return { ok: false };
  return { ok: true, value };
}

/**
 * 把统一限值并入批量请求体（顶层 common_limit_seconds）。
 * 请求体不是纯 JSON 对象时原样返回，结构错误仍由后端整批拒绝。
 */
export function withCommonLimit(payload, value) {
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
    return payload;
  }
  return { ...payload, common_limit_seconds: value };
}
