# Maritime Data Agent

轻量级本地智能问数系统，面向表格数据的自然语言查询场景。项目支持 CSV / Excel 上传、业务术语管理、多表意图识别、NL2SQL 生成、安全 SQL 校验和可追溯自然语言回答。

## 项目概览

本项目参考 SQLBot 类产品链路，构建了一套可本地运行的数据问答原型。用户可以上传业务数据表，维护术语库，并使用自选的大模型服务生成 SQLite 查询语句。系统会在执行前校验 SQL 权限和表访问范围，执行后返回原始查询结果、生成依据和自然语言答案，便于人工核验。

当前项目不生成图表，重点放在表格查询、回答可靠性和追溯信息展示。

## 核心功能

- 上传 CSV、XLSX、XLS 数据表，并自动识别字段和数据类型
- 管理数据集、业务术语、同义词和关联表范围
- 支持 OpenAI、DeepSeek、通义千问及自定义 OpenAI 兼容接口
- 通过“术语检索 -> 意图识别 -> NL2SQL -> SQL 校验 -> 只读执行 -> 答案生成”完成问数流程
- 支持根据船舶标识、MMSI、IMO、事件编号等注册键进行多表联立查询
- 展示 SQL 生成依据、候选表、追溯编号、耗时、Token 用量、SQL 和查询结果
- API Key 仅保存在浏览器 `sessionStorage`，服务端不持久化密钥

## 技术栈

- Backend: FastAPI, SQLite, pandas
- Frontend: HTML, CSS, JavaScript
- LLM: OpenAI-compatible Chat Completions API
- Testing: pytest

## 目录结构

```text
.
├── app/
│   ├── main.py          # FastAPI 入口和主要接口
│   ├── db.py            # SQLite 数据集、术语和会话存储
│   ├── intent.py        # 单表 / 多表意图识别
│   ├── llm.py           # 大模型调用和 SQL 生成
│   ├── query.py         # SQL 安全校验与只读执行
│   ├── multitable.py    # 多表候选补充与连接规则
│   └── static/          # 前端页面、样式和交互逻辑
├── docs/
│   └── TECHNICAL_PATH.md
├── tests/
├── requirements.txt
└── README.md
```

## 本地运行

建议使用 Python 3.10 或更高版本。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

启动后访问：

```text
http://127.0.0.1:8000
```

API 文档地址：

```text
http://127.0.0.1:8000/docs
```

## 使用流程

1. 在“数据表”页面上传 CSV 或 Excel 文件。
2. 在“术语库”页面补充业务术语、定义、同义词和关联数据表。
3. 在“模型设置”页面选择模型服务商，并填写自己的 API Key。
4. 回到“问数助手”，选择数据表并输入自然语言问题。
5. 查看系统返回的答案、SQL、查询结果和追溯信息。

## 安全设计

- 后端只允许执行单条 `SELECT` 或 `WITH ... SELECT` 查询。
- 拦截写入、建表、删除、`PRAGMA`、`ATTACH` 等高风险 SQL。
- 单表问题只能访问当前选择的数据集表。
- 多表问题只能访问系统识别并加入白名单的数据表。
- SQLite 连接以只读模式打开，查询结果默认限制返回 200 行。
- API Key 不写入数据库、不写入日志；生产部署建议启用 HTTPS，并考虑改为服务端密钥托管。

## 数据说明

仓库默认不提交本地数据库和实验数据文件：

- `data/smart_query.db`
- `data/uploads/`
- `data/智能取数-实验数据-0805/`
- `outputs/`

这些文件可能体积较大，或包含业务数据。公开发布前应确认数据授权和脱敏情况。

## 测试

安装测试依赖后可运行：

```bash
pip install pytest
pytest
```

## License

当前仓库暂未指定开源许可证。公开复用或分发前，建议根据项目开放范围补充合适的 LICENSE 文件。
