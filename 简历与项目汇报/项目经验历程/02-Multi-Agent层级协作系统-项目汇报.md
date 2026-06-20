# Multi-Agent 层级协作系统 v4

**时间：** 2026.05.25 — 2026.05.31（v1 → v4 持续迭代）
**角色：** 独立架构设计与开发（GPT-5.5 辅助方案设计）
**状态：** v4 核心引擎已交付，持续迭代中

## 背景

需要一个能自动拆解复杂软件工程任务、并行分派给多个 AI Worker 执行、自动验证产物质量、失败自动重试、中断可恢复的 Agentic Workflow Runtime。v1-v3 阶段实现了"AI 公司组织模拟"（Manager 调度 → Worker 执行 → Deputy 复核），但关键缺陷是：任务依赖不强制执行、Worker 输出是自由文本、验证只评分不触发重做、无法从中断恢复。

## v4 核心升级

将系统内核从"Manager 手动调度 Agent" 升级为 **状态机驱动的 Agentic Workflow Runtime**：

- **TaskNode 状态机**（10 种状态，合法转移表约束）：todo → ready → running → verifying → done/retrying/failed/blocked/needs_replan
- **结构化合约**：`WorkerResult`（8 字段）+ `VerificationResult`（6 字段）dataclass，自由文本自动归一化，JSON 解析失败兜底
- **验证闭环**：`delegate_with_verification` —— Worker 执行 → Sophia 审查 ∥ Nathaniel 验证 → merged verdict → pass/done / needs_retry(重试) / reject(否决) / needs_replan(重规划)
- **DAG Pipeline 引擎**：Kahn 拓扑排序 + 依赖检查 + `_find_ready_nodes` + `_propagate_blocks` + ThreadPoolExecutor 并行执行就绪节点，每轮持久化
- **收敛模式**：`run_convergence_loop` —— 连续 N 轮无新增 blocking issues 自动停止
- **Budget/Policy 熔断**：max_attempts、max_model_calls、max_tool_calls 三层预算控制
- **WorkflowRun 持久化**：中断后从 JSON 恢复，残留 running 节点自动重置为 todo
- **5 个新 Manager 工具**：delegate_with_verification、run_project_pipeline、run_convergence_loop、show_workflow_status、request_replan

## 技术栈

Python 3.12 · dataclass · ThreadPoolExecutor · Kahn 算法 · JSON Schema 归一化 · unittest.mock · 4 厂商 LLM 路由（DeepSeek/千问/MiniMax/GPT 中转） · Anthropic SDK · OpenAI SDK

## 架构

```
User / Chat (Control Plane)
    ↓
Workflow Runtime (TaskNode 状态机 + DAG 调度 + 持久化)
    ↓
Agent Layer (Manager / 5 Workers / 2 Verifiers / Deputy)
    ↓
Tool Layer (read_file / write_file / run_command / fetch_url / search_code / ...)
```

## 成果

- 2261 → 3263 行（+1002 行 v4 代码）
- 0 → **77 个单元/集成测试**（6 个测试文件，1.171s 全部通过）
- 5 个 dataclass + 9 个纯函数 + 7 个核心引擎函数
- 100% 向后兼容：delegate_task 不变、run_worker 只增不减、evaluate_result 新增可选参数
- 覆盖：schema 归一化、状态机转移、DAG 拓扑/就绪/阻断、预算熔断、持久化往返、验证闭环 mock

---

## 踩坑与修复记录

### 1. `_propagate_blocks` 跳过 "todo" 节点导致阻断无法传播

**现象：** 测试 `test_propagate_blocks` 失败——A 节点 failed，依赖它的 B 节点（status="todo"）没有被标记为 blocked。

**根因：** 阻断传播函数中有一个保护性跳过逻辑：
```python
if status in ("done", "running", "retrying", "todo", "verifying"):
    continue
```
`"todo"` 被错误地放入了跳过集合。设计意图是跳过正在执行或已完成的节点，但 "todo" 正是需要被阻断传播的目标状态——当一个 todo 节点的上游 failed 时，它应该被标记为 blocked 以防止被 `_find_ready_nodes` 选中执行。

**修复：** 从跳过集合中移除 `"todo"`，改为：
```python
if status in ("done", "running", "retrying", "verifying"):
    continue
```

**经验教训：** 状态机传播逻辑中，"被动状态"（todo/ready）和"主动状态"（running/verifying）的处理策略不同。写这种传播函数时，应该先枚举"哪些状态不需要传播"而非"哪些状态需要跳过"，前者更容易做对。

### 2. DeepSeek v4-pro 响应中混入 ThinkingBlock 导致 `content[0].text` 崩溃

**现象：** API 连通性测试脚本中 `resp.content[0].text` 抛出 `AttributeError: 'ThinkingBlock' object has no attribute 'text'`。

**根因：** DeepSeek v4-pro 默认开启思维链（thinking），响应 content 数组中可能同时包含 `ThinkingBlock` 和 `TextBlock`。直接用索引 `[0]` 取第一个块恰好是 thinking 块而非文本块。

**修复：** 改为按类型过滤：
```python
texts = [b.text for b in resp.content if b.type == 'text']
```
`manager.py` 中的 `call_llm()` 函数已正确处理了 thinking 块（在工具调用循环中过滤），但测试脚本和外层调用未处理。需要在 `_normalize_worker_result` 和 `_normalize_verification_result` 的设计中假定 `raw_text` 可能包含 thinking 内容，归一化时只提取 JSON 或包装为 requires_review。

**经验教训：** 使用支持 thinking 的模型时，永远不要假设 `response.content[i].text` 存在。所有读取 `content` 的代码都应该先过滤 `type == "text"`。

### 3. MiniMax M2.7 是 verbose thinking 模型，短指令也输出思考块

**现象：** 向 MiniMax M2.7 发送 "Say OK in one word" → 返回 `"<think>The user says "Say OK". They request me to respond with..."` 而非简洁的 "OK"。

**根因：** MiniMax M2.7 是一个 reasoning 模型，默认在回复前生成思考链。OpenAI 兼容接口中，思考内容通过 `reasoning_content` 字段或直接混在 `content` 中以 `<think>` 标签形式返回。短指令（如 "Say OK"）触发了不必要的思考，且模型在思考完成后可能忘记原始指令继续详细回复。

**修复：** 在 `manager.py` 的 `openai_response_to_anthropic_blocks()` 中增加对 `reasoning_content` 的处理。在 `call_llm_multi_turn()` 中，如果模型持续输出思考而不产出工具调用，应增大 `max_tokens` 或切换到非 reasoning 模型。对于简单任务，`select_worker_model()` 应避免路由到 MiniMax。

**经验教训：** 多厂商路由表需要区分"reasoning 模型"和"指令跟随模型"。reasoning 模型适合复杂分析任务，但不适合"Say OK"类琐碎指令。路由策略应该根据任务特征选择模型类型，而不仅是厂商和成本。

### 4. Windows GBK 编码导致 Unicode 字符打印崩溃

**现象：** API 连通性测试脚本中，`print('✅')` 抛出 `UnicodeEncodeError: 'gbk' codec can't encode character '❌'`，导致整个测试结果未能输出。

**根因：** Windows 的 `subprocess` 默认使用 GBK 编码（`sys.stdout.encoding == 'gbk'`），而 Python 脚本中使用的 emoji（✅❌⚠️）不在 GBK 字符集中。虽然 `manager.py` 中所有文件 I/O 都显式指定了 `encoding="utf-8"`，但 `print()` 到终端时触发了隐式编码转换。

**修复：** 强制 stdout 使用 UTF-8：
```python
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
```
或在环境变量中设置 `PYTHONIOENCODING=utf-8`。`manager.py` 中已有的 `print_lock` 下打印中文没有问题是因为中文在 GBK 中，但 emoji 不在。

**经验教训：** Windows 环境下，任何可能输出非 ASCII 字符的脚本都应该在开头设置 stdout 编码。更彻底的方案是项目级别配置 `PYTHONIOENCODING=utf-8` 环境变量。

### 5. GPT 中转首次调用冷启动延迟 18 秒

**现象：** API 测试中 GPT-5.4 首次调用耗时 17906ms，后续调用降至 ~4s。

**根因：** 中转服务器（aigocode.com）在无请求时会将 worker 缩容或休眠，首次请求触发冷启动（模型加载 + worker 初始化）。这是中转架构的固有特征，非代码 bug。

**缓解策略：** 在 `select_worker_model()` 中，GPT 只在大标定（COMPLEXITY_SIGNALS_MAJOR 匹配）时使用。`manager.py` 的路由策略已正确实现：DeepSeek 打头阵，GPT 仅兜底。冷启动延迟在实际使用中影响有限，因为 GPT 调用的场景（重大决策）本身就需要用户等待。

**经验教训：** 中转 API 的成本优势伴随着冷启动延迟代价。设计路由策略时需要考虑不仅是价格，还有 P50/P99 延迟特征。可考虑在系统空闲时发送 keep-alive 请求，但性价比不高。

### 6. `_topological_sort` 的循环检测错误信息不够诊断友好

**现象：** 早期测试中 DAG 有环时，错误信息只列出了未排序的节点 ID 集合，没有指出哪些边构成了环。

**根因：** Kahn 算法检测到环的条件是 `len(result) != len(nodes)`，此时所有剩余节点的入度都 > 0，但由于算法不追踪路径，无法指出具体的环路径。

**当前处理：** 捕获 ValueError，以 warning 级别写入 `execution_log`，不阻断 pipeline 执行。这是一个务实的妥协——AI 生成的 pipeline 可能有轻微的结构问题（如多余的 depends_on 边），阻断执行代价太高。

**经验教训：** 算法错误信息设计需要区分"开发者调试"和"运行时处理"。对于运行时，知道"有环"并能降级处理就够了；对于开发者，需要环的具体构成。当前版本面向运行时，后续可增加调试模式输出环路径。

---

## v4.3 架构重构：深模块化（2026.06.20）

在 v4 功能交付后，进行了一轮纯架构手术——将 4126 行的单文件 `manager.py` 拆解为 13 个职责清晰的深模块，目标是让面试官 30 秒看懂架构图。

### 重构方案

```
重构前:  manager.py (4126 行, 75 函数, 所有逻辑混在一起)
           ↓
重构后:  manager.py (732 行, -82%)
         runtime/
           ├── contracts.py     (99 行)  数据合约：TaskNodeStatus + 5 dataclass
           ├── sanitize.py      (65 行)  消息清洗：跨 provider 的 thinking/tool 块过滤
           ├── pure_functions.py(267 行)  纯函数：归一化、合并、预算、产物发现
           ├── config.py        (120 行)  配置+懒加载 provider 初始化
           ├── routing.py       (134 行)  复杂度路由：关键词 → 模型选择
           ├── persistence.py   (304 行)  持久化：会话/看板/知识库/WorkflowRun
           ├── llm.py           (464 行)  多厂商 LLM 调用：DeepSeek/GPT/MiniMax/千问
           ├── tools.py         (558 行)  16 个工具执行 + ask_coworker 回调注入
           ├── workers.py       (323 行)  Worker 执行层：load/run_worker/run_deputy
           ├── verification.py  (255 行)  验证闭环：Sophia∥Nathaniel → merge → retry
           ├── pipeline.py      (645 行)  DAG 引擎：Kahn 排序/就绪发现/阻断传播/并发执行
           └── manager_tools.py (354 行)  24 个 Manager 工具 schema 生成
```

### 关键设计决策

| 问题 | 方案 | 效果 |
|------|------|------|
| 循环依赖 (tools ↔ workers) | 回调注入 `set_coworker_executor(fn)` | 零反向依赖 |
| runtime → manager 反向引用 | 参数注入 `execute_manager_tool_fn` | runtime 永远不 import manager |
| `import manager` 缺密钥就崩 | 懒加载 `_init_providers()`，首次调用才建客户端 | import 永远不崩 |
| 测试 import 路径全部失效 | manager.py 兼容 re-export：`from runtime.xxx import func` | 174 测试断言零改动 |
| tools.py 预估 970 行偏大 | 拆出 manager_tools.py（354 行）独立管理工具 schema | tools.py 558 行，职责聚焦 |

### 依赖方向（严格单向）

```
manager.py  ──imports──→  runtime/*
runtime/*   ──×──→  manager.py   (zero)
```

模块内部按依赖层次排列：contracts → sanitize → pure_functions → config → routing → persistence → llm → tools → workers → verification → pipeline → manager_tools。

### 重构原则

- **不新增功能，不删除功能**：纯结构变换，行为 100% 保持
- **搬运顺序严格**：从底层无依赖模块开始，逐层向上
- **每步验证**：搬一个模块跑一次全量测试，确保 174 测试始终绿色
- **Git 基线铁律**：每次改动前先 commit，绝不等用户提醒

### 踩坑与修复（重构过程）

**1. `from X import Y` 绑定语义导致测试 patch 失效**
- 现象：12 个测试报错，patch `manager.run_worker` 无效
- 根因：Python `from X import Y` 在当前模块创建了 Y 的引用副本。即使 patch 了 X.Y，当前模块的 Y 仍然指向旧对象。函数迁移到 runtime 后，测试需要 patch 消费者模块而非定义模块
- 修复：将 patch target 从 `manager.run_worker` 改为 `runtime.verification.run_worker`（消费者）

**2. 手术刀误切——持久化 import 块随 tools 段一起删除**
- 现象：`project_setup` 函数和 persistence 相关 import 在删除 tools 段时被连带移除
- 修复：逐个还原被误删的 import 块和函数

**3. ThreadPoolExecutor import 被误判为"未使用"**
- 现象：`roundtable_discuss` 和 `main()` 中并行工具执行依赖 `ThreadPoolExecutor` 和 `as_completed`
- 修复：补回 `from concurrent.futures import ThreadPoolExecutor, as_completed`

### 量化对比

| 指标 | 重构前 (v4) | 重构后 (v4.3) |
|------|------------|--------------|
| manager.py 行数 | 2261 → 3263 (+v4) → 4126 | **732** |
| 模块数 | 1 | **14**（1 manager + 13 runtime） |
| 最大单文件 | 4126 行 | 645 行（pipeline.py） |
| 循环依赖 | 2 处 lazy import 绕过 | **0** |
| 测试通过 | 77 | **174**（零断言改动） |
| import 副作用 | 缺密钥即崩 | **懒加载，永无副作用** |

### 面试可讲的点

- **深模块设计**：每个模块有清晰的"做什么、依赖谁、被谁依赖"，面试官可以 30 秒看懂架构
- **回调注入解耦**：不用 DI 框架，用 3 行代码的函数注入解决 tools ↔ workers 循环依赖
- **懒加载 Provider**：`import manager` 不需要任何 API 密钥，首次调用才初始化客户端
- **兼容 re-export**：重构 4000 行代码，174 个测试的 import 路径一个不用改
- **Python 模块绑定语义**：深刻理解 `from X import Y` 的内存模型，patch 测试要 patch 消费者而非定义者

---

## 后续迭代方向（v5+）

- Human Control Plane（Web Dashboard + 暂停/恢复/审批）
- Workspace 隔离（git worktree 防止 Worker 竞态）
- 对抗验证器（Adversarial Verifier 专责反驳）
- LLM 语义判断复杂度（替代关键词匹配）
- Token 精确统计（替代估算）

---

## v4 实战验证：Todo API 项目

**时间：** 2026.05.31
**目的：** 用一个完整的 FastAPI Todo CRUD 项目端到端测试 v4 Runtime 的全部核心能力。

### 验证结果

| 能力 | 状态 | 说明 |
|------|------|------|
| DAG Pipeline | ✅ | project_setup 生成 5 个节点（设计→实现→测试→文档→审查），含依赖关系 |
| 阻塞传播 | ✅ | 设计审查节点因 thinking block 报错 failed → 4 个下游节点全部 blocked |
| Resume 恢复 | ✅ | 中断的 running 节点被重置为 todo，done 节点保持不变 |
| Retry 闭环 | ⚠️ | 概念验证通过（测试 12→11 失败→修复→12 通过），但 LLM 驱动 retry 受 #7 阻碍 |
| 结构化合约 | ✅ | WorkerResult + VerificationResult 归一化 77 个测试覆盖 |

### 测试数据

- 故意 bug：`main.py` 中 completed 字段更新逻辑被注释 → `test_update_completed` 失败
- 修复：取消注释 → 12/12 测试通过
- Resume：模拟中断 WorkflowRun（step2 running）→ 恢复后重置为 todo

### 踩坑与修复记录（续）

### 7. DeepSeek thinking block 在多轮 Worker 会话中回传失败

**现象：** v4 Pipeline 执行时，Sophia Worker 读取文件成功后，第二轮 API 调用抛出 `Error 400: The content[].thinking in the thinking mode must be passed back to the API`。清除 worker_sessions.json 重试后仍然复现。

**根因：** DeepSeek v4-pro 的 thinking mode 要求 conversation history 中的 thinking block 必须完整保留 `thinking` 和 `signature` 字段。当 Worker 通过 `call_llm_multi_turn` 进行多轮工具调用时，第一轮产生的 thinking block 被追加到 messages（`call_llm_multi_turn` 第 549 行），第二轮 API 调用时这些 blocks 被回传。但如果中间经历了 JSON 序列化/反序列化（worker_sessions 持久化），或 thinking block 在 assistant_content 数组中的位置不符合 API 预期，DeepSeek 会拒绝请求。

**当前缓解：** 每次 Pipeline 执行前清理 worker_sessions.json。`call_llm_multi_turn` 已正确处理 thinking block 的追加（line 548-549）。根本修复需要：① 在 `load_sessions` 中验证并修复历史 thinking blocks；② 或者为不需要 thinking 的简单任务禁用 thinking mode（设置 `thinking={"type": "disabled"}`）。

**经验教训：** reasoning 模型的 conversation state 管理比普通模型复杂得多。thinking block 不是可选的元数据，而是 API 协议的一部分。任何持久化/恢复机制都必须将这些 blocks 视为不可变的状态，类似于区块链中的交易记录。

### 8. project_setup 硬编码 model 参数传递 tuple 而非 string

**现象：** `project_setup()` 调用 `client.messages.create(model=MANAGER_COMPLEX_MODEL)`，`MANAGER_COMPLEX_MODEL = ("deepseek", "deepseek-v4-pro[1M]")` 是一个 tuple，导致 DeepSeek API 返回 `model: invalid type: sequence, expected a string`。

**根因：** v3 重构 model tier 为 (provider, model_id) tuple 格式时，`call_llm()` 和 `call_llm_multi_turn()` 都更新了参数解构，但 `project_setup()` 使用 `client.messages.create()` 直连，绕过了 `call_llm()` 的路由层。

**修复：** 改为 `model=MANAGER_COMPLEX_MODEL[1]`。更彻底的方案是将 `project_setup()` 也迁移到 `call_llm()` 统一接口。

**经验教训：** 统一抽象层（`call_llm()`）的价值在于防止这种局部不兼容。任何绕过抽象层的直接 API 调用都是技术债务。
