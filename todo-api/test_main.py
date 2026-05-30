"""pytest 测试 — 覆盖 CRUD + 404 + completed 状态更新。"""
import pytest
from fastapi.testclient import TestClient
from main import app, todos, _next_id

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_store():
    """每个测试前重置内存存储。"""
    global _next_id
    todos.clear()
    # _next_id 重置需要导入
    import main
    main._next_id = 1


class TestCreate:
    def test_create_todo(self):
        resp = client.post("/todos", json={"title": "Buy milk"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == 1
        assert data["title"] == "Buy milk"
        assert data["completed"] is False
        assert "created_at" in data

    def test_create_empty_title(self):
        resp = client.post("/todos", json={"title": ""})
        assert resp.status_code == 422


class TestRead:
    def test_list_empty(self):
        resp = client.get("/todos")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_items(self):
        client.post("/todos", json={"title": "A"})
        client.post("/todos", json={"title": "B"})
        resp = client.get("/todos")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_existing(self):
        client.post("/todos", json={"title": "Test"})
        resp = client.get("/todos/1")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Test"

    def test_get_404(self):
        resp = client.get("/todos/999")
        assert resp.status_code == 404


class TestUpdate:
    def test_update_title(self):
        client.post("/todos", json={"title": "Old"})
        resp = client.put("/todos/1", json={"title": "New"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "New"

    def test_update_completed(self):
        """关键测试：验证 completed 字段可以被更新。"""
        client.post("/todos", json={"title": "Test"})
        resp = client.put("/todos/1", json={"completed": True})
        assert resp.status_code == 200
        assert resp.json()["completed"] is True, (
            f"BUG: completed 应该为 True，实际为 {resp.json()['completed']}。"
            f"PATCH completed 更新逻辑未实现！"
        )

    def test_update_404(self):
        resp = client.put("/todos/999", json={"title": "X"})
        assert resp.status_code == 404


class TestDelete:
    def test_delete_existing(self):
        client.post("/todos", json={"title": "Test"})
        resp = client.delete("/todos/1")
        assert resp.status_code == 204

    def test_delete_404(self):
        resp = client.delete("/todos/999")
        assert resp.status_code == 404


class TestHealth:
    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
