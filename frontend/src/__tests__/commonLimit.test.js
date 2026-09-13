import { describe, expect, it } from 'vitest';
import {
  COMMON_LIMIT_MESSAGE,
  parseCommonLimitInput,
  withCommonLimit,
} from '../commonLimit.js';

describe('parseCommonLimitInput', () => {
  it('接受 0.30 至 5.00 的数字文本', () => {
    expect(parseCommonLimitInput('0.30')).toEqual({ ok: true, value: 0.3 });
    expect(parseCommonLimitInput('1')).toEqual({ ok: true, value: 1 });
    expect(parseCommonLimitInput(' 2.5 ')).toEqual({ ok: true, value: 2.5 });
    expect(parseCommonLimitInput('5.00')).toEqual({ ok: true, value: 5 });
  });

  it('拒绝空输入与非数字文本', () => {
    for (const raw of ['', '   ', 'abc', '1.0s', null, undefined]) {
      expect(parseCommonLimitInput(raw).ok).toBe(false);
    }
  });

  it('拒绝非有限数', () => {
    for (const raw of ['Infinity', '-Infinity', 'NaN']) {
      expect(parseCommonLimitInput(raw).ok).toBe(false);
    }
  });

  it('越界数字在此放行，由后端按同一口径整批拒绝', () => {
    // 页面展示后端 400 的同一条文案，保证“整批拒绝”路径可被真实走到
    expect(parseCommonLimitInput('6.0')).toEqual({ ok: true, value: 6 });
    expect(parseCommonLimitInput('0.29')).toEqual({ ok: true, value: 0.29 });
    expect(COMMON_LIMIT_MESSAGE).toBe('统一限值必须是0.30至5.00的JSON数字');
  });
});

describe('withCommonLimit', () => {
  it('在请求对象顶层附加 common_limit_seconds，且不改动原对象', () => {
    const payload = { items: [{ room_id: 'A101' }] };
    const next = withCommonLimit(payload, 1.0);
    expect(next).toEqual({ items: [{ room_id: 'A101' }], common_limit_seconds: 1.0 });
    expect(payload).not.toHaveProperty('common_limit_seconds');
  });

  it('非纯对象请求体原样返回，结构错误仍由后端整批拒绝', () => {
    expect(withCommonLimit([1, 2], 1.0)).toEqual([1, 2]);
    expect(withCommonLimit(null, 1.0)).toBeNull();
    expect(withCommonLimit('oops', 1.0)).toBe('oops');
    expect(withCommonLimit(42, 1.0)).toBe(42);
  });
});
