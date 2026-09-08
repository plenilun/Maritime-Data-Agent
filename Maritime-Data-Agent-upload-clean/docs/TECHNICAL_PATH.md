# 智能问数技术路径与变更记录

本文记录智能问数 demo 的表域分析、多表联合问数路径，以及每次对网页和问数链路施加的工程改动。

## 1. 当前数据表领域覆盖

| 数据表 | 主要领域 | 典型问题 |
|---|---|---|
| `cross_record_line` | VTS 报告线进入/穿越事件 | 今天进来吴淞 VTS 区域有多少船；某 MMSI 历史来过几次 |
| `section_flow_judge` | 截面上行/下行流量事件 | 早上 8 点到 10 点截面上行/下行流量 |
| `data_real_time` | 当前 AIS/船舶实时快照 | 当前吃水超过 8m 的船舶；当前位置、船名、MMSI |
| `violation_record` | 违规/执法事件 | 今天违规事件数量；AIS 关闭事件数量 |
| `risk_record` | 碰撞风险事件 | 今天风险事件数量；当前有效风险、风险等级分布 |
| `vessel_new_status_record` | 航行/锚泊/靠泊状态事件 | 当前航行、锚泊、靠泊数量；各锚地锚泊数量；船型统计 |

## 2. 字段重叠与关联能力

这几张表覆盖领域有明显重叠，重叠主要集中在四类字段。

| 字段域 | 代表字段 | 覆盖情况 | 用途 |
|---|---|---|---|
| 船舶身份 | `target_id`、`mmsi`、`imo`、`name_en` | 几乎所有表都有 `target_id`，实时表同时有 `mmsi` | 跨表识别同一船舶 |
| 时间 | `event_time`、`risk_time`、`violation_time`、`update_time`、`received_at` | 每类事件有自己的发生时间字段 | 时间窗口过滤、事件顺序分析 |
| 空间 | `lon`、`lat`、`district_code`、`on_duty_station`、`region_uuid` | 事件表普遍有经纬度和区域字段 | 区域过滤、位置上下文、截面/锚地分组 |
| 业务事件 | `event_name_code`、`event_type_code`、`risk_type_code`、`violation_type_code`、`ship_type` | 各领域有自己的枚举字段 | 意图识别、分类统计、回答口径 |

### 2.1 结构相同的事件表

`cross_record_line`、`section_flow_judge`、`vessel_new_status_record` 的字段结构完全一致，均包含 24 个同名字段：

`uuid`、`event_uuid`、`target_id`、`name_en`、`imo`、`composite_key`、`event_name_code`、`event_type_code`、`event_level_code`、`event_time`、`lon`、`lat`、`on_duty_station`、`district_code`、`event_desc_cn`、`event_desc_en`、`extend_info`、`remark`、`rule_uuid`、`update_time`、`avg_speed`、`region_uuid`、`region_type`、`ship_type`

这说明它们可复用统一的事件查询模板，但业务含义不同：

- `cross_record_line`：VTS 报告线进入事件。
- `section_flow_judge`：截面上行/下行穿越事件。
- `vessel_new_status_record`：航行、锚泊、靠泊状态事件。

### 2.2 可关联键与真实交集

`target_id` 是最适合跨表关联的船舶实体键。当前数据库中各表 `target_id` 去重数量如下：

| 数据表 | 去重 `target_id` 数 |
|---|---:|
| `cross_record_line` | 6774 |
| `data_real_time` | 3314 |
| `section_flow_judge` | 5696 |
| `violation_record` | 5860 |
| `risk_record` | 2988 |
| `vessel_new_status_record` | 4912 |

部分表之间的 `target_id` 交集很大，例如：

| 表 A | 表 B | 共同 `target_id` 数 |
|---|---|---:|
| `cross_record_line` | `section_flow_judge` | 4268 |
| `cross_record_line` | `violation_record` | 3006 |
| `violation_record` | `risk_record` | 2518 |
| `data_real_time` | `vessel_new_status_record` | 1776 |
| `cross_record_line` | `data_real_time` | 1748 |
| `risk_record` | `vessel_new_status_record` | 1329 |

`event_uuid` 不适合作为通用跨表事件键。当前只有 `violation_record` 与 `vessel_new_status_record` 存在少量交集，共 22 个，其余事件表之间交集为 0。

## 3. 是否可以多表联合解读

可以，但建议分阶段做，不建议直接放开任意多表 SQL。

当前系统安全校验仍是单表执行：`validate_sql(sql, allowed_table)` 只允许模型访问系统识别出的唯一数据表。这是为了避免模型误 join、跨表笛卡尔积、把事件数和船舶数混用。

更稳妥的多表路线是：

1. 先做多表意图识别，生成 `route_plan`。
2. 每张表分别生成只读 SQL，并沿用单表安全校验。
3. 后端汇总多个查询结果，形成统一 `TraceRecord.tables_used`、`result_summary` 和 `AnswerPayload`。
4. 答案模板负责把多个数据来源的结果合并成业务表述。
5. 只有经过白名单关系图审核的场景，才允许生成受控 JOIN SQL。

## 4. 推荐的多表联合场景

| 用户问题类型 | 推荐数据表 | 联合方式 | 注意事项 |
|---|---|---|---|
| 某船是否进入过 VTS，现在在哪里 | `cross_record_line` + `data_real_time` | 用 `target_id/mmsi` 关联；历史事件 + 当前快照 | 历史进入事件不能替代当前位置 |
| 当前深吃水船是否存在风险 | `data_real_time` + `risk_record` | 先查吃水船列表，再按 `target_id` 查有效风险 | 当前有效风险需排除 `risk_type_code='CLOSE'` |
| 截面流量与违规/风险是否相关 | `section_flow_judge` + `violation_record` + `risk_record` | 按时间窗口、`target_id` 和区域并行统计 | 不应把流量事件数与违规事件数相加 |
| 船舶状态与锚地统计 | `vessel_new_status_record` + `data_real_time` | 状态表给状态，实时表补船舶属性和位置 | 问“当前”时状态表优先筛选 `event_type_code='OPEN'` |
| 违规事件是否来自某类风险态势 | `violation_record` + `risk_record` | 可按 `target_id`、`composite_key`、时间窗口关联 | `composite_key` 是船对/组合键，需按具体业务校验 |
| 船型在穿越、风险、违规中的分布 | `section_flow_judge` + `risk_record` + `violation_record` | 并行按 `ship_type` 分组，对比输出 | 各表事件口径不同，只能对比，不能简单合计 |

## 5. 多表能力实施路径

### 5.1 已完成

- 上传和维护多张数据表。
- 术语库可关联单张表或全局。
- 自动识别用户问题需要的数据表与意图类型。
- 回答返回结构化追溯信息，包括识别意图、使用数据表、SQL、结果和追溯编号。
- 前端支持“自动识别数据表”和手动指定数据表。

### 5.2 下一步：多表并行查询

新增 `RoutePlan`：

```json
{
  "mode": "multi_query",
  "intent_type": "vessel_current_risk_by_condition",
  "steps": [
    {
      "dataset_id": "data_real_time",
      "purpose": "筛选当前深吃水船舶"
    },
    {
      "dataset_id": "risk_record",
      "purpose": "查询这些船舶的当前有效风险"
    }
  ],
  "join_keys": ["target_id"],
  "merge_rule": "按 target_id 汇总，不直接相加事件数"
}
```

执行策略：

- 每个 step 独立生成 SQL。
- 每个 SQL 仍调用单表 `validate_sql`。
- 后端用结构化数据合并结果。
- `TraceRecord.tables_used` 记录多张表。
- 前端追溯面板展示多条 SQL 和每步结果。

### 5.3 再下一步：受控 JOIN

只有当问题命中白名单关系时，才允许多表 JOIN：

| 关系 | JOIN 条件 | 适用场景 |
|---|---|---|
| 船舶实体关系 | `a.target_id = b.target_id` 或 `a.target_id = b.mmsi` | 单船历史 + 当前快照 |
| 船对关系 | `a.composite_key = b.composite_key` | 风险与违规联动 |
| 时间窗口关系 | 同一 `target_id` 且时间差在指定窗口内 | 事件前后分析 |
| 区域关系 | `district_code`、`on_duty_station`、`region_uuid` | 区域对比，不适合作为唯一 JOIN 条件 |

受控 JOIN 需要同步扩展：

- SQL 安全校验：允许白名单表集合和白名单 JOIN 键。
- 意图识别：返回多表 `route_plan` 而不是单一 `dataset_id`。
- AnswerFormatter：支持多源结果模板。
- ResultValidator：校验船舶数、事件数、分类合计、重复计数。

## 6. 网页与问数链路变更记录

### 2026-08-15：启动与基础配置

- 安装 `requirements.txt` 依赖。
- 启动 FastAPI 服务：`uvicorn app.main:app --host 127.0.0.1 --port 8000`。
- 将 `data/智能取数-实验数据-0805/` 下 6 张 Excel 导入 `data/smart_query.db`。
- 初始化 26 条海事业务术语，覆盖 VTS 区域、上行/下行、时间口径、吃水、违规、风险、状态、船型、锚地等。
- 调整数据集展示顺序，便于 demo 讲解。

### 2026-08-19：单表手选升级为自动意图识别

- 新增 `app/intent.py`：
  - 根据问题关键词、数据表名、字段名、术语库综合打分。
  - 自动识别 `dataset_id`、`intent_type`、候选数据表、置信度和路由原因。
  - 支持手动选择数据表覆盖自动识别结果。
- 新增 `app/answer.py`：
  - 定义回答模板、`AnswerPayload`、`TraceRecord` 和答案生成 Prompt。
  - 按“直接结论、统计范围、结果明细、统计口径、可追溯信息”五段式输出。
- 更新 `app/main.py`：
  - `/api/ask` 的 `dataset_id` 改为可选。
  - 自动路由成功后生成单表 SQL。
  - 自动路由不明确时返回 `need_clarification`，不硬猜数据表。
  - 返回 `route`、`answer_payload`、`trace_record`。
- 更新 `app/llm.py`：
  - SQL 生成 Prompt 带入系统识别出的意图、数据表和业务口径。
  - 答案生成 Prompt 改为基于结构化模板与追溯记录。
- 更新 `app/static/index.html`：
  - 首页文案改为“自动识别意图和数据表”。
  - 示例问题改为海事 demo 问题。
  - 数据表下拉框文案改为“数据表路由”。
- 更新 `app/static/app.js`：
  - 数据表下拉框默认值为“自动识别数据表”。
  - 问数请求允许 `dataset_id=null`。
  - 追溯面板展示识别意图、使用数据表、置信度、候选数据表、SQL 和查询结果。
- 更新 `app/static/style.css`：
  - 新增 Trace 面板和候选数据表样式。
- 新增 `tests/test_intent.py`：
  - 验证截面流量、吃水查询和手动选择覆盖自动路由。

### 2026-08-19：表域重叠与多表联合问数路径

- 梳理 6 张表的领域边界、字段重叠和真实键交集。
- 明确 `target_id/mmsi` 适合作为船舶实体关联键。
- 明确 `event_uuid` 不适合作为通用跨表 JOIN 键。
- 确定多表能力建议先走“多表并行查询 + 后端结构化合并”，再做“受控 JOIN”。
- 新增本文档，作为后续网页和问数链路改动的持续记录入口。

### 2026-08-21：融合多轮对话与 SQL 生成过程展示

- 融合同组成员的模型调用指标设计：
  - `chat()` 返回 `ChatResult`，记录模型接口耗时和 token 用量。
  - `generate_sql()` 要求模型返回 JSON，新增 `reasoning_summary` 作为可展示的 SQL 生成依据摘要。
- 保留自动意图识别链路：
  - `generate_sql()` 继续接收 `route_info`，将系统识别出的意图、数据表和业务口径注入 SQL 生成 Prompt。
  - `/api/ask` 继续支持 `dataset_id=null` 自动路由，也支持手动指定数据表覆盖自动路由。
- 更新 `/api/ask` 返回结构：
  - 保留 `route`、`answer_payload`、`trace_record`。
  - 新增 `reasoning_summary` 和 `metrics`，用于前端展示 SQL 生成过程、模型耗时、数据库查询耗时和 token 统计。
- 融合同组成员的前端多轮对话能力：
  - 新增浏览器本地对话列表、对话新建、切换、重命名和删除。
  - 每个对话保存所选数据表；为空时使用“自动识别数据表”。
  - 消息保存在 `localStorage`，模型配置仍保存在 `sessionStorage`。
- 更新问数详情面板：
  - 同时展示识别意图、使用数据表、置信度、候选表、追溯编号、SQL 生成依据、耗时/token、SQL 和查询结果。
