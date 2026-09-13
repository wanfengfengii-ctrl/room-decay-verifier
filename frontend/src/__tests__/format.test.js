import { describe, expect, it } from 'vitest';
import { formatBatchRoomId, formatSeconds, formatSlope, formatT20 } from '../format.js';

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

describe('formatBatchRoomId', () => {
  it('合法标识原样展示', () => {
    expect(formatBatchRoomId({ room_id: 'A101', status: 'ok' })).toBe('A101');
    expect(formatBatchRoomId({ room_id: 'R'.repeat(150), status: 'ok' })).toBe('R'.repeat(150));
  });

  it('标识缺失时显示项目位置', () => {
    const item = {
      status: 'invalid',
      index: 0,
      room_id: null,
      errors: [{ field: 'room_id', message: '字段缺失' }],
    };
    expect(formatBatchRoomId(item)).toBe('（第 1 项缺少 room_id）');
  });

  it('数字标识显示类型错误与项目位置，而非缺少标识', () => {
    const item = {
      status: 'invalid',
      index: 1,
      room_id: null,
      errors: [{ field: 'room_id', message: 'room_id 必须是字符串' }],
    };
    expect(formatBatchRoomId(item)).toBe('（第 2 项 room_id 类型错误）');
  });

  it('空白标识显示项目位置，不渲染空白单元格', () => {
    const item = {
      status: 'invalid',
      index: 2,
      room_id: '   ',
      errors: [{ field: 'room_id', message: 'room_id 不能为空白字符串' }],
    };
    expect(formatBatchRoomId(item)).toBe('（第 3 项 room_id 为空白）');
  });

  it('非对象元素没有 room_id 字段错误时按缺少处理', () => {
    const item = {
      status: 'invalid',
      index: 3,
      room_id: null,
      errors: [{ field: '(item)', message: '该数组元素必须是包含房间字段的 JSON 对象' }],
    };
    expect(formatBatchRoomId(item)).toBe('（第 4 项缺少 room_id）');
  });
});
