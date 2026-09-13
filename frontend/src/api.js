/** 真实调用 FastAPI 后端的客户端。 */

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api';

function describeHttpError(status, body) {
  if (body && Array.isArray(body.detail)) {
    return body.detail
      .map((d) => d.msg || JSON.stringify(d))
      .join('；');
  }
  if (body && typeof body.detail === 'string') return body.detail;
  return `服务返回 HTTP ${status}`;
}

export async function evaluateSample(payload) {
  let resp;
  try {
    resp = await fetch(`${API_BASE}/evaluate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error('无法连接复核服务，请确认后端已启动');
  }
  if (!resp.ok) {
    let body = null;
    try {
      body = await resp.json();
    } catch {
      /* 保留默认错误信息 */
    }
    throw new Error(describeHttpError(resp.status, body));
  }
  return resp.json();
}
