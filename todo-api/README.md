# Todo API

FastAPI Todo CRUD API — 内存存储，用于验证 AI-Agent v4 Runtime。

## 启动

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

访问 http://localhost:8000/docs 查看 Swagger UI。

## API 端点

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/todos` | 创建 Todo |
| `GET` | `/todos` | 列出全部 |
| `GET` | `/todos/{id}` | 获取单个 |
| `PUT` | `/todos/{id}` | 更新（title + completed） |
| `DELETE` | `/todos/{id}` | 删除 |
| `GET` | `/health` | 健康检查 |

### 请求示例

```bash
# 创建
curl -X POST http://localhost:8000/todos \
  -H "Content-Type: application/json" \
  -d '{"title": "Buy milk"}'

# 列出
curl http://localhost:8000/todos

# 更新完成状态
curl -X PUT http://localhost:8000/todos/1 \
  -H "Content-Type: application/json" \
  -d '{"completed": true}'

# 删除
curl -X DELETE http://localhost:8000/todos/1
```

### 响应格式

```json
{
  "id": 1,
  "title": "Buy milk",
  "completed": false,
  "created_at": "2026-05-31T01:30:00"
}
```

## 测试

```bash
pytest test_main.py -v
```

覆盖：创建、查询、更新、删除、404、completed 状态更新、健康检查，共 12 个用例。

## 技术栈

Python 3.12 · FastAPI · Pydantic · pytest · uvicorn · 内存存储
