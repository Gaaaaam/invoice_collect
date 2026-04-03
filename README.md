# Invoice Collect / 发票归集系统

**Language / 语言:** [English](#english) | [中文](#zh)

---

## English

<a id="english"></a>

### Overview

A full-stack web app for uploading invoices, extracting structured fields (via a NuExtract-compatible HTTP service), automatically classifying them into expense categories, and grouping related invoices (travel loops, meeting batches). You can adjust results by dragging invoices in the UI.

### Features

- **Categories** (see `config/categories.yml`): **travel**, **meeting**, **material**, **other**. The first three are *groupable*; **other** is not.
- **Travel**: Uses `config/travel.yml` → `home_city` as the reference for closed-loop trips; detects inter-city transport chains and closed loops; attaches hotels, taxis, etc. in the time window to the same trip group.
- **Meeting**: Heuristic grouping (same seller + same calendar day), optionally refined by LLM when multiple invoices exist.
- **Material**: Classified like other categories; automatic trip/meeting algorithms do not apply—use manual groups or ungrouped as needed.
- **Extraction (NuExtract path)**: Two-stage flow driven by `config/nuextract_templates.json`: (1) classify invoice type from the `invoice_type` list, (2) extract with the matching JSON template (e.g. regular invoice, railway e-ticket, air itinerary, train reimbursement slip). Taxi, ride-hailing, and hotel types still use built-in Python templates (single-step).
- **Fallback**: `extraction` in `config/models.yml` — `provider` `nuextract` / `ocr_fallback` / `auto`; optional `use_llm_on_fallback` when local OCR path needs LLM help; `ocr.engine` (`rapidocr_onnx`, `tesseract`, `easyocr`), `pdf_max_pages`, `min_text_chars`.
- **Classification pipeline**: Invoice subcategory (when present) → YAML rules (`config/rules.yml`) → LLM fallback → default **other**.

### Tech stack

- Backend: FastAPI, SQLAlchemy 2 (async) + SQLite (`invoice_collect.db`)
- Frontend: Jinja2 templates, static HTML/CSS/JS
- AI: OpenAI-compatible Chat Completions API (`config/models.yml` → `llm`); NuExtract client in root `nuextract.py` (`/api/v1/nuextract/` upload API)

### Project layout

| Path | Role |
|------|------|
| `main.py` | App entry, static mounts, CORS, logging bootstrap |
| `nuextract.py` | Async HTTP client for NuExtract service |
| `app/routers/` | `invoices`, `collections`, `config` API |
| `app/services/` | `extractor`, `fallback_extract`, `classifier`, `grouper`, `llm_client`, `progress`, `station_city_map` |
| `config/categories.yml` | Category ids, names, descriptions, `groupable` |
| `config/rules.yml` | Ordered rules (`priority`, `conditions`, `target_category`) |
| `config/models.yml` | `llm`, `nuextract`, `extraction` |
| `config/nuextract_templates.json` | Invoice types + per-type extraction schemas for the two-stage NuExtract flow |
| `config/travel.yml` | Travel settings, e.g. `home_city` for loop detection |
| `requirements.txt` | Runtime dependencies (API + default OCR fallback stack) |
| `templates/`, `static/` | Web UI |
| `uploads/` | Stored upload files |

### Configuration

- **`config/models.yml`**
  - `llm`: `base_url`, `api_key`, `model`, `timeout` for classification / meeting grouping.
  - `nuextract`: `host`, `port`, `timeout` for field extraction.
  - `extraction`: `provider` — `nuextract` (remote only), `ocr_fallback` (local only), `auto` (try NuExtract then OCR). `use_llm_on_fallback` toggles LLM use on the fallback path. `ocr.engine` defaults to `rapidocr_onnx` (`rapidocr_onnx`, `tesseract`, `easyocr`), with `pdf_max_pages` and `min_text_chars`.
  - The config API merges on save so existing keys (e.g. `extraction`) are preserved; the in-app editor may only expose part of the file—edit YAML directly for full control.
- **`config/nuextract_templates.json`**: Extend or adjust supported `invoice_type` values and template objects; must stay consistent with extractor mappings in `app/services/extractor.py`.
- **`config/travel.yml`**: `home_city` — user’s base city for travel closed-loop logic (also `GET`/`PUT` `/api/config/travel`).
- **`config/categories.yml`**, **`config/rules.yml`**: As above.

Do not commit real API keys; use placeholders in shared repos.

### Environment variables

| Variable | Meaning |
|----------|---------|
| `INVOICE_COLLECT_LOG_LEVEL` | Python log level, default `INFO`. Use `DEBUG` for per-invoice details and raw LLM snippets. |

### Install and run

```bash
cd invoice_collect
pip install -r requirements.txt
python main.py
```

**OCR fallback notes:** `rapidocr_onnx` + `onnxruntime` work with `pip` only (models download on first run). **Tesseract** requires a system `tesseract` binary in addition to `pytesseract`/`pillow`. **EasyOCR** pulls large PyTorch-based dependencies (commented in `requirements.txt`).

Default bind: `127.0.0.1:8088` with `reload=True` (see `main.py`). For production, prefer:

```bash
uvicorn main:app --host 127.0.0.1 --port 8088
```

Open `http://127.0.0.1:8088/`. Interactive API docs: `http://127.0.0.1:8088/docs`.

### Collection flow and main APIs

1. Upload: `POST /api/invoices/upload` → extraction runs → `extract_status=done` on success, `error` if both NuExtract and OCR fallback fail. `POST /api/invoices/{id}/re-extract` to retry one invoice.
2. Start collection: `POST /api/collections/process` → SSE `GET /api/collections/stream/{task_id}`; `POST /api/collections/cancel/{task_id}` to cancel.
   - Archived invoices in the UI live in browser `localStorage` and are sent as `exclude_invoice_ids`, so they are skipped in this run.
3. Board: `GET /api/collections/result`.
4. Manual fixes: `PATCH /api/collections/move`, `PATCH /api/collections/move/batch`, group CRUD under `/api/collections/groups/*`.
5. Invoices: `GET/DELETE /api/invoices/{id}`, `GET /api/invoices/{id}/file`, `POST /api/invoices/batch-delete`, list `GET /api/invoices`.
6. Config: `/api/config/categories`, `/rules`, `/models`, `/travel` (`GET`/`PUT` as applicable).

### Logging (troubleshooting accuracy)

On `INFO`, logs include: collection task options and summaries; per-invoice classification path (subcategory / rule id / LLM); travel loop counts; meeting heuristic vs LLM adoption. Use `DEBUG` for richer context.

### Deployment notes

- Persist both `invoice_collect.db` and `uploads/` via volume mounts; SQLite is not designed for multi-instance writes.
- Ensure the app host can reach the NuExtract endpoint configured in `models.yml` (client uses `/api/v1/nuextract/`).
- For production, disable reload and run behind a process manager with explicit host/port, CORS policy, and secret injection.
- Keep real keys out of VCS; use placeholders in config files and inject secrets during deployment.

---

<a id="zh"></a>

## 中文

### 项目简介

全栈发票归集应用：上传发票、通过兼容 NuExtract 的 HTTP 服务做结构化字段抽取，再按规则与可选 LLM 归入费用大类，并对差旅、会议等做自动分组；支持在界面中拖拽修正归属。

### 功能说明

- **费用大类**（以 `config/categories.yml` 为准）：**差旅费**（travel）、**会议费**（meeting）、**材料费**（material）、**其他费用**（other）。前三种默认可分组，**其他费用**不分组。
- **差旅费**：读取 `config/travel.yml` 中的 **`home_city`**（常住/出发参照城市），据此做行程链与闭环检测，并把时间窗口内的住宿、打车等并入同一行程组。
- **会议费**：默认按「同一销售方 + 同一天」启发式分组；多张票时可由 LLM 细化分组（需配置 LLM）。
- **材料费**：与其他大类一样参与自动分类；当前归集任务不包含类似差旅/会议的专用自动分组逻辑，可使用未分组或手动建组。
- **抽取（NuExtract 路径）**：由 `config/nuextract_templates.json` 驱动的**两阶段**流程——先用 `invoice_type` 列表判别票种，再按票种选用对应 JSON 模板做字段抽取（如电子普票、铁路电子客票、航空行程单、火车票报销凭证等）。出租车/网约车/酒店等仍走内置 Python 模板（单阶段）。
- **保底抽取**：`config/models.yml` 的 **`extraction`** 段——`provider` 可选 `nuextract` / `ocr_fallback` / `auto`；**`use_llm_on_fallback`** 控制本地保底路径是否再调用 LLM；`ocr` 含 `engine`、`pdf_max_pages`、`min_text_chars`。
- **分类顺序**：`invoice_subcategory`（若有）→ `config/rules.yml` 规则 → LLM 兜底 → 默认归入 **other**。

### 技术栈

- 后端：FastAPI、SQLAlchemy 2 异步 + SQLite（`invoice_collect.db`），开发环境启用 CORS（`main.py`）
- 前端：Jinja2、`static/` 下原生 JS/CSS
- 大模型：兼容 OpenAI 的 Chat Completions API（`config/models.yml` 的 `llm` 段）
- NuExtract：根目录 `nuextract.py` 异步客户端，请求服务端 `/api/v1/nuextract/`

### 目录结构

| 路径 | 说明 |
|------|------|
| `main.py` | 应用入口、静态资源挂载、CORS、日志初始化 |
| `nuextract.py` | NuExtract HTTP 客户端 |
| `app/routers/` | 发票、归集、配置等 API |
| `app/services/` | 抽取/保底抽取、分类、分组、LLM、进度推送、站点城市映射 |
| `config/categories.yml` | 大类 id、名称、描述、是否可分组 |
| `config/rules.yml` | 规则优先级、匹配条件、`target_category` |
| `config/models.yml` | LLM、NuExtract、抽取策略与 OCR 参数 |
| `config/nuextract_templates.json` | 票种列表与各票种抽取模板（两阶段 NuExtract） |
| `config/travel.yml` | 差旅归集参数（如 `home_city`） |
| `requirements.txt` | 运行依赖（含默认 OCR 保底栈） |
| `templates/`、`static/` | 网页界面 |
| `uploads/` | 上传文件存储 |

### 配置说明

- **`config/models.yml`**：`llm`（分类与会议分组）、`nuextract`（抽取服务地址）、`extraction`（`provider`、`use_llm_on_fallback`、`ocr` 等）。保存时 API 会与磁盘已有内容合并，避免误删未在界面编辑的字段；完整修改可直接编辑 YAML。
- **`config/nuextract_templates.json`**：可扩展 `invoice_type` 与各模板结构；需与 `app/services/extractor.py` 中的类型映射保持一致。
- **`config/travel.yml`**：`home_city`（闭环判断参照城市）；也可通过 **`GET` / `PUT` `/api/config/travel`** 读写。
- **`config/categories.yml`**、**`config/rules.yml`**：同上表。

请勿将真实 API 密钥提交到公共仓库。

### 环境变量

| 变量 | 说明 |
|------|------|
| `INVOICE_COLLECT_LOG_LEVEL` | 日志级别，默认 `INFO`；设为 `DEBUG` 可查看单张发票上下文、LLM 原始片段等 |

### 安装与运行

```bash
cd invoice_collect
pip install -r requirements.txt
python main.py
```

**OCR 说明：** 默认引擎 `rapidocr_onnx` 可通过 pip 与首次运行拉取的模型工作；**Tesseract** 需单独安装系统程序，并安装 `pytesseract`/`pillow`；**EasyOCR** 依赖较重（见 `requirements.txt` 注释）。

默认监听 `127.0.0.1:8088` 且开启热重载（见 `main.py`）。生产环境建议使用：

```bash
uvicorn main:app --host 127.0.0.1 --port 8088
```

浏览器访问 `http://127.0.0.1:8088/`；接口文档：`http://127.0.0.1:8088/docs`。

### 归集流程与主要 API

1. **上传**：`POST /api/invoices/upload` → 抽取完成后 `extract_status=done`；远程与本地均失败则为 `error`。单张重抽：`POST /api/invoices/{invoice_id}/re-extract`。
2. **归集**：`POST /api/collections/process`，进度流 `GET /api/collections/stream/{task_id}`；取消：`POST /api/collections/cancel/{task_id}`。
   - 前端「历史归档」存于浏览器 `localStorage`，通过 `exclude_invoice_ids` 传给后端，归集时排除这些发票。
3. **看板**：`GET /api/collections/result`。
4. **手动调整**：`PATCH /api/collections/move`、`PATCH /api/collections/move/batch`、`/api/collections/groups/*` 管理分组。
5. **发票**：列表 `GET /api/invoices`，详情/删除 `GET`/`DELETE /api/invoices/{id}`，原文件 `GET /api/invoices/{id}/file`，批量删除 `POST /api/invoices/batch-delete`。
6. **配置**：`/api/config/categories`、`/rules`、`/models`、`/travel`（按需 `GET`/`PUT`）。

### 日志与排错（分类/分组不准时）

在 **INFO** 下可看到：归集任务参数与分类汇总、每张发票的分类路径（子类型 / 命中规则 / LLM）、差旅组数量与闭环数、会议是否采用 LLM 等。**DEBUG** 下信息更细，便于对照规则与抽取字段。

### 部署注意事项

- `invoice_collect.db` 与 `uploads/` 需做持久化挂载；SQLite 不适合多实例并发写入。
- 需确保应用节点可访问 `models.yml` 中配置的 NuExtract 服务（客户端路径为 `/api/v1/nuextract/`）。
- 生产环境应关闭热重载，使用进程管理启动，并按需收紧 CORS、注入密钥。
- `models.yml` 中密钥请使用占位符，真实值通过环境/密钥服务注入，避免入库与泄露。

---

[↑ Back to top / 返回顶部](#invoice-collect--发票归集系统)
