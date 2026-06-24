# Feishu/Lark Bidirectional Integration

[中文](#中文) | [English](#english)

---

## 中文

本文记录 Local Agent Workbench 接入飞书开放平台应用的完整流程。目标链路是：

```text
飞书群消息 -> 事件订阅 URL -> Local Agent Workbench -> Agent 任务 -> 回填原飞书群聊
```

当前版本已完成并通过本地测试：

- URL verification challenge 自动返回。
- `im.message.receive_v1` 文本消息会创建 Agent 任务。
- 普通消息默认走 `manager_task`，由 Manager 调度。
- `/worker Alex ...`、`@Sophia ...`、`Elena: ...` 这类显式前缀会创建指定 Worker 任务。
- 同一个 `chat_id` 的最近飞书消息会作为上下文注入新任务，便于回答“结合上面”“刚才说的”等问题。
- 新任务会附带当天 Agent 任务摘要，便于回答“今天做了什么”“发一份今日进展”等问题。
- 新任务会附带本地 Git 工作区摘要，包括未提交文件、diff 统计和当天提交标题，便于根据真实文件改动写日报。
- 事件按 `event_id` 去重，飞书重试不会重复创建任务。
- 任务完成、失败或取消后，会把干净的结果正文回填到原 `chat_id`。

### 1. 启动后端

```bash
cd local-agent-workbench
python server.py
```

确认本地服务正常：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/integrations/feishu/status
```

给自动化 Agent 的最小健康检查顺序：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/agent/runtime
curl http://127.0.0.1:8000/integrations/feishu/status
```

### 2. 准备公网入口

飞书开放平台必须访问公网 HTTPS URL。开发环境可以用 Cloudflare Tunnel、ngrok、frp 或部署到公网服务器。

Cloudflare Tunnel 示例：

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

拿到公网域名后，事件订阅 URL 固定填写：

```text
https://<your-public-host>/integrations/feishu/events
```

不要把一次性的临时公网域名写进仓库；只在飞书后台配置和本地 `.env` 中使用。

### 3. 创建飞书开放平台应用

在飞书开放平台创建企业自建应用，然后记录：

- App ID
- App Secret
- Event Verification Token

把它们写入本地 `.env`：

```env
FEISHU_EVENT_VERIFICATION_TOKEN=replace-with-event-token
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=replace-with-app-secret
FEISHU_API_BASE_URL=https://open.feishu.cn/open-apis
FEISHU_DEFAULT_TASK_TYPE=manager_task
FEISHU_DEFAULT_WORKER=Elena
```

如果使用 Lark 国际版，可以把 API base URL 换成：

```env
FEISHU_API_BASE_URL=https://open.larksuite.com/open-apis
```

### 4. 配置事件订阅 URL

在应用后台打开“事件订阅”，把请求地址填成：

```text
https://<your-public-host>/integrations/feishu/events
```

点击保存或校验时，飞书会发送 `url_verification` 请求。后端会校验 token，并返回：

```json
{"challenge":"飞书传来的 challenge"}
```

如果 challenge 没有通过，优先检查：

- 公网 URL 是否能访问到本机 `8000` 端口。
- `.env` 中的 `FEISHU_EVENT_VERIFICATION_TOKEN` 是否和飞书后台一致。
- 后端是否已经重启并加载了最新 `.env`。
- 飞书事件是否开启了加密；当前版本尚未支持 `encrypt` payload。

### 5. 添加必要事件

最小必选事件：

```text
im.message.receive_v1
```

当前只处理文本消息。图片、文件、互动卡片、文档事件、审批事件等暂不处理。

### 6. 申请权限

最小推荐权限：

```text
im:message
im:message:send_as_bot
```

如果飞书后台要求填写申请理由，可以使用：

```text
本应用用于公司内部 Agent 工作台测试。需要接收群聊中的文本指令，将其转换为后台 Agent 任务，并在任务完成后由机器人把处理结果回填到原群聊。权限仅用于内部测试群消息的接收与机器人文本回复，不读取无关会话内容，不处理文件或敏感个人信息。
```

### 7. 发布应用并添加到群

在权限和事件配置完成后：

1. 发布或重新发布应用版本。
2. 在目标飞书群中添加该应用机器人。
3. 在群里发送一条文本消息测试。

普通消息会走 Manager：

```text
总结一下当前项目进展
```

指定 Worker：

```text
/worker Alex 检查后端测试是否通过
@Sophia review runtime/feishu_inbound.py
Elena: 写一段今天的项目日报
```

### 8. 验证状态

查看集成状态：

```bash
curl http://127.0.0.1:8000/integrations/feishu/status
```

关键字段：

- `inbound.enabled`: 是否配置了事件 token。
- `default_task_type`: 普通消息默认任务类型，当前推荐 `manager_task`。
- `app_reply_configured`: 是否配置了 App ID 和 App Secret，可回填原群聊。
- `webhook_reply_configured`: 是否配置了自定义机器人 webhook，可作为固定群通知兜底。
- `inbound.chat_history_chats`: 当前本地已记录上下文的飞书会话数量。
- `inbound.chat_history_messages`: 当前本地已记录的飞书上下文消息数量。

如果只想验证 challenge 逻辑，可以向本地服务发送一个模拟请求：

```bash
curl -X POST http://127.0.0.1:8000/integrations/feishu/events \
  -H "Content-Type: application/json" \
  -d '{"type":"url_verification","token":"replace-with-event-token","challenge":"local-test"}'
```

预期返回：

```json
{"challenge":"local-test"}
```

### 9. 运行测试

```bash
python -m pytest -q
```

当前预期：

```text
249 passed
```

飞书相关覆盖点包括：

- challenge 响应。
- token 校验。
- 文本消息解析。
- 同一 `chat_id` 的上文注入。
- 当天 Agent 任务摘要注入。
- 本地 Git 工作区变更摘要注入。
- Worker 前缀选择。
- app message 发送到 `chat_id`。
- 回填消息不再带 `[Agent Task Result]`、`任务ID` 等调试外壳。

### 10. 上下文边界

飞书事件处理会把最近收到的同群聊文本事件保存在本地 `feishu_events.json` 的 `chat_history` 中；该文件已被 `.gitignore` 忽略，不会提交到仓库。创建新 Agent 任务时，后端会读取同一个 `chat_id` 的最近若干条消息，并附加当天 `agent_tasks.json` 中的任务摘要。

同时，后端会对当前本地 Git 工作区做只读摘要：`git status --short`、`git diff --stat`、`git diff --cached --stat` 和当天 `git log --oneline`。这让机器人回答“今天本地改了什么”“汇总今天进展”时能看到文件级改动。为降低泄露风险，这里不会自动注入完整 diff 或文件内容。

这不是“读取完整飞书群历史”。只有飞书实际投递到 `/integrations/feishu/events` 的文本事件才会进入上下文。如果机器人没有收到普通群消息事件，或者应用权限/事件订阅没有覆盖某些消息，后端就无法知道那些“上面的聊天”。如需补齐历史消息，需要额外申请飞书消息历史相关权限，并接入主动拉取消息历史的 API。

### 11. 生产边界

当前实现适合内部测试和 demo。上线前建议补齐：

- `FEISHU_EVENT_ENCRYPT_KEY` 加密事件解密。
- 主动拉取飞书消息历史，用于机器人未在线或 webhook 未收到事件时补上下文。
- 更细的用户、群、租户白名单。
- 机器人回复频控和失败重试策略。
- 互动卡片和长结果分页。
- 审计日志和密钥托管。

---

## English

This guide documents the Feishu/Lark Open Platform app integration for Local Agent Workbench.

The target flow is:

```text
Feishu group message -> event subscription URL -> Local Agent Workbench -> Agent task -> reply to the source chat
```

Implemented behavior:

- URL verification challenge responses.
- `im.message.receive_v1` text events create Agent tasks.
- Normal messages default to `manager_task`.
- Explicit prefixes such as `/worker Alex ...`, `@Sophia ...`, or `Elena: ...` create Worker tasks.
- Recent messages from the same `chat_id` are injected into new tasks as Feishu chat context.
- New tasks include a same-day Agent task summary for requests such as "what did we do today?"
- New tasks include a read-only local Git workspace summary: changed files, diff stats, staged diff stats, and same-day commit titles.
- Event ids are persisted and deduplicated.
- Finished tasks reply to the original `chat_id` with clean task output.

### Setup Summary

1. Start the backend:

```bash
cd local-agent-workbench
python server.py
```

2. Expose it through a public HTTPS tunnel:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

3. Configure the Feishu/Lark event subscription URL:

```text
https://<your-public-host>/integrations/feishu/events
```

4. Fill local `.env`:

```env
FEISHU_EVENT_VERIFICATION_TOKEN=replace-with-event-token
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=replace-with-app-secret
FEISHU_API_BASE_URL=https://open.feishu.cn/open-apis
FEISHU_DEFAULT_TASK_TYPE=manager_task
FEISHU_DEFAULT_WORKER=Elena
```

5. Subscribe to the message event:

```text
im.message.receive_v1
```

6. Request the minimum permissions needed to receive group text messages and send bot replies:

```text
im:message
im:message:send_as_bot
```

7. Publish the app, add the bot to a group, and send a test message.

### Verification

```bash
curl http://127.0.0.1:8000/integrations/feishu/status
python -m pytest -q
```

Expected test result:

```text
249 passed
```

### Context Boundary

The inbound handler stores recent received text events per `chat_id` in local `feishu_events.json`; this file is ignored by git. When a new Agent task is created, the backend injects recent messages from the same chat plus a same-day summary from `agent_tasks.json`.

The backend also injects a read-only local Git workspace summary from `git status --short`, `git diff --stat`, `git diff --cached --stat`, and same-day `git log --oneline`. This helps Feishu requests such as "summarize today's local changes" produce reports based on actual file changes. Full diffs and file contents are not automatically injected.

This is not full Feishu group-history access. The backend can only remember messages that Feishu actually delivered to `/integrations/feishu/events`. If the app is not subscribed to ordinary group-message events, lacks permission, or was offline before a message arrived, that message will not be available as context. Full history recovery requires additional Feishu message-history permissions and an API fetch path.

### Current Limits

- Text messages only.
- Unencrypted event payloads only.
- Context is event-backed only; full group-history fetching is not implemented yet.
- No interactive cards or user permission mapping yet.
- App credentials are required for replies to the source chat; the custom bot webhook is only a fixed-group fallback.
