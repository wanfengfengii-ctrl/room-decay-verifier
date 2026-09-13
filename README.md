# T20 混响复核台

会议室竣工复核用的全栈小台：接收测量软件导出的线性声压采样 JSON，按统一口径自动选取衰减段并计算 T20，避免手工挑段导致的结论漂移。

## 计算口径

1. 背景值 = 末尾 `ceil(N × 10%)` 个样本的算术平均；
2. 从全序列**首个最大值**开始，逐项减去背景值，仅保留正值；
3. 以最大校正量为基准，计算 `20 × log10(校正量 / 基准量)` dB；
4. 选取落在闭区间 **[-25, -5] dB** 的点，以峰值后的秒数为横轴做普通最小二乘；
5. 有效点**不足 30 个**或**斜率不为负**即拒绝（异常衰减只能看到拒绝原因）；
6. `T20 = -20 / 斜率`，用**未舍入值**与上限比较，**相等算合格**；界面显示三位小数、取点数与斜率。

### 输入 JSON

| 字段 | 约束 |
| --- | --- |
| `sample_interval_ms` | 1 至 100 的整数 |
| `pressure` | 200 至 20000 个大于零的数 |
| `limit_seconds` | 0.30 至 5.00 |

## Docker Compose 运行（推荐）

```bash
docker compose up --build
```

- Web：http://localhost:8080 （可用 `WEB_PORT` 覆盖宿主端口）
- API：http://localhost:8000 （可用 `API_PORT` 覆盖宿主端口）

```bash
WEB_PORT=9000 API_PORT=9001 docker compose up --build
```

### 一次性验收服务 `verify`

`verify` 服务随 `docker compose up` 自动运行一次后退出，也可单独执行：

```bash
docker compose up --build verify   # 或 docker compose run --rm verify
echo $?                            # 0 = 验收通过
```

它对真实服务做端到端断言：独立 oracle 复算 T20/斜率/取点数、相等算合格、同一采样两次响应逐字节一致、异常衰减只暴露拒绝原因、非法输入 422、Web 反代链路一致。

## 本地开发

```bash
# 后端
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd backend && ../.venv/bin/python -m pytest        # pytest：公式与 API 契约

# 前端
cd frontend && npm install
npm run test                                       # Vitest：格式化与提交状态机
npx playwright install chromium
npm run test:e2e                                   # Playwright：真实前后端联调
```

## 目录结构

```
backend/    FastAPI 应用与 pytest（app/t20.py 为计算核心）
frontend/   React + Vite，Vitest 单测与 Playwright 联调
verify/     一次性验收服务（独立 oracle，纯标准库）
docker-compose.yml
```
