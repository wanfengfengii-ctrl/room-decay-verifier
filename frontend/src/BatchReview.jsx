import { useReducer, useRef, useState } from 'react';
import { evaluateBatch } from './api.js';
import { formatBatchRoomId, formatSeconds, formatSlope } from './format.js';
import { BATCH_EXAMPLE_JSON } from './sample.js';
import { batchReducer, initialBatchState } from './batchState.js';

const STATUS_TEXT = {
  ok: '正常',
  rejected: '衰减异常',
  invalid: '字段错误',
};

function RowDetail({ item }) {
  if (item.status === 'ok') {
    return (
      <>
        <td data-testid="row-t20">{formatSeconds(item.t20_seconds)}</td>
        <td data-testid="row-points">{item.points_used}</td>
        <td data-testid="row-slope">{formatSlope(item.slope)}</td>
        <td>{formatSeconds(item.limit_seconds)}</td>
        <td data-testid="row-verdict" className={item.passed ? 'pass' : 'fail'}>
          {item.passed ? '合格' : '不合格'}
        </td>
        <td className="detail">—</td>
      </>
    );
  }
  if (item.status === 'rejected') {
    return (
      <>
        <td>—</td>
        <td>—</td>
        <td>—</td>
        <td>—</td>
        <td data-testid="row-verdict">不计入</td>
        <td className="detail" data-testid="row-reason">
          {item.reason}
        </td>
      </>
    );
  }
  return (
    <>
      <td>—</td>
      <td>—</td>
      <td>—</td>
      <td>—</td>
      <td data-testid="row-verdict">不计入</td>
      <td className="detail" data-testid="row-errors">
        {item.errors.map((e) => `${e.field}：${e.message}`).join('；')}
      </td>
    </>
  );
}

/**
 * 批量复核入口：1 至 20 个房间逐项复核。
 * items 按文件顺序展示；部分失败不遮蔽其余正常结果；
 * 请求级失败（无法解析 / room_id 重复）整批拒绝并清空旧批次。
 */
export default function BatchReview() {
  const [text, setText] = useState('');
  const [state, dispatch] = useReducer(batchReducer, initialBatchState);
  const fileRef = useRef(null);

  async function handleSubmit(event) {
    event.preventDefault();
    dispatch({ type: 'SUBMIT' }); // 新提交开始即清空旧批次

    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      dispatch({ type: 'ERROR', error: '输入不是合法的 JSON，请检查后重试' });
      return;
    }

    try {
      const body = await evaluateBatch(payload);
      dispatch({ type: 'SUCCESS', items: body.items, summary: body.summary });
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
          data-testid="batch-input"
          rows={14}
          placeholder='{"items": [{"room_id": "A101", "sample_interval_ms": 1, "pressure": [...], "limit_seconds": 1.0}]}'
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="actions">
          <button
            type="submit"
            data-testid="batch-submit-btn"
            disabled={state.phase === 'loading'}
          >
            {state.phase === 'loading' ? '批量复核中…' : '开始批量复核'}
          </button>
          <button type="button" data-testid="batch-example-btn" onClick={() => setText(BATCH_EXAMPLE_JSON)}>
            载入混合示例
          </button>
          <button type="button" onClick={() => fileRef.current?.click()}>
            上传 JSON 文件
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            hidden
            data-testid="batch-file-input"
            onChange={handleFile}
          />
        </div>
        <p className="hint">每批 1 至 20 个房间；每项需含 room_id 及 sample_interval_ms / pressure / limit_seconds。</p>
      </form>

      {state.phase === 'success' && (
        <section data-testid="batch-result">
          <div className="panel summary" data-testid="batch-summary">
            <h2>合格汇总（仅正常项）</h2>
            <p>
              共 {state.summary.total} 间；正常 {state.summary.ok} 间，其中合格{' '}
              <strong className="pass">{state.summary.passed}</strong> 间、不合格{' '}
              <strong className="fail">{state.summary.failed}</strong> 间；
              衰减异常 {state.summary.rejected} 间、字段错误 {state.summary.invalid} 间（均不计入）。
            </p>
          </div>

          <table className="batch-table" data-testid="batch-table">
            <thead>
              <tr>
                <th>#</th>
                <th>房间</th>
                <th>状态</th>
                <th>T20</th>
                <th>取点数</th>
                <th>斜率 (dB/s)</th>
                <th>上限</th>
                <th>判定</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {state.items.map((item, i) => (
                <tr
                  key={`${item.status}-${i}`}
                  className={`row-${item.status}`}
                  data-testid="batch-row"
                  data-index={item.status === 'invalid' ? item.index : i}
                  data-status={item.status}
                >
                  <td>{i + 1}</td>
                  <td data-testid="row-room-id">{formatBatchRoomId(item)}</td>
                  <td data-testid="row-status">
                    <span className={`tag tag-${item.status}`}>{STATUS_TEXT[item.status]}</span>
                  </td>
                  <RowDetail item={item} />
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {state.phase === 'error' && (
        <section className="panel error" data-testid="batch-error-panel">
          <h2>整批未被受理</h2>
          <p data-testid="batch-error-message">{state.error}</p>
        </section>
      )}
    </>
  );
}
