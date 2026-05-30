# FundVision 测试策略文档

## 1. 项目概述

FundVision 是一个基于 Vue 3 + Vite + Pinia 的基金可视化看板 Web 应用，核心功能包括：
- 手动录入基金持仓（代码、份额、成本净值）
- 通过天天基金 API 获取实时净值
- 自动计算持仓收益、收益率和总资产
- 可视化展示（卡片、饼图、排行、表格）
- localStorage 数据持久化

## 2. 测试层次与范围

```
                    ┌─────────────────────┐
                    │    E2E 测试          │  ← Cypress / Playwright
                    │  (完整用户流程)       │
                    ├─────────────────────┤
                    │   组件测试            │  ← Vue Test Utils + vitest
                    │  (UI 渲染和交互)      │
                    ├─────────────────────┤
                    │   集成测试            │  ← vitest
                    │  (Store+API+Storage) │
                    ├─────────────────────┤
                    │   单元测试            │  ← vitest
                    │  (纯函数/工具/API)    │
                    └─────────────────────┘
```

## 3. 测试策略

### 3.1 单元测试（高优先级）

| 模块 | 测试目标 | 关键场景 |
|------|---------|---------|
| `src/utils/storage.js` | localStorage 封装 | CRUD 操作、JSON 序列化异常、边界值 |
| `src/api/fund.js` | API 调用与 JSONP 解析 | 正常响应解析、异常格式、网络超时 |
| `src/stores/fund.js` | Pinia Store 状态管理 | 添加/删除/刷新基金、计算 getters、持久化 |

### 3.2 集成测试（中优先级）

| 场景 | 测试目标 |
|------|---------|
| addFund → API → Store | 完整的添加基金流程 |
| refreshAllFunds → API → Store | 批量刷新与计算 |
| Store ↔ localStorage | 数据持久化与恢复 |

### 3.3 组件测试（中优先级）

| 组件 | 关键断言 |
|------|---------|
| `FundInput.vue` | 表单校验、提交事件、空值处理 |
| `AssetCards.vue` | 数字格式化、颜色逻辑（正收益/负收益） |
| `FundTable.vue` | 数据渲染、删除/刷新按钮、空状态 |
| `AssetPieChart.vue` | ECharts 配置生成、数据到图表映射 |
| `ProfitRanking.vue` | 排序正确性、空数据展示 |

### 3.4 E2E 测试（低优先级，后续补充）

- 完整流程：打开页面 → 添加基金 → 确认卡片更新 → 刷新 → 删除
- localStorage 数据恢复测试
- 响应式布局验证

## 4. 测试环境

- **测试框架**: vitest（与 Vite 原生集成）
- **断言库**: vitest 内置（兼容 chai）
- **DOM 模拟**: jsdom
- **组件测试**: @vue/test-utils + vitest
- **安装命令**:
  ```bash
  npm install -D vitest @vue/test-utils jsdom
  ```

## 5. 运行测试

```bash
# 运行所有测试
npx vitest run

# 监听模式（开发时）
npx vitest

# 运行指定文件
npx vitest run tests/unit/storage.test.js

# 生成覆盖率报告
npx vitest run --coverage
```

## 6. CI/CD 集成建议

- 每次提交触发 `npm run test:unit`
- PR 合并前要求全部单元测试通过
- 覆盖率目标：核心逻辑 ≥ 80%

## 7. 风险与注意事项

| 风险 | 缓解措施 |
|------|---------|
| 天天基金 API 不稳定 | Mock API 响应，不依赖外部服务测试 |
| localStorage 不可用（隐私模式） | 已在 storage.js 中 try-catch 包裹 |
| JSONP 格式变化 | 单元测试覆盖 parseJsonp 的各种边界 |
| 基金代码前缀逻辑 | 用快照测试或参数化测试覆盖所有前缀 |
| 大额计算精度丢失 | 测试中覆盖大数值和极端收益率场景 |
