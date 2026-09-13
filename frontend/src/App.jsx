import { useState } from 'react';
import BatchReview from './BatchReview.jsx';
import SingleReview from './SingleReview.jsx';

export default function App() {
  // 两个入口各自挂载独立组件，切换即卸载，状态互不残留。
  const [mode, setMode] = useState('single'); // single | batch

  return (
    <main className="page">
      <h1>T20 混响复核台</h1>
      <div className="mode-switch" role="tablist" aria-label="复核模式">
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'single'}
          className={mode === 'single' ? 'mode-btn active' : 'mode-btn'}
          data-testid="mode-single"
          onClick={() => setMode('single')}
        >
          单间复核
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'batch'}
          className={mode === 'batch' ? 'mode-btn active' : 'mode-btn'}
          data-testid="mode-batch"
          onClick={() => setMode('batch')}
        >
          批量复核
        </button>
      </div>

      {mode === 'single' ? (
        <>
          <p className="hint">
            粘贴或上传测量软件导出的 JSON（sample_interval_ms / pressure / limit_seconds），
            后台将按统一口径自动选取衰减段并计算 T20。
          </p>
          <SingleReview />
        </>
      ) : (
        <>
          <p className="hint">
            工程师一次验收多间会议室时使用：上传包含 1 至 20 个房间的 JSON，
            每项以 <code>room_id</code> 标识并复用现有采样间隔、压力序列和上限字段，
            提交后按文件顺序逐间查看结论与合格汇总；同一楼层统一验收标准时，
            可勾选「统一限值」让全批共用一个上限。
          </p>
          <BatchReview />
        </>
      )}
    </main>
  );
}
