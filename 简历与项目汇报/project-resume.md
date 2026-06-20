# Multi-Agent 层级协作系统

**一句话：** 从单个 AI Agent 开始，逐步搭建成一个 7 智能体的工程团队模拟系统。

## 技术栈

Python 3.12 · Anthropic SDK · OpenAI SDK · ThreadPoolExecutor · Kahn 算法 · dataclass · JSON 驱动配置 · 4 厂商 LLM 路由（DeepSeek/千问/MiniMax/GPT） · 深模块架构（14 文件，零循环依赖）

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

| 指标 | v3 | v4 | v4.3 |
|------|-----|-----|------|
| 智能体 | 7 | 9（+2 Verifier） | 9 |
| 工具 | 26 | 31（+5 v4） | 31 |
| 代码量 | 900+ 行 | 4126 行 | 732 行 manager + ~3500 行 runtime（14 模块） |
| 配置文件驱动 | 100% | 100% | 100% |
| 测试覆盖 | 无 | 77 | **174**（零断言改动） |
| 循环依赖 | 2 处 lazy import | 2 处 lazy import | **0** |
| 最大单文件 | 2261 行 | 4126 行 | 645 行（pipeline.py） |

## 面试可讲的点

- **深模块架构**：把 4126 行巨石拆成 14 个职责清晰的文件，依赖方向严格单向，零循环依赖。面试官 30 秒看懂架构
- **回调注入解耦**：不用 DI 框架，3 行函数注入解决 tools ↔ workers 循环依赖
- **懒加载 Provider**：`import manager` 不需要任何 API 密钥，首次调用才初始化
- **兼容性重导出**：重构 4000 行代码，174 个测试的 import 一行不改全部通过
- **权限隔离的双保险**：API 层 tools 白名单 + 运行时二次校验，不是 prompt 软约束
- **单点故障修复**：自研副经理机制，独立分析 + 明确反对 + 替代方案
- **跨平台编码问题**：Windows GBK → subprocess stdout 崩为 None，独立排查修复
- **AI 辅助开发的边界**：架构设计、安全策略、踩坑修复是我做的；boilerplate 由 AI 生成
