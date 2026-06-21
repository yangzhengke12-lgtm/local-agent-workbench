# Business Connectors

[中文](#中文) | [English](#english)

---

## 中文

这份文档说明当前仓库中已经落地的最小业务连接器。它不是“真实公司系统”的硬编码，而是一个可运行的本地 demo，用来展示如何把 Agent 接入数据库和内部 API。

### 已实现连接器

| Tool | 能力 | 默认数据源 | 安全边界 |
|---|---|---|---|
| `database_query` | 查询业务数据 | `examples/demo_business.db` | 只允许 `SELECT/WITH`，禁止写入和管理语句，最多返回 50 行 |
| `internal_api_request` | 请求内部 API | `examples/internal_api_demo.json` | 只允许 `GET` 和白名单路径，不让 Agent 构造任意 URL |

### SQLite Demo

首次查询时会根据 `examples/demo_business.sql` 自动创建 `examples/demo_business.db`。

示例任务：

```text
请让 Sophia 查询当前 open 的 high priority 工单，并总结应该先处理什么。
```

对应工具调用可以是：

```json
{
  "name": "database_query",
  "input": {
    "query": "SELECT ticket_id, priority, status, title FROM tickets WHERE status = 'open'",
    "max_rows": 10
  }
}
```

### Internal API Demo

如果没有配置 `INTERNAL_API_BASE_URL`，工具会使用本地 mock 文件 `examples/internal_api_demo.json`。可用路径：

```text
/tickets/ticket_9001
/tickets/ticket_9002
/orders/ord_1001
/orders/ord_1003
/customers/cust_001
/customers/cust_003
/metrics/daily
```

示例：

```json
{
  "name": "internal_api_request",
  "input": {
    "path": "/tickets/ticket_9001"
  }
}
```

### 接真实公司系统

生产接入时不要让 Agent 直接拼 SQL 或任意 URL。推荐做法：

1. 把真实数据库连接和 API token 放在 `.env` 或公司密钥系统。
2. 在 `runtime/business_connectors.py` 中替换 adapter。
3. 保留表/字段/路径白名单。
4. 对高风险工具增加人工审批。
5. 测试中使用 mock，不调用真实公司系统。

示例环境变量：

```env
INTERNAL_API_BASE_URL=https://internal.example.com
INTERNAL_API_TOKEN=replace-with-company-secret
```

### 面试表达

可以这样讲：

> 当前版本已经有最小业务连接器：一个只读 SQLite 数据库查询工具和一个内部 API 白名单请求工具。它们默认使用本地 demo 数据，不依赖真实公司系统；生产里替换 adapter 和密钥来源即可接入飞书、工单、订单、客户系统或内部知识库。

---

## English

This document describes the minimal business connectors implemented in this repository. They are not hardcoded company integrations; they are runnable local demos that show how the agent runtime can connect to databases and internal APIs.

### Implemented Connectors

| Tool | Capability | Default data source | Safety boundary |
|---|---|---|---|
| `database_query` | Query business data | `examples/demo_business.db` | SELECT/WITH only, no write/admin SQL, max 50 rows |
| `internal_api_request` | Request internal API data | `examples/internal_api_demo.json` | GET-only allowlisted paths, no arbitrary URLs |

### SQLite Demo

On first use, `examples/demo_business.db` is created from `examples/demo_business.sql`.

Example task:

```text
Ask Sophia to query open high-priority tickets and summarize what should be handled first.
```

Possible tool call:

```json
{
  "name": "database_query",
  "input": {
    "query": "SELECT ticket_id, priority, status, title FROM tickets WHERE status = 'open'",
    "max_rows": 10
  }
}
```

### Internal API Demo

If `INTERNAL_API_BASE_URL` is not configured, the tool uses `examples/internal_api_demo.json`. Available paths:

```text
/tickets/ticket_9001
/tickets/ticket_9002
/orders/ord_1001
/orders/ord_1003
/customers/cust_001
/customers/cust_003
/metrics/daily
```

### Production Integration

For real company systems, do not let agents generate arbitrary SQL or arbitrary URLs. Use server-side adapters:

1. Store database credentials and API tokens in `.env` or the company secret manager.
2. Replace adapters in `runtime/business_connectors.py`.
3. Keep table/field/path allowlists.
4. Add human approval for risky tools.
5. Use mocks in tests instead of calling production systems.
