# Invoice Collect / 发票归集系统

**Language / 语言:** [English](#english) | [中文](#zh)

---

## English

### Overview

A full-stack web app for uploading invoices, extracting structured fields (via NuExtract-style service), automatically classifying them into expense categories, and grouping related invoices (travel loops, meeting batches). You can adjust results by dragging invoices in the UI.

### Features

- **Categories** (see `config/categories.yml`): **travel**, **meeting**, **material**, **other**. The first three are *groupable*; **other** is not.
- **Travel**: Detects inter-city transport chains and closed loops; attaches hotels, taxis, etc. in the time window to the same trip group.
- **Meeting**: Heuristic grouping (same seller + same calendar day), optionally refined by LLM when multiple invoices exist.
- **Material**: Classified like other categories; automatic trip/meeting algorithms do not apply—use manual groups or ungrouped as needed.
- **Extraction**: Integrates with a NuExtract HTTP API (`nuextract` section in `config/models.yml`).
- **Classification pipeline**: Invoice subcategory (when present) → YAML rules (`config/rules.yml`) → LLM fallback → default **other**.

### Tech stack

- Backend: FastAPI, SQLAlchemy 2 (async) + SQLite (`invoice_collect.db`)
- Frontend: Jinja2 templates, static HTML/CSS/JS
- AI: OpenAI-compatible Chat Completions API (`config/models.yml` → `llm`)

### Project layout

| Path | Role |
|------|------|
| `main.py` | App entry, static mounts, logging bootstrap |
| `app/routers/` | `invoices`, `collections`, `config` API |
| `app/services/` | `extractor`, `classifier`, `grouper`, `llm_client`, `progress` |
| `config/` | `categories.yml`, `rules.yml`, `models.yml` |
| `templates/`, `static/` | Web UI |
| `uploads/` | Stored upload files |

### Configuration

- **`config/models.yml`**
  - `llm`: `base_url`, `api_key`, `model`, `timeout` for classification / meeting grouping.
  - `nuextract`: `host`, `port`, `timeout` for field extraction.
- **`config/categories.yml`**: Category ids, names, descriptions, `groupable`.
- **`config/rules.yml`**: Ordered rules (`priority`, `conditions`, `target_category`).

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

Default bind: `127.0.0.1:8088` (see `main.py`). Alternatively:

```bash
uvicorn main:app --host 127.0.0.1 --port 8088
```

Open `http://127.0.0.1:8088/` in a browser.

### Collection flow and main APIs

1. Upload invoices → extraction runs → `extract_status=done`.
2. Start collection: `POST /api/collections/process` → SSE `GET /api/collections/stream/{task_id}`.
3. Fetch board: `GET /api/collections/result`.
4. Manual fixes: `PATCH /api/collections/move`, batch move, group CRUD under `/api/collections/groups/*`.
5. Invoice CRUD: `/api/invoices/*`. Dynamic config: `/api/config/*`.

### Logging (troubleshooting accuracy)

On `INFO`, logs include: collection task options and summaries; per-invoice classification path (subcategory / rule id / LLM); travel loop counts; meeting heuristic vs LLM adoption. Use `DEBUG` for richer context.

---

<a id="zh"></a>

## 中文

### 项目简介

全栈发票归集应用：上传发票、调用 NuExtract 类服务做字段抽取，再按规则与可选 LLM 归入费用大类，并对差旅、会议等做自动分组；支持在界面中拖拽修正归属。

### 功能说明

- **费用大类**（以 `config/categories.yml` 为准）：**差旅费**（travel）、**会议费**（meeting）、**材料费**（material）、**其他费用**（other）。前三种默认可分组，**其他费用**不分组。
- **差旅费**：根据城市间交通票做行程链与闭环检测，并把时间窗口内的住宿、打车等并入同一行程组。
- **会议费**：默认按「同一销售方 + 同一天」启发式分组；多张票时可由 LLM 细化分组（需配置 LLM）。
- **材料费**：与其他大类一样参与自动分类；当前归集任务不包含类似差旅/会议的专用自动分组逻辑，可使用未分组或手动建组。
- **抽取**：通过 `config/models.yml` 中的 `nuextract` 连接抽取服务。
- **分类顺序**：`invoice_subcategory`（若有）→ `config/rules.yml` 规则 → LLM 兜底 → 默认归入 **other**。

### 技术栈

- 后端：FastAPI、SQLAlchemy 2 异步 + SQLite（`invoice_collect.db`）
- 前端：Jinja2、`static/` 下原生 JS/CSS
- 大模型：兼容 OpenAI 的 Chat Completions API（`config/models.yml` 的 `llm` 段）

### 目录结构

| 路径 | 说明 |
|------|------|
| `main.py` | 应用入口、静态资源挂载、日志初始化 |
| `app/routers/` | 发票、归集、配置等 API |
| `app/services/` | 抽取、分类、分组、LLM、进度推送 |
| `config/` | 大类、规则、模型与外部服务地址 |
| `templates/`、`static/` | 网页界面 |
| `uploads/` | 上传文件存储 |

### 配置说明

- **`config/models.yml`**：`llm`（分类与会议分组）、`nuextract`（抽取服务地址与超时）。
- **`config/categories.yml`**：大类 id、名称、描述、是否可分组。
- **`config/rules.yml`**：规则优先级、匹配条件、`target_category`。

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

默认监听 `127.0.0.1:8088`（见 `main.py`）。也可使用：

```bash
uvicorn main:app --host 127.0.0.1 --port 8088
```

浏览器访问 `http://127.0.0.1:8088/`。

### 归集流程与主要 API

1. 上传发票 → 抽取完成（`extract_status=done`）。
2. 触发归集：`POST /api/collections/process`，通过 `GET /api/collections/stream/{task_id}` 订阅 SSE 进度。
3. 看板数据：`GET /api/collections/result`。
4. 手动调整：`PATCH /api/collections/move`、批量移动、`/api/collections/groups/*` 管理分组。
5. 发票与配置：`/api/invoices/*`、`/api/config/*`。

### 日志与排错（分类/分组不准时）

在 **INFO** 下可看到：归集任务参数与分类汇总、每张发票的分类路径（子类型 / 命中规则 / LLM）、差旅组数量与闭环数、会议是否采用 LLM 等。**DEBUG** 下信息更细，便于对照规则与抽取字段。

---

[↑ Back to top / 返回顶部](#invoice-collect--发票归集系统)
