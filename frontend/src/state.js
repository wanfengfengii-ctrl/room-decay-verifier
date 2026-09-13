/**
 * 提交状态机。
 * 关键约束：任何一次提交（无论成功、被拒绝还是出错）都会先清空旧结果，
 * 出错/被拒绝后界面上不得残留上一次的成功证据。
 */

export const initialState = {
  phase: 'idle', // idle | loading | success | rejected | error
  result: null,
  reason: null,
  error: null,
};

export function submissionReducer(state, action) {
  switch (action.type) {
    case 'SUBMIT':
      return { phase: 'loading', result: null, reason: null, error: null };
    case 'SUCCESS':
      return { phase: 'success', result: action.result, reason: null, error: null };
    case 'REJECTED':
      return { phase: 'rejected', result: null, reason: action.reason, error: null };
    case 'ERROR':
      return { phase: 'error', result: null, reason: null, error: action.error };
    default:
      return state;
  }
}
