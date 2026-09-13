import { describe, expect, it } from 'vitest';
import {
  FIT_QUALITY_THRESHOLD,
  describeFitQuality,
  formatBatchRoomId,
  formatRSquared,
  formatSeconds,
  formatSlope,
  formatT20,
} from '../format.js';

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

describe('formatRSquared', () => {
  it('固定四位小数', () => {
    expect(formatRSquared(1)).toBe('1.0000');
    expect(formatRSquared(0.90004)).toBe('0.9000');
    expect(formatRSquared(0.869709)).toBe('0.8697');
  });

  it('第 5 位四舍五入可能越过展示阈值，分类仍以未舍入值为准', () => {
    // 0.89996 未舍入低于 0.9（需复查），但四位小数显示 0.9000
    expect(formatRSquared(0.89996)).toBe('0.9000');
  });

  it('阈值恰好 0.9000 展示为 0.9000', () => {
    expect(formatRSquared(FIT_QUALITY_THRESHOLD)).toBe('0.9000');
  });

  it('缺字段或非有限数返回 null（交给调用方显示占位文案）', () => {
    expect(formatRSquared(undefined)).toBeNull();
    expect(formatRSquared(null)).toBeNull();
    expect(formatRSquared(NaN)).toBeNull();
    expect(formatRSquared(Infinity)).toBeNull();
    expect(formatRSquared('0.9')).toBeNull();
  });
});

describe('describeFitQuality', () => {
  it('R² 高于等于阈值标记稳定，四位小数与标签同时给出', () => {
    expect(describeFitQuality({ r_squared: 0.936359, fit_quality: 'stable' })).toEqual({
      available: true,
      r2Text: '0.9364',
      review: false,
      label: '稳定',
    });
    const boundary = describeFitQuality({ r_squared: 0.9, fit_quality: 'stable' });
    expect(boundary.label).toBe('稳定');
    expect(boundary.r2Text).toBe('0.9000');
  });

  it('R² 低于阈值标记需复查', () => {
    expect(describeFitQuality({ r_squared: 0.899731, fit_quality: 'needs_review' })).toEqual({
      available: true,
      r2Text: '0.8997',
      review: true,
      label: '需复查',
    });
  });

  it('分类以未舍入 R² 为准：显示 0.9000 但原值低于阈值仍提示复查', () => {
    const fit = describeFitQuality({ r_squared: 0.89996, fit_quality: 'needs_review' });
    expect(fit.r2Text).toBe('0.9000');
    expect(fit.review).toBe(true);
    expect(fit.label).toBe('需复查');
  });

  it('标签以服务端 fit_quality 为准，缺半截字段时退回同一阈值口径', () => {
    expect(describeFitQuality({ r_squared: 0.95 }).review).toBe(false);
    expect(describeFitQuality({ r_squared: 0.8 }).review).toBe(true);
    // 服务端标签与数值冲突时不自作主张：以服务端 fit_quality 为准
    expect(describeFitQuality({ r_squared: 0.95, fit_quality: 'needs_review' }).review).toBe(true);
  });

  it('旧服务不返回新字段时显示暂无拟合质量，且不误判为需复查', () => {
    expect(describeFitQuality({})).toEqual({
      available: false,
      r2Text: null,
      review: null,
      label: '暂无拟合质量',
    });
    expect(describeFitQuality(undefined).label).toBe('暂无拟合质量');
    expect(describeFitQuality(null).available).toBe(false);
    expect(describeFitQuality({ fit_quality: 'stable' }).available).toBe(false);
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
