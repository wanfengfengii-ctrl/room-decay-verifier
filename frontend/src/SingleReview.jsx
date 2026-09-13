import { useReducer, useRef, useState } from 'react';
import { evaluateSample } from './api.js';
import DecayTrail from './DecayTrail.jsx';
import FitQuality from './FitQuality.jsx';
import { formatSeconds, formatSlope } from './format.js';
import { EXAMPLE_JSON } from './sample.js';
import { initialState, submissionReducer } from './state.js';

/**
 * 单次复核入口：请求与响应契约保持不变。
 * 作为独立组件挂载，切走再切回即为全新状态，不残留任何旧结论。
 */
export default function SingleReview() {
  const [text, setText] = useState('');
  const [state, dispatch] = useReducer(submissionReducer, initialState);
  const fileRef = useRef(null);

  async function handleSubmit(event) {
    event.preventDefault();
    dispatch({ type: 'SUBMIT' });

    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      dispatch({ type: 'ERROR', error: '输入不是合法的 JSON，请检查后重试' });
      return;
    }

    try {
      const body = await evaluateSample(payload);
      if (body.status === 'rejected') {
        dispatch({ type: 'REJECTED', reason: body.reason });
      } else {
        dispatch({ type: 'SUCCESS', result: body });
      }
    } catch (err) {
      dispatch({ type: 'ERROR', error: err.message });
    }
  }

  function handleFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    file.text().then((content) => setText(content));
  }

  return (
    <>
      <form onSubmit={handleSubmit}>
        <textarea
          data-testid="payload-input"
          rows={12}
          placeholder='{"sample_interval_ms": 1, "pressure": [...], "limit_seconds": 1.0}'
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="actions">
          <button type="submit" data-testid="submit-btn" disabled={state.phase === 'loading'}>
            {state.phase === 'loading' ? '复核中…' : '开始复核'}
          </button>
          <button type="button" onClick={() => setText(EXAMPLE_JSON)}>
            载入示例
          </button>
          <button type="button" onClick={() => fileRef.current?.click()}>
            上传 JSON 文件
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            hidden
            data-testid="file-input"
            onChange={handleFile}
          />
        </div>
      </form>

      {state.phase === 'success' && (
        <section className="panel success" data-testid="result-panel">
          <h2>复核结论</h2>
          <dl>
            <div>
              <dt>T20（三位小数）</dt>
              <dd data-testid="t20-value">{formatSeconds(state.result.t20_seconds)}</dd>
            </div>
            <div>
              <dt>取点数</dt>
              <dd data-testid="points-value">{state.result.points_used}</dd>
            </div>
            <div>
              <dt>斜率 (dB/s)</dt>
              <dd className="slope-cell">
                <span data-testid="slope-value">{formatSlope(state.result.slope)}</span>
                <FitQuality result={state.result} />
              </dd>
            </div>
            <div>
              <dt>上限</dt>
              <dd>{formatSeconds(state.result.limit_seconds)}</dd>
            </div>
            <div>
              <dt>判定</dt>
              <dd data-testid="verdict" className={state.result.passed ? 'pass' : 'fail'}>
                {state.result.passed ? '合格' : '不合格'}
              </dd>
            </div>
          </dl>
          <p className="hint fit-note">
            拟合质量按回归 R² 提示是否值得现场复查（R² ≥ 0.9000 为稳定），不参与、也不改变上面的合格判定。
          </p>
          <DecayTrail result={state.result} />
        </section>
      )}

      {state.phase === 'rejected' && (
        <section className="panel rejected" data-testid="rejection-panel">
          <h2>复核被拒绝</h2>
          <p data-testid="rejection-reason">{state.reason}</p>
        </section>
      )}

      {state.phase === 'error' && (
        <section className="panel error" data-testid="error-panel">
          <h2>提交失败</h2>
          <p data-testid="error-message">{state.error}</p>
        </section>
      )}
    </>
  );
}
