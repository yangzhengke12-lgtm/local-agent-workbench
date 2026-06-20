# Python 输入解析反模式：用字符检测区分 int/float

## 反模式

```python
# ❌ 脆弱：用 "." 存在与否区分 int 和 float
if "." in user_input:
    return float(user_input)
else:
    return int(user_input)
```

## 问题

1. **科学计数法被拒绝**：`"1e5"` 不含 `.`，走 `int()` 路径触发 `ValueError`
2. **逻辑不完整**：`"inf"`、`"nan"`、带下划线 `"1_000"` 等合法字面量依赖巧合通过
3. **维护风险**：依赖 Python 解析器实现细节，不够声明式

## 推荐方案

```python
# ✅ 让 Python 解析器做判断，再根据语义收窄类型
def parse_number(s: str) -> int | float:
    value = float(s)
    if value.is_integer() and "e" not in s.lower() and "." not in s:
        return int(value)
    return value
```

## 适用场景

- 所有需要从用户输入/配置文件/API 解析数值并保留整数类型的场景
- CLI 工具、计算器、表单解析等

## 记录日期

2025-07-14 — 发现于 calculator.py 审查
