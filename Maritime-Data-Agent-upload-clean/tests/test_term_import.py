import io

import pandas as pd
from fastapi.testclient import TestClient

from app import db
from app.main import app


def excel_bytes(rows):
    output = io.BytesIO()
    pd.DataFrame(rows).to_excel(output, index=False)
    return output.getvalue()


def setup_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.save_dataset("ships-id", "船舶表", "ships_table", "ships.xlsx", 0, [])


def test_import_terms_and_skip_duplicates(tmp_path, monkeypatch):
    setup_database(tmp_path, monkeypatch)
    content = excel_bytes(
        [
            {"术语": "活跃船舶", "定义": "最近上报过位置的船舶", "同义词": "在线船舶", "关联数据表": "船舶表"},
            {"术语": "港口", "定义": "船舶停靠区域", "同义词": "码头", "关联数据表": "全局术语"},
            {"术语": "活跃船舶", "定义": "重复行", "同义词": "", "关联数据表": "船舶表"},
        ]
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/terms/import",
            files={"file": ("terms.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )

    assert response.status_code == 200
    assert response.json() == {"imported": 2, "skipped": 1, "total": 3}
    terms = db.list_terms()
    assert len(terms) == 2
    assert next(item for item in terms if item["term"] == "活跃船舶")["dataset_id"] == "ships-id"
    assert next(item for item in terms if item["term"] == "港口")["dataset_id"] is None


def test_import_is_atomic_when_dataset_is_unknown(tmp_path, monkeypatch):
    setup_database(tmp_path, monkeypatch)
    content = excel_bytes(
        [
            {"术语": "有效术语", "定义": "定义", "同义词": "", "关联数据表": ""},
            {"术语": "无效术语", "定义": "定义", "同义词": "", "关联数据表": "不存在的表"},
        ]
    )
    with TestClient(app) as client:
        response = client.post("/api/terms/import", files={"file": ("terms.xlsx", content)})

    assert response.status_code == 400
    assert "不存在" in response.json()["detail"]
    assert db.list_terms() == []
