import { describe, expect, it } from 'vitest';
import { initialState, submissionReducer } from '../state.js';

const successResult = {
  status: 'ok',
  t20_seconds: 0.5,
  points_used: 500,
  slope: -40,
  limit_seconds: 1.0,
  passed: true,
};

describe('submissionReducer', () => {
  it('提交时清空旧结果与旧错误', () => {
    const dirty = {
      phase: 'success',
      result: successResult,
      reason: null,
      error: null,
    };
    const next = submissionReducer(dirty, { type: 'SUBMIT' });
    expect(next).toEqual({ phase: 'loading', result: null, reason: null, error: null });
  });

  it('成功后展示新结果', () => {
    const next = submissionReducer(
      submissionReducer(initialState, { type: 'SUBMIT' }),
      { type: 'SUCCESS', result: successResult },
    );
    expect(next.phase).toBe('success');
    expect(next.result).toEqual(successResult);
  });

  it('出错后不得保留旧结果', () => {
    const succeeded = submissionReducer(initialState, {
      type: 'SUCCESS',
      result: successResult,
    });
    const failed = submissionReducer(succeeded, {
      type: 'ERROR',
      error: '无法连接复核服务',
    });
    expect(failed.phase).toBe('error');
    expect(failed.result).toBeNull();
    expect(failed.reason).toBeNull();
    expect(failed.error).toBe('无法连接复核服务');
  });

  it('被拒绝后只保留拒绝原因', () => {
    const succeeded = submissionReducer(initialState, {
      type: 'SUCCESS',
      result: successResult,
    });
    const rejected = submissionReducer(succeeded, {
      type: 'REJECTED',
      reason: '衰减曲线斜率非负，不符合混响衰减特征',
    });
    expect(rejected.phase).toBe('rejected');
    expect(rejected.result).toBeNull();
    expect(rejected.error).toBeNull();
    expect(rejected.reason).toContain('斜率非负');
  });
});
