import { describe, expect, it } from 'vitest';
import { formatSeconds, formatSlope, formatT20 } from '../format.js';

describe('formatT20', () => {
  it('保留三位小数', () => {
    expect(formatT20(1.23456)).toBe('1.235');
    expect(formatT20(0.5)).toBe('0.500');
    expect(formatT20(2)).toBe('2.000');
  });

  it('空值显示占位符', () => {
    expect(formatT20(null)).toBe('—');
    expect(formatT20(undefined)).toBe('—');
    expect(formatT20(NaN)).toBe('—');
  });
});

describe('formatSeconds', () => {
  it('三位小数并带单位', () => {
    expect(formatSeconds(0.5)).toBe('0.500 s');
  });
});

describe('formatSlope', () => {
  it('保留六位小数', () => {
    expect(formatSlope(-33.3333333)).toBe('-33.333333');
    expect(formatSlope(-40)).toBe('-40.000000');
  });
});
