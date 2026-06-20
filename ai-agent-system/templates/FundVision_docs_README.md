# FundVision — 基金可视化看板

## 项目简介

FundVision 是一个基于 Vue 3 生态的基金持仓可视化看板 Web 应用。用户可以通过录入基金代码、持有份额和成本净值，自动获取天天基金 API 的实时净值数据，直观查看总资产、收益统计、资产配置分布与持仓排名等关键信息。

> **适用场景：** 个人投资者管理多只基金持仓，快速掌握整体收益状况。

---

## 技术栈

| 类别 | 技术 | 版本 |
|------|------|------|
| 前端框架 | Vue 3 (Composition API + `<script setup>`) | ^3.5.34 |
| 构建工具 | Vite | ^8.0.12 |
| 状态管理 | Pinia | ^3.0.4 |
| UI 组件库 | Ant Design Vue 4 | ^4.2.6 |
| 图表库 | ECharts | ^6.1.0 |
| HTTP 客户端 | Axios | ^1.16.1 |
| 数据持久化 | 浏览器 localStorage | — |

---

## 快速启动

### 前置依赖

- Node.js >= 18.x
- npm >= 9.x / yarn / pnpm

### 启动步骤

```bash
# 1. 进入项目目录
cd fund-vision

# 2. 安装依赖（如尚未安装）
npm install

# 3. 启动开发服务器（默认监听 http://localhost:5173）
npm run dev
```

### 生产构建

```bash
# 构建生产版本，输出到 dist/ 目录
npm run build

# 本地预览构建产物
npm run preview
```

---

## 目录结构

```
fund-vision/
├── public/                      # 静态资源目录（favicon 等）
├── src/
│   ├── api/
│   │   └── fund.js              # 天天基金 API 调用封装
│   ├── components/
│   │   ├── FundInput.vue        # 基金录入表单组件
│   │   ├── AssetCards.vue       # 资产统计卡片组件
│   │   ├── AssetPieChart.vue    # 资产配置饼图组件
│   │   ├── ProfitRanking.vue    # 收益排行列表组件
│   │   └── FundTable.vue        # 基金持仓表格组件
│   ├── stores/
│   │   └── fund.js              # Pinia Store（核心状态管理）
│   ├── utils/
│   │   └── storage.js           # localStorage 工具封装
│   ├── App.vue                  # 根组件（整体布局）
│   └── main.js                  # 应用入口（注册插件）
├── docs/                        # 项目文档目录
│   ├── README.md                # ← 本文档
│   ├── ARCHITECTURE.md          # 架构说明
│   ├── COMPONENTS.md            # 组件文档
│   ├── API.md                   # API 对接说明
│   └── DEPLOY.md                # 部署指南
├── index.html                   # HTML 入口
├── vite.config.js               # Vite 配置（含代理）
└── package.json                 # 项目依赖与脚本
```

---

## 功能概览

1. **基金录入** — 输入基金代码、持有份额、成本净值，一键添加至持仓列表
2. **实时净值** — 自动调用天天基金 API 获取每只基金的最新单位净值
3. **自动计算** — 持仓收益、收益率、总资产实时计算
4. **数据看板** — 资产卡片、资产配置饼图、收益排行、持仓表格综合展示
5. **数据持久化** — 持仓数据自动保存至浏览器 localStorage，刷新不丢失
6. **持仓管理** — 支持按基金删除个股、一键刷新所有净值
