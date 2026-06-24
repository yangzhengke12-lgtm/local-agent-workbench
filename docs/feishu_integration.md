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
- 事件按 `event_id` 去重，飞书重试不会重复创建任务。
- 任务完成、失败或取消后，会把干净的结果正文回填到原 `chat_id`。

### 1. 启动后端

```bash
cd ai-agent-system
python server.py
```

确认本地服务正常：

```bash
curl http://127.0.0.1:8000/health
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

### 9. 运行测试

```bash
python -m pytest -q
```

当前预期：

```text
245 passed
```

飞书相关覆盖点包括：

- challenge 响应。
- token 校验。
- 文本消息解析。
- Worker 前缀选择。
- app message 发送到 `chat_id`。
- 回填消息不再带 `[Agent Task Result]`、`任务ID` 等调试外壳。

### 10. 生产边界

当前实现适合内部测试和 demo。上线前建议补齐：

- `FEISHU_EVENT_ENCRYPT_KEY` 加密事件解密。
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
- Event ids are persisted and deduplicated.
- Finished tasks reply to the original `chat_id` with clean task output.

### Setup Summary

1. Start the backend:

```bash
cd ai-agent-system
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
245 passed
```

### Current Limits

- Text messages only.
- Unencrypted event payloads only.
- No interactive cards or user permission mapping yet.
- App credentials are required for replies to the source chat; the custom bot webhook is only a fixed-group fallback.
