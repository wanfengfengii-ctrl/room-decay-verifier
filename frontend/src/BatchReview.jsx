import { useReducer, useRef, useState } from 'react';
import { evaluateBatch } from './api.js';
import { COMMON_LIMIT_MAX, COMMON_LIMIT_MESSAGE, COMMON_LIMIT_MIN, parseCommonLimitInput, withCommonLimit } from './commonLimit.js';
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
        <td data-testid="row-limit">{formatSeconds(item.limit_seconds)}</td>
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
 * 请求级失败（无法解析 / room_id 重复 / 统一限值非法）整批拒绝并清空旧批次。
 * 启用「统一限值」时，全批按顶层 common_limit_seconds 判定，各项可省略 limit_seconds。
 */
export default function BatchReview() {
  const [text, setText] = useState('');
  const [commonEnabled, setCommonEnabled] = useState(false);
  const [commonLimitText, setCommonLimitText] = useState('');
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

    if (commonEnabled) {
      const parsed = parseCommonLimitInput(commonLimitText);
      if (!parsed.ok) {
        // 输入本身不是数字：本地提示，旧结果已清空、当前输入保留供修正
        dispatch({ type: 'ERROR', error: COMMON_LIMIT_MESSAGE });
        return;
      }
      payload = withCommonLimit(payload, parsed.value);
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
      {/* noValidate：限值越界等校验统一走后端 400 口径，页面展示同一文案 */}
      <form onSubmit={handleSubmit} noValidate>
        <textarea
          data-testid="batch-input"
          rows={14}
          placeholder='{"items": [{"room_id": "A101", "sample_interval_ms": 1, "pressure": [...], "limit_seconds": 1.0}]}'
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="common-limit">
          <label className="common-limit-toggle">
            <input
              type="checkbox"
              data-testid="common-limit-toggle"
              checked={commonEnabled}
              onChange={(e) => setCommonEnabled(e.target.checked)}
            />
            统一限值（同一楼层全批共用，各项可省略 limit_seconds）
          </label>
          {commonEnabled && (
            <label className="common-limit-value">
              限值（秒）
              <input
                type="number"
                data-testid="common-limit-input"
                min={COMMON_LIMIT_MIN}
                max={COMMON_LIMIT_MAX}
                step="0.01"
                placeholder="0.30 至 5.00"
                value={commonLimitText}
                onChange={(e) => setCommonLimitText(e.target.value)}
              />
            </label>
          )}
        </div>
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
        <p className="hint">
          每批 1 至 20 个房间；每项需含 room_id 及 sample_interval_ms / pressure / limit_seconds。
          启用统一限值后各项可省略 limit_seconds，结果表「上限」列逐行显示实际采用的限值。
        </p>
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
