# Multi-Agent 层级协作系统

**一句话：** 从单个 AI Agent 开始，逐步搭建成一个 7 智能体的工程团队模拟系统。

## 技术栈

Python · Anthropic SDK · ThreadPoolExecutor · JSON 驱动配置 · Subprocess 管理

## 搭建过程

**起点 — 单 Agent**
从调用 DeepSeek API 的对话机器人起步，只有 2 个工具（获取时间、读文件）。跑通后发现了三个硬伤：上下文混乱、权限不可控、无法并行。

**迭代 — 加 Manager + 5 Worker**
引入"管理者 - 员工"分层架构。Manager 负责拆解任务、指派员工、审核结果；5 个 Worker 各有独立系统提示和工具权限。用 workers.json 做零代码配置，ThreadPoolExecutor 实现并行调度。

**踩坑 — 三个独立排查修复的问题**
1. Windows 下 find 命令返回空 → 定位到 subprocess 用 GBK 解码非 GBK 字节导致 stdout 崩为 None → 改为 `encoding="utf-8", errors="replace"`
2. Thinking 模式 API 报 400 → 模型返回的 thinking 块未被保留回传 → 在消息历史中补充 thinking 块处理
3. 工具输出撑爆上下文 → 自研截断保护，每个工具硬上限 6000 字符，搜索上限 50 条

**深度 — 从"派活工具"到"工程组织"**
加任务看板（创建/分配/状态流转）、圆桌讨论（两轮交叉回应）、共享知识库、三维绩效评分、会话持久化。发现 Manager 单点故障后引入副经理制衡机制。最终覆盖 GitHub 集成、文档转换、流程模板沉淀。

## 量化

| 指标 | 数值 |
|------|------|
| 智能体 | 7（1 正经理 + 1 副经理 + 5 Worker） |
| 工具 | 13 Worker 工具 + 13 管理工具 |
| 代码量 | 900+ 行 Python |
| 配置文件驱动 | 100%（增删员工、调权限不用改代码） |

## 面试可讲的点

- **权限隔离的双保险**：API 层 tools 白名单 + 运行时二次校验，不是 prompt 软约束
- **单点故障修复**：自研副经理机制，独立分析 + 明确反对 + 替代方案
- **跨平台编码问题**：Windows GBK → subprocess stdout 崩为 None，独立排查修复
- **AI 辅助开发的边界**：架构设计、安全策略、踩坑修复是我做的；boilerplate 由 AI 生成
