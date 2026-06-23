# Business Connectors

[中文](#中文) | [English](#english)

---

## 中文

这份文档说明当前仓库中已经落地的最小业务连接器。它不是“真实公司系统”的硬编码，而是一个可运行的本地 demo，用来展示如何把 Agent 接入数据库、内部 API 和飞书群通知。

### 已实现连接器

| Tool | 能力 | 默认数据源 | 安全边界 |
|---|---|---|---|
| `database_query` | 查询业务数据 | `examples/demo_business.db` | 只允许 `SELECT/WITH`，禁止写入和管理语句，最多返回 50 行 |
| `internal_api_request` | 请求内部 API | `examples/internal_api_demo.json` | 只允许 `GET` 和白名单路径，不让 Agent 构造任意 URL |
| `feishu_send_message` | 发送飞书/飞书国际版群通知 | `.env` 中的 `FEISHU_WEBHOOK_URL` | 只能发到后端配置好的自定义机器人 webhook，Agent 不能传任意 webhook |

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

### Feishu Custom Bot

方案一采用飞书群“自定义机器人” webhook，只做出站通知，不接收飞书消息。它适合演示：

```text
Agent 任务完成 -> 总结结果 -> 推送到飞书群
```

配置步骤：

1. 在飞书群里添加自定义机器人。
2. 复制机器人 webhook 到本地 `.env`。
3. 如果机器人开启了签名校验，把密钥填到 `FEISHU_WEBHOOK_SECRET`。
4. 重启后端或桌面端。

```env
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/replace-with-token
FEISHU_WEBHOOK_SECRET=replace-with-signing-secret
```

示例任务：

```text
请让 Elena 总结今天的任务结果，并发送到飞书群。
```

对应工具调用可以是：

```json
{
  "name": "feishu_send_message",
  "input": {
    "title": "Agent 任务完成",
    "text": "工单 ticket_9001 已分析完成，建议先处理支付回调失败。"
  }
}
```

当前版本只支持文本消息。飞书文档、互动卡片、接收 @机器人消息属于方案二，需要接入飞书开放平台应用和事件订阅。

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
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/replace-with-token
FEISHU_WEBHOOK_SECRET=replace-with-signing-secret
```

### 面试表达

可以这样讲：

> 当前版本已经有最小业务连接器：一个只读 SQLite 数据库查询工具、一个内部 API 白名单请求工具，以及一个飞书自定义机器人通知工具。它们默认使用本地 demo 数据或本地 `.env` 配置，不依赖真实公司系统；生产里替换 adapter 和密钥来源即可接入飞书、工单、订单、客户系统或内部知识库。

---

## English

This document describes the minimal business connectors implemented in this repository. They are not hardcoded company integrations; they are runnable local demos that show how the agent runtime can connect to databases, internal APIs, and Feishu/Lark group notifications.

### Implemented Connectors

| Tool | Capability | Default data source | Safety boundary |
|---|---|---|---|
| `database_query` | Query business data | `examples/demo_business.db` | SELECT/WITH only, no write/admin SQL, max 50 rows |
| `internal_api_request` | Request internal API data | `examples/internal_api_demo.json` | GET-only allowlisted paths, no arbitrary URLs |
| `feishu_send_message` | Send Feishu/Lark group notifications | `FEISHU_WEBHOOK_URL` in `.env` | Sends only to the backend-configured custom bot webhook; agents cannot provide arbitrary webhooks |

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

### Feishu/Lark Custom Bot

The first Feishu integration uses a group custom bot webhook for outbound notifications only. It is designed for demos such as:

```text
agent task completed -> summarize result -> push to a Feishu group
```

Setup:

1. Add a custom bot to a Feishu/Lark group.
2. Copy the webhook into local `.env`.
3. If signing is enabled, fill `FEISHU_WEBHOOK_SECRET`.
4. Restart the backend or desktop app.

```env
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/replace-with-token
FEISHU_WEBHOOK_SECRET=replace-with-signing-secret
```

Possible tool call:

```json
{
  "name": "feishu_send_message",
  "input": {
    "title": "Agent Task Completed",
    "text": "Ticket ticket_9001 has been analyzed. The recommended next action is to fix payment callback retries."
  }
}
```

This version supports text messages only. Feishu docs, interactive cards, or inbound @bot messages are phase-two work and require a Feishu Open Platform app plus event subscriptions.

### Production Integration

For real company systems, do not let agents generate arbitrary SQL or arbitrary URLs. Use server-side adapters:

1. Store database credentials and API tokens in `.env` or the company secret manager.
2. Replace adapters in `runtime/business_connectors.py`.
3. Keep table/field/path allowlists.
4. Add human approval for risky tools.
5. Use mocks in tests instead of calling production systems.
