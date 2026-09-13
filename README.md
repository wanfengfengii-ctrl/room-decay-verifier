# T20 混响复核台

会议室竣工复核用的全栈小台：接收测量软件导出的线性声压采样 JSON，按统一口径自动选取衰减段并计算 T20，避免手工挑段导致的结论漂移。

## 计算口径

1. 背景值 = 末尾 `ceil(N × 10%)` 个样本的算术平均；
2. 从全序列**首个最大值**开始，逐项减去背景值，仅保留正值；
3. 以最大校正量为基准，计算 `20 × log10(校正量 / 基准量)` dB；
4. 选取落在闭区间 **[-25, -5] dB** 的点，以峰值后的秒数为横轴做普通最小二乘；
5. 有效点**不足 30 个**或**斜率不为负**即拒绝（异常衰减只能看到拒绝原因）；
6. `T20 = -20 / 斜率`，用**未舍入值**与上限比较，**相等算合格**；界面显示三位小数、取点数与斜率；
7. 用同一段 **[-25, -5] dB 取点**和已求得的回归线计算决定系数 **R²**（`1 - SSE/SST`），
   浮点误差使比值略越过 0 或 1 时先**钳制到闭区间 [0, 1]** 再分类。

### 拟合优度（R²）——只提示复查，不改变判定

现场复核遇到「同样合格、但衰减点离散程度差异很大」的采样时，成功响应在既有最小二乘证据之外
额外返回两个字段：

| 字段 | 含义 |
| --- | --- |
| `r_squared` | 钳制到 [0, 1] 的决定系数（float） |
| `fit_quality` | `"stable"`：R² **≥ 0.9000**；`"needs_review"`：R² 低于 0.9000 |

- 单间与批量的**正常项字段完全一致**（批量仅多 `room_id`）；
  衰减拒绝（`rejected`）与字段错误（`invalid`）响应**不携带**这两个证据字段；
- 页面在**斜率旁**显示四位小数的 R² 与「**稳定 / 需复查**」徽标（批量为「拟合质量」列）；
- 需复查**只是复查提示**：`passed` / 合格汇总 / 房间顺序一律不变
  （离散但 T20 在上限内仍判合格；理想衰减但上限过低仍判不合格）；
- 分类以**未舍入** R² 与 0.9000 比较，相等归稳定侧；页面四位小数仅用于展示；
- 升级期间若旧服务未返回新字段，页面显示「**暂无拟合质量**」，不崩溃、也不误判。

### 衰减轨迹（`decay_trail`）——只可视化判定依据，不改变计算口径

现场工程师确认 T20 结论时，常要核对系统实际选中了哪段衰减采样：单间成功结论下
可展开一张轨迹图，后端在**同一次计算**里复用背景扣除、峰值定位与 [-25, -5] dB
窗口，额外返回：

| 字段 | 含义 |
| --- | --- |
| `decay_trail.total_points` | 完整窗口取点数（与 `points_used` 一致） |
| `decay_trail.sampled_points` | 展示用取样点数组 `[{time_seconds, db}]`，横轴为**峰值后秒数** |
| `decay_trail.fit_line` | `{t_start, db_start, t_end, db_end}`：**完整回归线**在窗口首末 t 处的两个端点 |

- 完整窗口点**超过 200 个**时，`sampled_points` 按**首尾必留、等距索引**取样到 200 个
  （索引 `round(k·(N−1)/199)`，不重复）；不超过 200 时全部返回；
- **斜率、R²、T20 仍使用完整窗口计算**，取样只影响 `sampled_points`，
  `fit_line` 端点取自未取样的同一条最小二乘直线；
- 单间与批量的**正常项字段完全一致**（批量仅多 `room_id`）；衰减拒绝（`rejected`）
  与字段错误（`invalid`）响应**不携带** `decay_trail`；
- 页面提交单间采样后**先显示原结论**，展开「衰减轨迹」即可按峰值后时间查看取样点与
  拟合线，图旁标明**完整取点数与已展示点数**；展开 / 收起只切换本地状态、**不重复发请求**，
  收起后本次结果不丢失；
- 升级期间旧服务未返回该字段时，入口显示「**暂无衰减轨迹**」，不凭半截字段画图；
  拒绝响应与字段错误不生成图形数据，**新提交或请求失败会清除上一张图**。

### 输入 JSON

| 字段 | 约束 |
| --- | --- |
| `sample_interval_ms` | 1 至 100 的整数 |
| `pressure` | 200 至 20000 个大于零的数 |
| `limit_seconds` | 0.30 至 5.00 |

## 批量复核 `POST /api/evaluate-batch`

工程师一次验收多间会议室时，前端切换到「批量复核」并上传 1 至 20 个房间的 JSON：

```json
{
  "items": [
    {"room_id": "A101", "sample_interval_ms": 1, "pressure": [...], "limit_seconds": 1.0},
    {"room_id": "A102", "sample_interval_ms": 1, "pressure": [...], "limit_seconds": 1.0}
  ]
}
```

- 每项以 `room_id`（非空字符串）标识，并复用单次入口的采样间隔、压力序列和上限字段；
- 后端逐项调用同一个 T20 计算，正常项证据与 `/api/evaluate` 单独提交完全一致；
- `items` 严格保持输入顺序，响应形如：

```json
{
  "items": [
    {"room_id": "A101", "status": "ok", "t20_seconds": 0.5, "r_squared": 1.0, "fit_quality": "stable", "...": "正常项保留全部证据字段"},
    {"room_id": "A102", "status": "rejected", "reason": "衰减异常项仍只含拒绝原因"},
    {"status": "invalid", "index": 2, "room_id": "B-03", "errors": [{"field": "pressure", "message": "长度不足（最小长度：200）"}]}
  ],
  "summary": {"total": 3, "ok": 1, "passed": 1, "failed": 0, "rejected": 1, "invalid": 1}
}
```

- 部分失败（衰减异常 / 字段错误）不影响其余房间；汇总只统计正常项（`ok`）的合格与不合格；
- `room_id` 缺失或不是字符串时该项 `room_id` 为 `null`，凭 `index`（从 0 起）与字段错误定位；仅含空白的标识原样回传，同样凭 `index` 定位；
- 整批无法解析、请求体不含 `items` 数组、房间数超出 1–20、或 `room_id` 重复，均返回请求级
  `400 {"detail": "..."}`，整批拒绝、不产生任何逐项结论。

### 统一限值 `common_limit_seconds`

同一楼层采用统一验收标准时，不必为每个房间重复填写限值：在请求对象顶层给出可选的
`common_limit_seconds`（0.30 至 5.00 的 JSON 数字），批量页勾选「统一限值」即会自动附加：

```json
{
  "common_limit_seconds": 1.0,
  "items": [
    {"room_id": "A101", "sample_interval_ms": 1, "pressure": [...]},
    {"room_id": "A102", "sample_interval_ms": 1, "pressure": [...], "limit_seconds": 0.5}
  ]
}
```

- 提供统一限值时，条目可省略 `limit_seconds`；条目与顶层同时给出时**以顶层值为准**，
  条目里残留的旧限值（即使不合法）整列忽略、不再判为字段错误，逐项仍调用同一个 T20 计算；
- 未提供（或为 `null`）时完全按各条目原值处理，缺少条目限值仍是该行的字段错误；
- 正常项证据中的 `limit_seconds` 逐行回显实际采用的限值，合格汇总据此更新，
  衰减异常与字段错误行留在原位置、不受统一限值影响；
- 统一限值为字符串、布尔值、非有限数或越界值时，整批返回
  `400 {"detail": "统一限值必须是0.30至5.00的JSON数字"}`，不产生任何逐项结论。

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

它对真实服务做端到端断言：独立 oracle 复算 T20/斜率/取点数/R²、相等算合格、同一采样两次响应逐字节一致、异常衰减只暴露拒绝原因、非法输入 422、混合批次顺序稳定且正常项证据与单独提交一致、汇总只计正常项、重复 `room_id` 与无法解析批次被整批拒绝、统一限值与等值逐房间限值结论一致且非法统一限值整批 400、R² 四舍五入与阈值两侧（稳定/需复查）标签及「提示不影响判定」、衰减轨迹的等距取样（超 200 点首尾必留、索引不重复）与回归线端点取自完整窗口、拒绝/字段错误不含图形数据、旧服务缺字段兼容、Web 反代链路一致。

`verify/test_compose_ports.py` 另以纯 YAML 解析回归 `WEB_PORT` / `API_PORT` 的宿主端口可配置性（无需 Docker 守护进程）：

```bash
.venv/bin/pip install pyyaml
.venv/bin/python -m pytest verify/test_compose_ports.py
```

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
