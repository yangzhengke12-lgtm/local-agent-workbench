# FundVision 基金可视化看板

**时间：** 2026.05
**角色：** Multi-Agent 层级协作系统辅助开发 + 人工调试
**状态：** 已完结

## 背景
需要一个个人基金持仓管理工具，能够实时追踪基金净值、计算盈亏、按类型分析资产分布。同时以此项目作为 Multi-Agent 层级协作系统的首个真实项目实战验证。

## 构建方式
使用自研的 Multi-Agent 层级协作系统（Manager + 5 Workers）完成约 85% 的代码开发：
- **Alex（开发）**：Pinia Store、API 层、所有 5 个 Vue 组件、Vite 配置
- **Sophia（审查）**：83 项代码审查清单、核心文件审查、首次 Worker 间协作验证
- **Nathaniel（测试）**：单元测试 + 集成测试 + 测试计划文档
- **Elena（文档）**：README 完整文档（权限受限，通过 Sophia 中转）
- **Marcus（运维）**：构建验证

人工完成：FundTable.vue 补全（系统超时）、localStorage 持久化调试修复。

## 技术栈
Vue 3 + Vite + Pinia + Ant Design Vue 4 + ECharts + Axios + localStorage

## 成果
完整的基金可视化看板，支持基金录入、实时估值、资产卡片、饼图分类、收益排行、持仓管理、数据持久化。构建 0 错误，开发服务器正常运行。
