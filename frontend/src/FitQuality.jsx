import { describeFitQuality } from './format.js';

/**
 * 斜率旁的拟合优度证据：四位小数 R² 与「稳定 / 需复查」标签。
 * 标签仅提示是否值得现场复查，不参与、也不改变合格判定；
 * 旧服务在升级期间不返回 r_squared 时显示「暂无拟合质量」，避免崩溃或误判。
 * 单间与批量共用同一展示口径。
 */
export default function FitQuality({
  result,
  testId = 'fit-quality',
  r2TestId = 'fit-r2',
  labelTestId = 'fit-label',
}) {
  const fit = describeFitQuality(result);

  if (!fit.available) {
    return (
      <span
        className="fit-quality fit-quality-unknown"
        data-testid={testId}
        title="当前服务未返回拟合优度，不影响 T20 判定"
      >
        <span data-testid={labelTestId}>{fit.label}</span>
      </span>
    );
  }

  return (
    <span
      className={`fit-quality ${fit.review ? 'fit-review' : 'fit-stable'}`}
      data-testid={testId}
      title={
        fit.review
          ? 'R² 低于 0.9000：衰减点较离散，建议现场复查；不改变合格判定'
          : 'R² 不低于 0.9000：衰减点离散程度可接受；不改变合格判定'
      }
    >
      <span className="fit-r2" data-testid={r2TestId}>
        R²={fit.r2Text}
      </span>
      <span className="fit-label" data-testid={labelTestId}>
        {fit.label}
      </span>
    </span>
  );
}
