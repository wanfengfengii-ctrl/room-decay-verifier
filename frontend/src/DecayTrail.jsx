import { useMemo, useState } from 'react';
import { buildTrailChart, readDecayTrail } from './trail.js';

/**
 * 单间成功结论下可展开的衰减轨迹。
 *
 * - 数据完全来自本次成功响应里的 decay_trail：展开 / 收起只切换本地
 *   useState，不发起任何请求；提交状态机在新提交或失败时清空 result，
 *   本组件随成功面板一起卸载，上一张图自然清除；
 * - 旧服务（升级期间）成功响应缺少 decay_trail 或字段损坏时，
 *   readDecayTrail 返回 null，入口只显示「暂无衰减轨迹」，不凭半截字段画图；
 * - 拒绝响应与字段错误不渲染成功面板，因此不会生成任何图形数据。
 */
export default function DecayTrail({ result }) {
  const [expanded, setExpanded] = useState(false);
  const trail = useMemo(() => readDecayTrail(result), [result]);

  if (!trail) {
    return (
      <div className="decay-trail trail-unavailable" data-testid="decay-trail-empty">
        <span className="trail-placeholder" data-testid="trail-placeholder">
          暂无衰减轨迹
        </span>
      </div>
    );
  }

  return (
    <div className="decay-trail" data-testid="decay-trail">
      <button
        type="button"
        className="trail-toggle"
        aria-expanded={expanded}
        aria-controls="decay-trail-panel"
        data-testid="trail-toggle"
        onClick={() => setExpanded((open) => !open)}
      >
        {expanded ? '收起衰减轨迹' : '展开衰减轨迹'}
      </button>
      {expanded && <DecayTrailChart trail={trail} />}
    </div>
  );
}

function DecayTrailChart({ trail }) {
  const chart = useMemo(() => buildTrailChart(trail), [trail]);
  const { plot, dots, fitLine, yTicks, xTicks, width, height } = chart;

  return (
    <div id="decay-trail-panel" data-testid="trail-panel">
      <p className="trail-count" data-testid="trail-count">
        完整取点数 <strong data-testid="trail-total">{trail.totalPoints}</strong>
        ，已展示 <strong data-testid="trail-shown">{trail.points.length}</strong> 个取样点
        （超过 200 个时首尾必留、等距取样；斜率 / R² / T20 仍按完整窗口计算）
      </p>
      <svg
        className="trail-chart"
        data-testid="trail-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="[-25, -5] dB 窗口取样点与完整回归线"
      >
        {/* 绘图区边框 */}
        <rect
          x={plot.x}
          y={plot.y}
          width={plot.width}
          height={plot.height}
          className="trail-plot-bg"
        />

        {/* y 轴刻度（dB） */}
        {yTicks.map((tick) => (
          <g key={`y-${tick.label}`}>
            <line
              x1={plot.x}
              x2={plot.x + plot.width}
              y1={tick.y}
              y2={tick.y}
              className="trail-grid"
            />
            <text x={plot.x - 6} y={tick.y + 3} textAnchor="end" className="trail-axis-text">
              {tick.label}
            </text>
          </g>
        ))}

        {/* x 轴刻度（峰值后秒数） */}
        {xTicks.map((tick, i) => (
          <g key={`x-${i}`}>
            <line
              x1={tick.x}
              x2={tick.x}
              y1={plot.y + plot.height}
              y2={plot.y + plot.height + 4}
              className="trail-axis"
            />
            <text
              x={tick.x}
              y={plot.y + plot.height + 18}
              textAnchor="middle"
              className="trail-axis-text"
            >
              {tick.label}
            </text>
          </g>
        ))}

        {/* 轴标题 */}
        <text
          x={plot.x + plot.width / 2}
          y={height - 4}
          textAnchor="middle"
          className="trail-axis-title"
        >
          峰值后时间 (s)
        </text>
        <text
          x={12}
          y={plot.y + plot.height / 2}
          textAnchor="middle"
          className="trail-axis-title"
          transform={`rotate(-90 12 ${plot.y + plot.height / 2})`}
        >
          dB
        </text>

        {/* 完整窗口回归线：两端点来自服务端同一条最小二乘直线 */}
        <line
          x1={fitLine.x1}
          y1={fitLine.y1}
          x2={fitLine.x2}
          y2={fitLine.y2}
          className="trail-fitline"
          data-testid="trail-fitline"
        />

        {/* 等距取样的窗口点 */}
        <g data-testid="trail-dots">
          {dots.map((dot, i) => (
            <circle key={i} cx={dot.x} cy={dot.y} r={2.2} className="trail-dot">
              <title>{`t=${dot.t.toFixed(4)}s，${dot.db.toFixed(3)} dB`}</title>
            </circle>
          ))}
        </g>
      </svg>
      <p className="hint trail-legend">
        <span className="legend-dot" /> 取样点　<span className="legend-line" /> 完整回归线（端点取自未取样的 [-25,-5] dB 全窗口）
      </p>
    </div>
  );
}
