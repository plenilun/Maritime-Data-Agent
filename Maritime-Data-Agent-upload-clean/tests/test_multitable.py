from app.multitable import build_multitable_context, select_terms_for_context


def dataset(dataset_id, table_name, columns):
    return {
        "id": dataset_id,
        "name": table_name,
        "table_name": table_name,
        "source_file": f"{table_name}.xlsx",
        "columns": [{"name": name, "type": "TEXT"} for name in columns],
    }


def test_builds_context_for_violation_with_realtime_draught_filter():
    violation = dataset("violation_record", "violation_record", ["violation_uuid", "target_id", "violation_time"])
    realtime = dataset("data_real_time", "data_real_time", ["target_id", "mmsi", "draught", "update_time"])
    route_info = {
        "dataset_id": "violation_record",
        "table_name": "violation_record",
        "candidates": [
            {"dataset_id": "violation_record", "score": 12},
            {"dataset_id": "data_real_time", "score": 5},
        ],
    }
    terms = [
        {
            "term": "吃水",
            "definition": "吃水字段是 draught",
            "synonyms": "draught",
            "dataset_id": "data_real_time",
        }
    ]

    context = build_multitable_context("今天吃水超过 8 米的违规船有多少", [violation, realtime], terms, route_info)

    assert context["enabled"] is True
    assert context["allowed_tables"] == ["violation_record", "data_real_time"]
    assert context["join_plan"][0]["left_key"] == "target_id"
    assert context["join_plan"][0]["right_key"] == "target_id"


def test_keeps_single_table_when_no_cross_table_need():
    violation = dataset("violation_record", "violation_record", ["violation_uuid", "target_id", "violation_time"])
    realtime = dataset("data_real_time", "data_real_time", ["target_id", "mmsi", "draught", "update_time"])
    route_info = {
        "dataset_id": "violation_record",
        "table_name": "violation_record",
        "candidates": [{"dataset_id": "violation_record", "score": 12}],
    }

    context = build_multitable_context("今天违规事件有多少", [violation, realtime], [], route_info)

    assert context["enabled"] is False
    assert context["allowed_tables"] == ["violation_record"]


def test_select_terms_for_context_includes_selected_dataset_terms():
    terms = [
        {"term": "违规事件", "definition": "违规记录", "synonyms": "违规", "dataset_id": "violation_record"},
        {"term": "吃水", "definition": "吃水字段是 draught", "synonyms": "draught", "dataset_id": "data_real_time"},
        {"term": "无关术语", "definition": "无关", "synonyms": "", "dataset_id": "risk_record"},
    ]

    selected = select_terms_for_context(terms, {"violation_record", "data_real_time"}, "违规船吃水超过 8 米")

    assert [item["term"] for item in selected] == ["违规事件", "吃水"]
