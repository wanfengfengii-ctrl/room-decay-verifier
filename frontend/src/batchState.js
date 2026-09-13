/**
 * 批量复核状态机。
 * 关键约束：
 * - 任何一次新提交开始即清空上一批结果（部分失败的批次里，无效行随 200 响应返回，
 *   其余房间的结论照常展示，不算“提交失败”）；
 * - 只有请求级失败（无法解析 / room_id 重复 / 服务不可达）才进入 error，
 *   进入 error 时不得残留上一批的表格与汇总。
 */

export const initialBatchState = {
  phase: 'idle', // idle | loading | success | error
  items: null,
  summary: null,
  error: null,
};

export function batchReducer(state, action) {
  switch (action.type) {
    case 'SUBMIT':
      return { phase: 'loading', items: null, summary: null, error: null };
    case 'SUCCESS':
      return {
        phase: 'success',
        items: action.items,
        summary: action.summary,
        error: null,
      };
    case 'ERROR':
      return { phase: 'error', items: null, summary: null, error: action.error };
    default:
      return state;
  }
}
