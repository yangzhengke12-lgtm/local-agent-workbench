# MCP 视觉模型接入

**时间：** 2026.05  
**角色：** 独立完成  
**状态：** 已完结

## 背景
DeepSeek v4 Pro 作为语言模型原生不支持多模态识图。为扩展 Agent 的视觉理解能力，需引入外部视觉服务。

## 工作内容
- 为 Claude Code 环境配置并验证了 9 个 MCP 服务器（qwen-vision、minimax-vision、github、filesystem、memory、fetch、sequential-thinking、context7、ppt-mcp）
- 重点接入千问视觉（Qwen-Vision）和 MiniMax Vision 两个 MCP 视觉工具，实现通过标准 MCP 协议调用外部视觉模型分析本地图片
- 验证了 MCP 工具与模型原生多模态能力的边界：MCP 视觉依赖磁盘文件路径，嵌入对话的图片需模型自身多模态支持，二者互补
- 发现并验证了 MCP 服务器的 allowed_directories 作用域限制机制

## 技术栈
MCP 协议 · MiniMax API · Qwen Vision API · 多模型编排 · Agent 工具链

## 成果
9 个 MCP 服务器全部正常连接并可用，Agent 获得本地图片识别能力，为后续多模型协作项目打下工具链基础。
