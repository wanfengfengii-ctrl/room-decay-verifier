import { describe, expect, it } from 'vitest';
import { batchReducer, initialBatchState } from '../batchState.js';

const items = [
  { room_id: 'A', status: 'ok', t20_seconds: 0.5, passed: true },
  { room_id: 'B', status: 'rejected', reason: '衰减曲线斜率非负' },
  {
    status: 'invalid',
    index: 2,
    room_id: 'C',
    errors: [{ field: 'pressure', message: '长度不足' }],
  },
];

const summary = { total: 3, ok: 1, passed: 1, failed: 0, rejected: 1, invalid: 1 };

describe('batchReducer', () => {
  it('新提交开始即清空旧批次', () => {
    const dirty = { phase: 'success', items, summary, error: null };
    const next = batchReducer(dirty, { type: 'SUBMIT' });
    expect(next).toEqual({ phase: 'loading', items: null, summary: null, error: null });
  });

  it('部分失败的批次仍保留全部行与汇总', () => {
    const next = batchReducer(
      batchReducer(initialBatchState, { type: 'SUBMIT' }),
      { type: 'SUCCESS', items, summary },
    );
    expect(next.phase).toBe('success');
    expect(next.items).toHaveLength(3);
    expect(next.items.map((i) => i.status)).toEqual(['ok', 'rejected', 'invalid']);
    expect(next.summary).toEqual(summary);
    expect(next.error).toBeNull();
  });

  it('请求级失败后不得残留上一批表格与汇总', () => {
    const succeeded = batchReducer(initialBatchState, { type: 'SUCCESS', items, summary });
    const failed = batchReducer(succeeded, {
      type: 'ERROR',
      error: 'room_id 重复："A"，整批已拒绝',
    });
    expect(failed.phase).toBe('error');
    expect(failed.items).toBeNull();
    expect(failed.summary).toBeNull();
    expect(failed.error).toContain('重复');
  });

  it('初始状态为空', () => {
    expect(initialBatchState).toEqual({
      phase: 'idle',
      items: null,
      summary: null,
      error: null,
    });
  });
});
