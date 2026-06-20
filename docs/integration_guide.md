# Business Integration Guide

[中文](#中文) | [English](#english)

---

## 中文

这份文档说明如何把 Local Agent Workbench 接入真实业务系统，例如企业知识库、飞书/Jira/GitLab、数据库、工单系统或公司内部 API。

核心原则：

> 不把业务系统硬编码进 Agent prompt，而是把外部能力封装成受控 tool adapter。

Agent Runtime 负责决策和调度；业务系统接入点放在工具层、配置层和服务端边界。

### 接入位置

| 文件 | 作用 |
|---|---|
| `runtime/tools.py` | 定义工具 schema，并实现工具执行逻辑 |
| `workers.json` | 给指定 Worker 开放工具权限 |
| `.env` | 存放业务系统 token、API key、base URL |
| `server.py` | 如果需要给前端暴露业务 API，可以在这里新增 route |
| `runtime/persistence.py` | 如果需要本地知识库或持久化状态，可以扩展这里 |

典型流程：

```text
define tool schema
-> implement adapter
-> add env config
-> grant worker permission
-> test with mocked external API
-> expose through task workflow
```

### 接企业知识库

适用场景：产品文档问答、内部 FAQ、接口文档检索、客服辅助回复、研发规范查询。

推荐架构：

```text
documents
-> chunk
-> embedding
-> vector store
-> search_knowledge(query)
-> Agent answer with retrieved context
```

可以先从最小版本开始：读取 `docs/` 下的 Markdown/Text 文件，做关键词检索或简单排序，返回相关片段。后续再升级为 Chroma、FAISS 或 pgvector。

注意：

- 回答应基于检索片段，不让模型自由编造。
- 返回结果最好包含来源文件、标题、片段位置。
- 私有文档是否发送给外部模型，需要遵守公司策略。

### 接飞书 / Jira / GitLab

推荐做成明确工具，而不是让 Agent 自己拼 API：

```text
feishu_create_doc(title, content)
jira_create_issue(title, description, assignee)
gitlab_get_merge_request(project_id, mr_id)
```

实现步骤：

1. 在 `.env` 配置外部系统 token。
2. 在 `runtime/tools.py` 增加工具 schema。
3. 在 `execute_tool` 中实现服务端 adapter。
4. 在 `workers.json` 中给指定 Worker 开权限。
5. 用 mock 外部 API 的方式补测试。

示例 schema：

```python
"feishu_create_doc": {
    "name": "feishu_create_doc",
    "description": "Create a Feishu document from a title and markdown content.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"}
        },
        "required": ["title", "content"]
    }
}
```

### 接数据库

数据库接入要保守。不要直接实现：

```text
execute_sql(sql)
```

推荐封装成业务语义工具：

```text
query_order(order_id)
get_customer_profile(customer_id)
list_recent_refunds(start_date, end_date)
search_product_by_sku(sku)
```

安全规则：

- 默认只读连接。
- 使用参数化查询。
- 表和字段白名单。
- 强制 `LIMIT`。
- 禁止 `UPDATE` / `DELETE` / `DROP` / `ALTER`。
- 高风险操作需要人工确认。
- 日志里不要打印完整敏感数据。

### 接公司内部 API

很多业务系统不需要数据库直连，只需要调用内部 HTTP API：

```text
get_ticket(ticket_id)
list_open_tickets(team_id)
create_support_reply(ticket_id, draft)
get_order_status(order_id)
```

推荐流程：

```text
Agent task
-> tool call
-> server-side adapter
-> internal API
-> sanitized result
-> task logs/result
```

要求：

- 不把内部 API token 暴露给前端。
- 不让 Agent 构造任意 URL。
- 每个工具固定 base URL 和 path。
- 校验输入，脱敏输出。

### 权限建议

| Worker 类型 | 推荐工具 |
|---|---|
| Research / 文档类 | `search_knowledge`, `fetch_url` |
| Reviewer / 审查类 | `read_file`, `search_code`, `search_knowledge` |
| Engineer / 开发类 | `read_file`, `write_file`, `run_command`, `search_code` |
| Ops / 运维类 | `run_command`, `read_file`, `find_files` |
| Writer / 文档类 | `read_file`, `search_knowledge`, `feishu_create_doc` |

权限原则：

> 默认不给，按任务需要最小授权。

### 测试建议

每接一个业务系统，至少补三类测试：

1. schema 测试：工具参数是否正确。
2. no-key 测试：缺少 API key 时是否返回明确错误。
3. adapter 测试：mock 外部 API，验证输入、输出、错误处理。

不要让测试调用真实公司系统。

---

## English

This document explains how to connect Local Agent Workbench to real business systems, such as internal knowledge bases, Feishu/Jira/GitLab, databases, ticketing systems, or company APIs.

Core principle:

> Do not hardcode business systems into prompts. Wrap external capabilities as controlled tool adapters.

### Integration Points

| File | Purpose |
|---|---|
| `runtime/tools.py` | Define tool schemas and implement execution logic |
| `workers.json` | Grant tool permissions to selected workers |
| `.env` | Store external tokens, API keys, and base URLs |
| `server.py` | Add routes only when the frontend needs extra business APIs |
| `runtime/persistence.py` | Extend local knowledge or persistence behavior |

Typical flow:

```text
define tool schema
-> implement adapter
-> add env config
-> grant worker permission
-> test with mocked external API
-> expose through task workflow
```

### Internal Knowledge Base

Recommended architecture:

```text
documents
-> chunk
-> embedding
-> vector store
-> search_knowledge(query)
-> Agent answer with retrieved context
```

Start small with Markdown/Text files and keyword search. Upgrade to Chroma, FAISS, or pgvector when needed.

### Feishu / Jira / GitLab

Prefer explicit tools:

```text
feishu_create_doc(title, content)
jira_create_issue(title, description, assignee)
gitlab_get_merge_request(project_id, mr_id)
```

Implementation steps:

1. Add provider config to `.env`.
2. Add a tool schema in `runtime/tools.py`.
3. Implement the server-side adapter in `execute_tool`.
4. Grant the tool to selected workers in `workers.json`.
5. Add mocked tests for success and failure cases.

### Databases

Avoid:

```text
execute_sql(sql)
```

Prefer business-level read tools:

```text
query_order(order_id)
get_customer_profile(customer_id)
list_recent_refunds(start_date, end_date)
search_product_by_sku(sku)
```

Safety rules:

- read-only connection by default
- parameterized queries
- table and column allowlists
- forced `LIMIT`
- no `UPDATE`, `DELETE`, `DROP`, or `ALTER`
- human approval for risky operations
- avoid logging sensitive data

### Internal Company APIs

Recommended flow:

```text
Agent task
-> tool call
-> server-side adapter
-> internal API
-> sanitized result
-> task logs/result
```

Rules:

- do not expose internal API tokens to the frontend
- do not let the agent construct arbitrary URLs
- keep fixed base URLs and paths per tool
- validate inputs
- sanitize outputs

### Permission Model

| Worker type | Suggested tools |
|---|---|
| Research / docs | `search_knowledge`, `fetch_url` |
| Reviewer | `read_file`, `search_code`, `search_knowledge` |
| Engineer | `read_file`, `write_file`, `run_command`, `search_code` |
| Ops | `run_command`, `read_file`, `find_files` |
| Writer | `read_file`, `search_knowledge`, `feishu_create_doc` |

Permission principle:

> Deny by default. Grant the minimum tools needed for the task.

### Testing

For each business integration, add at least:

1. schema tests
2. missing-key tests
3. mocked adapter tests

Tests should not call real company systems.
