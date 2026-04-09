# Invoice Collect / 发票归集系统 🧾

**语言 / Language**: [中文](./README.md) · [English](./README.en.md)

一款全栈发票管理系统，支持发票自动提取、智能分类、差旅/会议自动聚类。

---

## 📖 简介

**核心痛点解决**：报销贴票、整理发票是一项繁琐的工作。本项目旨在通过 AI 技术与规则引擎，实现发票的自动化结构提取，并根据业务逻辑（如出差行程、会议批次）自动将发票聚类归集，极大地降低人工整理的成本。

## ✨ 核心特性

- 🧠 **双阶提取**：结合 NuExtract 与 OCR 降级方案（RapidOCR/Tesseract/EasyOCR），精准识别各类发票字段。
- ✈️ **差旅闭环**：基于城市与时间链条，自动将车票、机票、酒店、打车等发票归集为一次“差旅”。
- 📅 **智能聚类**：利用启发式算法（同一销售方+同一天）与 LLM 识别会议等批量发票。
- 🖱️ **人工干预**：支持在 UI 上通过拖拽调整归集结果，满足复杂或异常的报销场景。

## 📸 界面展示

### 1. 架构概览
![项目架构图](./assets/system_structure_ch.webp)


### 2. 主界面与发票上传
![主界面](./assets/main.png)


### 3. 智能分组与拖拽交互
![交互](./assets/pull_and_drag.gif)


## 🚀 快速开始

### 前置要求

- **Python** >= 3.9
- **API Key** (可选，推荐)：配置兼容 OpenAI 的大模型 API，用于更精准的分类和会议分组。
- **NuExtract** (可选)：如需强大的结构化提取能力，需自备 NuExtract 服务；否则系统将自动降级使用本地 OCR。

### 安装与运行

```bash
# 1. 克隆项目
git clone https://github.com/Gaaaaam/invoice_collect.git
cd invoice_collect

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置文件准备
# 项目自带默认配置，您可以根据需要修改 config 目录下的文件，如 models.yml, travel.yml 等。
# 重点：配置 config/models.yml 中的大模型与提取服务参数，或者稍后在 UI 中配置。

# 4. 启动服务
python main.py
# 生产环境建议使用: uvicorn main:app --host 127.0.0.1 --port 8088
```

访问 `http://127.0.0.1:8088/` 即可体验。

### OCR 降级与依赖兼容

未配置 NuExtract 时，系统使用本地 **RapidOCR（基于 ONNX Runtime）** 等组件识别票面文字，请按 `requirements.txt` 完整安装 OCR 相关依赖。

在 **NumPy 2.x** 环境下必须使用 **onnxruntime ≥ 1.19**（本仓库已约束 `onnxruntime>=1.19.2`）。若环境中仍为旧版 onnxruntime（例如曾固定 1.16），可能出现 `_ARRAY_API` 或 `numpy.core.multiarray` 等导入错误，可执行：

```bash
pip install -U "onnxruntime>=1.19.2,<2"
```

或重新执行 `pip install -r requirements.txt`。若需其他引擎，可在 `config/models.yml` 中将 OCR 配置为 `tesseract` 或 `easyocr`（需自行安装对应 Python 包与系统组件）。

### Docker 部署
```
docker pull kyriegan1007/invoice-collect:latest
```

### 环境变量

| 变量 | 说明 |
|------|------|
| `INVOICE_COLLECT_LOG_LEVEL` | 日志级别，默认 `INFO`；设为 `DEBUG` 可查看单张发票上下文、LLM 原始片段等 |
| `INVOICE_COLLECT_DATA_DIR` | 可选。存放 `invoice_collect.db` 与 `uploads/` 的目录；默认与源码根目录一致。Docker 中常设为 `/data` 并挂载数据卷。 |
| `INVOICE_COLLECT_HOST` | Uvicorn 监听地址（镜像内默认 `0.0.0.0`）。 |
| `INVOICE_COLLECT_PORT` | 监听端口（镜像内默认 `8088`）。 |
| `INVOICE_COLLECT_LLM_BASE_URL` | 可选。运行时覆盖 `config/models.yml` 中的 `llm.base_url`。 |
| `INVOICE_COLLECT_LLM_API_KEY` | 可选。运行时覆盖 `llm.api_key`。 |
| `INVOICE_COLLECT_LLM_MODEL` | 可选。运行时覆盖 `llm.model`。 |
| `INVOICE_COLLECT_LLM_TIMEOUT` | 可选。运行时覆盖 `llm.timeout`（秒，整数）。 |
| `INVOICE_COLLECT_NUEXTRACT_HOST` | 可选。运行时覆盖 `nuextract.host`。 |
| `INVOICE_COLLECT_NUEXTRACT_PORT` | 可选。运行时覆盖 `nuextract.port`。 |
| `INVOICE_COLLECT_NUEXTRACT_TIMEOUT` | 可选。运行时覆盖 `nuextract.timeout`（秒，整数）。 |

### 指令示例

``` bash
docker run -d --name invoice-collect -p 8088:8088 \
-v /your/data/path:/data \
-e INVOICE_COLLECT_DATA_DIR=/data \
-e INVOICE_COLLECT_LLM_BASE_URL=your-llm-base-url \
-e INVOICE_COLLECT_LLM_MODEL=your-model-name \
-e INVOICE_COLLECT_LLM_API_KEY=your-api-key \
-e INVOICE_COLLECT_NUEXTRACT_HOST=your-nuextract-host \
-e INVOICE_COLLECT_NUEXTRACT_PORT=your-nuextract-port \
kyriegan1007/invoice-collect:latest
```

访问 `http://your-service-ip:8088/` 即可体验。

## 💡 核心逻辑深度解析

本项目不仅提供了基础设施，更内置了贴合真实报销场景的业务逻辑。

### 1. 差旅闭环检测逻辑

差旅归集不只是简单的时间聚合，系统会结合 `config/travel.yml` 中配置的 `home_city`（常住/出发参照城市）进行**行程链条推导**：

- 当识别到离开 `home_city` 的交通票据（如高铁、机票），即标记为一次差旅的开始。
- 后续的交通票据将作为行程节点串联。
- 当识别到返回 `home_city` 的票据，则视为形成**“闭环”**。
- 系统会将这一时间窗口内的住宿费、市内交通费（打车）自动挂载到该闭环行程组中。

### 2. NuExtract 双阶提取模板

为了应对中国繁杂的票据种类，系统设计了基于 `config/nuextract_templates.json` 的双阶提取流程：

1. **类型判别**：首先判断发票的具体类型（如铁路电子客票、航空行程单等）。
2. **定向抽取**：根据判断出的类型，应用 JSON 模板中对应的高度定制化 schema，从而最大化发挥大模型结构化提取的准确度。并且这种设计极具扩展性，只需修改 JSON 文件即可轻松支持新票种。

## ⚙️ 配置指南

系统的大部分行为由 `config/` 目录下的 YAML/JSON 文件控制。


| 配置文件                       | 作用说明                                     |
| -------------------------- | ---------------------------------------- |
| `models.yml`               | 配置 LLM 接口地址与密钥、NuExtract 服务地址、OCR 引擎选择等。 |
| `rules.yml`                | 核心分类规则引擎配置。定义“字段包含某字符则归入某类”的规则树。         |
| `categories.yml`           | 定义报销大类（如差旅费、会议费、材料费等）及其是否允许分组。           |
| `travel.yml`               | 差旅特定配置，如核心的 `home_city` 变量。              |
| `nuextract_templates.json` | 定义各类型发票的结构化提取 Schema。                    |


### 配置示例：自定义分类规则 (`rules.yml`)

您可以轻松通过规则引擎将特定发票划入特定类别，以下是一个典型的配置示例：

```yaml
- id: rule_hotel_to_travel
  name: 住宿费归差旅费
  priority: 20
  conditions:
  - field: invoice_type
    match_type: contains
    value: 住宿
  - field: seller_name
    match_type: regex
    value: (酒店|宾馆|旅馆|民宿|客栈)
  condition_logic: OR
  target_category: travel
```

## 🛠 技术栈

- **后端**: FastAPI, SQLAlchemy 2 (async), SQLite
- **前端**: Jinja2, HTML5 / Vanilla JS / CSS3
- **AI / 核心能力**: OpenAI-compatible LLM 接口, NuExtract (异步 HTTP 客户端), RapidOCR/Tesseract/EasyOCR

## 🗺 项目路线图

- 支持更多类型的电子票据（如行程单 PDF 直接解析）。
- 增加报销单自动生成导出功能（Excel/PDF）。
- 对接更多国产 LLM 模型（如通义千问、DeepSeek）以提供开箱即用的体验优化。
- *(规划中)* 完善移动端浏览体验。

## 📄 开源协议

[Apache 2.0 License](LICENSE)