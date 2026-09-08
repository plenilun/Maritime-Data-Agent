from app.llm import extract_reasoning_summary, extract_response_json, extract_sql


def test_extracts_sql_from_plain_json():
    response = '{"reasoning_summary":"count rows","sql":"SELECT COUNT(*) FROM \\"sales_123\\";"}'
    assert extract_sql(response) == 'SELECT COUNT(*) FROM "sales_123"'


def test_extracts_sql_from_json_fence():
    response = '''```json
{"reasoning_summary":"count rows","sql":"SELECT COUNT(*) FROM \\"sales_123\\";"}
```'''
    assert extract_sql(response) == 'SELECT COUNT(*) FROM "sales_123"'


def test_extracts_sql_from_sql_fence():
    response = '''```sql
SELECT * FROM "sales_123" LIMIT 10;
```'''
    assert extract_sql(response) == 'SELECT * FROM "sales_123" LIMIT 10'


def test_extracts_json_after_provider_preamble():
    response = 'Here is the result:\n{"reasoning_summary":"list rows","sql":"SELECT * FROM \\"sales_123\\" LIMIT 10"}'
    assert extract_sql(response) == 'SELECT * FROM "sales_123" LIMIT 10'


def test_extracts_reasoning_summary_from_json_fence():
    response = '''```json
{"reasoning_summary":"按港口分组并统计记录数","sql":"SELECT port, COUNT(*) FROM sales_123 GROUP BY port"}
```'''
    assert extract_response_json(response)["reasoning_summary"] == "按港口分组并统计记录数"


def test_extracts_reasoning_summary_after_provider_preamble():
    response = '生成结果如下：\n{"reasoning_summary":"筛选上海港记录","sql":"SELECT * FROM sales_123 WHERE port = \\"上海\\""}'
    assert extract_response_json(response)["reasoning_summary"] == "筛选上海港记录"


def test_extracts_reasoning_summary_from_compatible_alias():
    response = '{"explanation":"按港口筛选记录","sql":"SELECT * FROM sales_123 WHERE port = \\"上海\\""}'
    assert extract_reasoning_summary(response, extract_sql(response)) == "按港口筛选记录"


def test_builds_reasoning_summary_when_model_returns_only_sql():
    sql = 'SELECT port, COUNT(*) FROM sales_123 WHERE status = "active" GROUP BY port ORDER BY COUNT(*) DESC LIMIT 10'
    summary = extract_reasoning_summary(sql, sql)
    assert "聚合统计" in summary
    assert "筛选" in summary
    assert "分组" in summary
    assert "排序" in summary
    assert "最多返回 10 条记录" in summary
