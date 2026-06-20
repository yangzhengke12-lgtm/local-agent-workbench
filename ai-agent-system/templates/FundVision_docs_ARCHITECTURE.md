# FundVision — 架构说明

## 整体架构

FundVision 采用 **单页应用（SPA）** 架构，前端通过 Vite 开发服务器代理调用第三方基金 API，所有数据缓存在浏览器 localStorage 中，无需后端服务。

```
┌─────────────────────────────────────────────────────────┐
│                     FundVision SPA                       │
│                                                         │
│  ┌───────────┐   ┌──────────────┐   ┌───────────────┐  │
│  │  App.vue   │──▶│  Components   │──▶│  Pinia Store  │  │
│  │ (布局容器)  │   │  (5个子组件)  │   │  (fund.js)    │  │
│  └───────────┘   └──────────────┘   └───────┬───────┘  │
│                                              │          │
│                                     ┌───────▼───────┐  │
│                                     │ localStorage  │  │
│                                     │ (持久化)       │  │
│                                     └───────────────┘  │
│                                              │          │
│                                     ┌───────▼───────┐  │
│                                     │  api/fund.js  │  │
│                                     │ (Axios请求)    │  │
│                                     └───────┬───────┘  │
│                                              │          │
└──────────────────────────────────────────────┼──────────┘
                                               │
                                        ┌──────▼──────┐
                                        │  Vite Proxy  │
                                        │  /fundapi →  │
                                        │  天天基金API  │
                                        └─────────────┘
```

---

## 组件树

```
App.vue（根组件，整体布局）
├── FundInput.vue           # 基金录入表单（顶部录入区）
├── AssetCards.vue          # 资产统计卡片（总资产/总收益/收益率）
├── AssetPieChart.vue       # 资产配置饼图（按基金类型分布）
├── ProfitRanking.vue       # 收益排行列表（按收益率降序）
└── FundTable.vue           # 基金持仓表格（带操作按钮）
```

---

## 数据流

### 核心数据流向

```
用户输入（基金代码/份额/成本净值）
        │
        ▼
FundInput.vue ──dispatch──▶ Pinia Store (fund.js)
                                │
                        ┌───────┼────────┐
                        │       │        │
                        ▼       ▼        ▼
                  addFund()   refreshAllFunds()   removeFund()
                        │       │
                        │       ▼
                        │   api/fund.js ──▶ 天天基金 API
                        │       │
                        │       ▼
                        │   返回最新净值数据
                        │       │
                        └───────┘
                                │
                                ▼
                    更新 funds 数组状态
                    触发 getters 重新计算
                                │
                ┌───────────────┼────────────────┐
                │               │                │
                ▼               ▼                ▼
          AssetCards     AssetPieChart       ProfitRanking
          (totalAssets/  (typeDistribution)  (按 profitRate 排序)
           totalProfit/
           totalProfitRate)
                │
                ▼
           FundTable
           (展示所有持仓明细)
```

### 数据流说明

1. **录入流程：** 用户在 FundInput 中输入基金代码、持有份额、成本净值 → 点击添加按钮 → 调用 Pinia Store 的 `addFund()` action → 自动调用 API 获取当前净值 → 计算收益和收益率 → 状态更新 → 所有订阅组件重新渲染

2. **刷新流程：** 用户点击刷新按钮（FundTable 或全局） → 调用 `refreshAllFunds()` → 遍历所有基金调用 API 获取最新净值 → 重新计算收益 → 状态更新

3. **删除流程：** 用户点击删除按钮 → 调用 `removeFund(code)` → 从 funds 数组中移除 → 状态更新

---

## API 调用链路

```
浏览器
  │
  │ 请求: GET /fundapi/js/基金代码.js
  │
  ▼
Vite Dev Server
  │
  │ 代理规则: /fundapi → https://fundgz.1234567.com.cn
  │
  ▼
天天基金 API (https://fundgz.1234567.com.cn/js/基金代码.js)
  │
  │ 响应: JSONP 回调格式（如 jsonpgz({...})）
  │
  ▼
api/fund.js
  │
  │ 解析 JSONP 响应 → 提取净值数据
  │
  ▼
Pinia Store → 更新状态 → 组件重新渲染
```

> **注意：** 天天基金 API 返回的是 JSONP 格式（`jsonpgz({...})`），需要去除外层函数调用后解析 JSON。[待确认] 实际响应格式及解析方式需对接时验证。

---

## localStorage 持久化策略

### 存储结构

```json
{
  "fund-vision:funds": [
    {
      "code": "110011",
      "name": "易方达中小盘混合",
      "shares": 1000.00,
      "costNav": 2.5000,
      "currentNav": 2.8000,
      "profit": 300.00,
      "profitRate": 12.00
    }
    // ... 更多基金
  ]
}
```

### 持久化流程

```
应用初始化
    │
    ▼
loadFromStorage() ← 从 localStorage 读取 "fund-vision:funds"
    │
    ├── 有数据 → 解析 JSON → 填充 funds 数组
    └── 无数据 → funds 初始化为空数组
    │
    ▼
用户操作（添加/删除/刷新）
    │
    ▼
saveToStorage() ← 每次状态变更后自动调用
    │
    ├── 序列化 funds 数组为 JSON
    ├── 写入 localStorage 的 "fund-vision:funds" 键
    └── 异常处理：JSON 序列化失败或 storage 写满时静默捕获
```

### 策略要点

- **存储时机：** 每次 funds 数组变化后立即持久化（写后即存）
- **存储键名：** 使用 `fund-vision:funds` 作为前缀，避免与其他应用冲突
- **异常处理：** 读写均包裹 try-catch，localStorage 不可用时静默降级
- **数据容量：** localStorage 通常有 5MB 限制，基金持仓数据量小，不会触发上限
