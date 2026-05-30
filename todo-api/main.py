"""FastAPI Todo API — 内存存储，完整 CRUD。

注意：PATCH completed 字段更新逻辑故意留空，
用于验证 v4 Runtime 的 verifier → retry 闭环。
"""
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Todo API", version="0.1.0")

# ── 内存存储 ──
todos: dict[int, dict] = {}
_next_id: int = 1


class TodoCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class TodoUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    completed: bool | None = None


class TodoResponse(BaseModel):
    id: int
    title: str
    completed: bool
    created_at: str


# ── CRUD ──


@app.post("/todos", response_model=TodoResponse, status_code=201)
def create_todo(body: TodoCreate):
    """创建新 Todo。"""
    global _next_id
    todo = {
        "id": _next_id,
        "title": body.title,
        "completed": False,
        "created_at": datetime.now().isoformat(),
    }
    todos[_next_id] = todo
    _next_id += 1
    return todo


@app.get("/todos", response_model=list[TodoResponse])
def list_todos():
    """列出所有 Todo。"""
    return list(todos.values())


@app.get("/todos/{todo_id}", response_model=TodoResponse)
def get_todo(todo_id: int):
    """获取单个 Todo。"""
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")
    return todos[todo_id]


@app.put("/todos/{todo_id}", response_model=TodoResponse)
def update_todo(todo_id: int, body: TodoUpdate):
    """全量更新 Todo。"""
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")
    todo = todos[todo_id]
    if body.title is not None:
        todo["title"] = body.title
    if body.completed is not None:
        todo["completed"] = body.completed
    return todo


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int):
    """删除 Todo。"""
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")
    del todos[todo_id]


@app.get("/health")
def health():
    return {"status": "ok"}
