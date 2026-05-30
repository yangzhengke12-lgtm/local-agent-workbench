# FundVision 代码审查清单

> 基于 `PROJECT_BRIEF.md` 需求制定，覆盖功能完整性、安全性、性能、代码规范、错误处理五大维度。
> 
> 审查者：Sophia（Code Reviewer）  
> 制定日期：2025-07-14  
> 目标文件范围：`fund-vision/` 下所有源码文件  
> **目标写入路径**：`fund-vision/review_checklist.md`

---

## 一、功能完整性检查

### 1.1 基础设施层

| # | 检查项 | 对应需求 | 状态 |
|---|--------|----------|:----:|
| F-01 | `vite.config.js` 是否配置了 `/fundapi` 代理到 `https://fundgz.1234567.com.cn` | 需求 1 | ⬜ |
| F-02 | 代理是否正确设置了 `changeOrigin: true` 和路径 `rewrite` | 需求 1 | ⬜ |
| F-03 | `src/utils/storage.js` 是否提供了 `getItem` / `setItem` / `removeItem` 三个方法 | 需求 2 | ⬜ |
| F-04 | storage 工具是否正确处理了 `JSON.parse` / `JSON.stringify` 异常 | 需求 2 | ⬜ |
| F-05 | `src/api/fund.js` 是否正确调用 API 路径 `/js/{code}.js` | 需求 3 | ⬜ |
| F-06 | API 层是否正确解析了 JSONP 响应（`jsonpgz({...})`） | 需求 3 | ⬜ |
| F-07 | `src/stores/fund.js` 是否包含所有必需 state：`funds`, `loading` | 需求 4 | ⬜ |
| F-08 | Store 是否包含所有必需 getters：`totalAssets`, `totalProfit`, `totalProfitRate`, `typeDistribution` | 需求 4 | ⬜ |
| F-09 | Store 是否包含所有必需 actions：`addFund`, `removeFund`, `refreshAllFunds`, `loadFromStorage`, `saveToStorage` | 需求 4 | ⬜ |
| F-10 | `src/main.js` 是否正确注册了 Pinia、Ant Design Vue、ECharts | 需求 5 | ⬜ |

### 1.2 组件层（5 个组件均需逐一检查）

| # | 检查项 | 对应需求 | 状态 |
|---|--------|----------|:----:|
| F-11 | `src/components/FundInput.vue`：基金代码 + 持有份额 + 成本净值输入框 + 添加按钮 | 需求 7 | ⬜ |
| F-12 | `src/components/AssetCards.vue`：总资产 / 总收益 / 收益率三张统计卡片 | 需求 8 | ⬜ |
| F-13 | `src/components/AssetPieChart.vue`：按基金类型的资产分布饼图（ECharts） | 需求 9 | ⬜ |
| F-14 | `src/components/ProfitRanking.vue`：按收益率降序排列的收益排行列表 | 需求 10 | ⬜ |
| F-15 | `src/components/FundTable.vue`：含代码/名称/份额/成本净值/当前净值/收益/收益率/操作列 | 需求 11 | ⬜ |
| F-16 | 表格操作列是否包含「删除」和「刷新」按钮 | 需求 11 | ⬜ |

### 1.3 集成与交互

| # | 检查项 | 对应需求 | 状态 |
|---|--------|----------|:----:|
| F-17 | `src/App.vue` 是否实现了完整布局：标题栏 + 卡片行 + 饼图+排行并排 + 表格 | 需求 6 | ⬜ |
| F-18 | 添加基金时是否自动调用 API 获取最新净值并填充 `currentNav` | 需求 2 | ⬜ |
| F-19 | 添加基金后是否自动计算 `profit` 和 `profitRate` | 需求 3 | ⬜ |
| F-20 | 删除基金功能是否正常工作并从 localStorage 同步移除 | 需求 5 | ⬜ |
| F-21 | 刷新功能是否更新所有基金的当前净值并重算收益 | 需求 5 | ⬜ |
| F-22 | 应用启动时是否从 localStorage 恢复数据（`loadFromStorage`） | 需求 6 | ⬜ |
| F-23 | 数据变更时是否自动持久化到 localStorage（`saveToStorage`） | 需求 6 | ⬜ |
| F-24 | `src/components/HelloWorld.vue` 是否已删除（默认模板残留） | — | ⬜ |
| F-25 | `src/style.css` 是否替换为项目专属样式（非 Vite 默认样式） | — | ⬜ |
| F-26 | `README.md` 是否更新为 FundVision 项目说明（非 Vite 默认说明） | — | ⬜ |

---

## 二、安全性检查

### 2.1 输入验证

| # | 检查项 | 风险等级 | 状态 |
|---|--------|:----:|:----:|
| S-01 | 基金代码输入是否做了格式校验（6 位数字） | 🔴 High | ⬜ |
| S-02 | 持有份额输入是否校验为正数 | 🟠 Medium | ⬜ |
| S-03 | 成本净值输入是否校验为正数 | 🟠 Medium | ⬜ |
| S-04 | 是否存在重复添加同一基金代码的防护 | 🟠 Medium | ⬜ |
| S-05 | 用户输入是否做了 trim / 空白字符处理 | 🟢 Low | ⬜ |

### 2.2 API 安全

| # | 检查项 | 风险等级 | 状态 |
|---|--------|:----:|:----:|
| S-06 | API 请求是否通过 Vite proxy 代理，避免前端直接暴露目标 URL | 🟠 Medium | ⬜ |
| S-07 | axios 实例是否设置了合理的 timeout（当前 10000ms） | 🟢 Low | ⬜ |
| S-08 | JSONP 响应解析是否有格式校验（防止非法响应导致 `JSON.parse` 崩溃） | 🟠 Medium | ⬜ |
| S-09 | 是否对 API 返回的数据字段（`gsz`, `dwjz`, `fundcode`, `name`）做了空值防御 | 🟠 Medium | ⬜ |

### 2.3 客户端存储安全

| # | 检查项 | 风险等级 | 状态 |
|---|--------|:----:|:----:|
| S-10 | localStorage 存取是否包裹了 try/catch（隐私模式/配额溢出） | 🟠 Medium | ⬜ |
| S-11 | 存入 localStorage 的数据是否只包含序列化安全的字段（无函数/循环引用） | 🟠 Medium | ⬜ |
| S-12 | 从 localStorage 恢复数据时是否校验了数据结构（`Array.isArray`） | 🟠 Medium | ⬜ |

### 2.4 依赖与供应链

| # | 检查项 | 风险等级 | 状态 |
|---|--------|:----:|:----:|
| S-13 | 是否使用了已知有漏洞的依赖版本（检查 `package.json`） | 🔴 High | ⬜ |
| S-14 | 是否有未使用的依赖残留 | 🟢 Low | ⬜ |

### 2.5 XSS / 注入防护

| # | 检查项 | 风险等级 | 状态 |
|---|--------|:----:|:----:|
| S-15 | 基金名称等 API 返回字符串渲染到 DOM 时是否使用模板语法 `{{ }}` 而非 `v-html` | 🔴 High | ⬜ |
| S-16 | ECharts 图表配置中是否避免直接拼接用户输入的字符串 | 🟠 Medium | ⬜ |
| S-17 | 是否有 `eval`、`new Function`、`innerHTML` / `v-html` 等危险调用 | 🔴 Critical | ⬜ |

---

## 三、性能检查

### 3.1 网络与数据

| # | 检查项 | 状态 |
|---|--------|:----:|
| P-01 | `refreshAllFunds` 是否使用了合理的并发策略（当前为逐个串行刷新，避免 API 压力） | ⬜ |
| P-02 | 是否有请求去重机制（短时间内同一基金代码不重复请求） | ⬜ |
| P-03 | 刷新操作是否有 loading 状态指示，防止用户重复点击 | ⬜ |
| P-04 | localStorage 写入是否过于频繁（每次 `saveToStorage` 调用是否合理） | ⬜ |

### 3.2 渲染性能

| # | 检查项 | 状态 |
|---|--------|:----:|
| P-05 | 基金列表渲染是否使用了 `v-for` 的 `key` 绑定（建议 `fund.code`） | ⬜ |
| P-06 | ECharts 图表实例是否在组件卸载时正确 `dispose`，防止内存泄漏 | ⬜ |
| P-07 | 是否避免在 computed / template 中进行复杂计算（如 `reduce` 遍历大量数据） | ⬜ |
| P-08 | Ant Design Vue 组件是否按需引入还是全量注册（当前为全量 `app.use(Antd)`） | ⬜ |

### 3.3 打包与加载

| # | 检查项 | 状态 |
|---|--------|:----:|
| P-09 | `vite build` 是否成功，产物大小是否合理 | ⬜ |
| P-10 | ECharts 是否考虑按需引入以减少包体积（当前为 `import * as echarts`） | ⬜ |

---

## 四、代码规范检查

### 4.1 Vue 3 Composition API

| # | 检查项 | 状态 |
|---|--------|:----:|
| C-01 | 所有 `.vue` 组件是否使用了 `<script setup>` 语法 | ⬜ |
| C-02 | 是否避免了 Options API 混用（保持 Composition API 一致性） | ⬜ |
| C-03 | `ref` / `reactive` 使用是否恰当（基本类型用 `ref`，对象用 `reactive` 或 `ref`） | ⬜ |
| C-04 | computed 是否只用于派生状态，无副作用 | ⬜ |

### 4.2 注释与文档

| # | 检查项 | 状态 |
|---|--------|:----:|
| C-05 | 所有文件是否包含详细中文注释（需求明确要求） | ⬜ |
| C-06 | 函数/方法是否有 JSDoc 注释说明参数和返回值 | ⬜ |
| C-07 | 复杂逻辑（如 JSONP 解析、基金类型判断）是否有注释解释 | ⬜ |

### 4.3 命名规范

| # | 检查项 | 状态 |
|---|--------|:----:|
| C-08 | 组件文件是否使用 PascalCase（如 `FundInput.vue`） | ⬜ |
| C-09 | 函数/变量是否使用 camelCase | ⬜ |
| C-10 | 常量是否使用 UPPER_SNAKE_CASE（如 `STORAGE_KEY`） | ⬜ |
| C-11 | Pinia store 是否遵循 `useXxxStore` 命名约定 | ⬜ |

### 4.4 项目结构

| # | 检查项 | 状态 |
|---|--------|:----:|
| C-12 | 目录结构是否清晰：`api/`, `stores/`, `utils/`, `components/` | ⬜ |
| C-13 | 是否有未清理的模板残留文件（如 `HelloWorld.vue`、默认 assets） | ⬜ |
| C-14 | `package.json` 中 `scripts` 是否可用（`dev` / `build` / `preview`） | ⬜ |

### 4.5 响应式设计

| # | 检查项 | 状态 |
|---|--------|:----:|
| C-15 | 布局是否在 PC 端正常显示，无明显错位 | ⬜ |
| C-16 | CSS 是否使用了适当的布局方案（Flexbox / Grid） | ⬜ |

---

## 五、错误处理检查

### 5.1 API 错误

| # | 检查项 | 状态 |
|---|--------|:----:|
| E-01 | API 请求失败时是否有用户友好的错误提示（Toast / Message） | ⬜ |
| E-02 | `fetchFundNav` 的 catch 是否向上抛出有意义的错误信息 | ⬜ |
| E-03 | 网络超时是否有处理（axios timeout 已设 10s） | ⬜ |
| E-04 | JSONP 解析失败时是否有明确的异常抛出 | ⬜ |

### 5.2 用户操作错误

| # | 检查项 | 状态 |
|---|--------|:----:|
| E-05 | 添加已存在的基金代码时是否有提示（当前为重复添加去重） | ⬜ |
| E-06 | 添加不存在的基金代码时是否有友好报错而非静默失败 | ⬜ |
| E-07 | 表单提交前是否校验必填字段，缺失时是否有指引 | ⬜ |

### 5.3 边界情况

| # | 检查项 | 状态 |
|---|--------|:----:|
| E-08 | 持仓为空（无基金）时，卡片/表格/饼图是否正确展示空状态 | ⬜ |
| E-09 | 只有 1 只基金时，图表和列表是否正常显示 | ⬜ |
| E-10 | localStorage 损坏（非法 JSON）时，应用是否能正常启动而不白屏 | ⬜ |
| E-11 | API 返回异常数据（如 `gsz` 为 null、`fundcode` 缺失）时是否防御 | ⬜ |

---

## 六、补充检查（2025-07-14）

| # | 检查项 | 状态 |
|---|--------|:----:|
| B-01 | `src/api/fund.js` 是否为每个请求添加了时间戳或缓存破坏参数，避免代理/浏览器缓存旧数据 | ⬜ |
| B-02 | `src/api/fund.js` 是否处理了 `axios` 响应拦截中可能出现的非字符串 `data`（如 Buffer/Stream） | ⬜ |
| B-03 | `src/stores/fund.js` 的 `typeDistribution` getter 是否对空 `funds` 数组返回空对象 `{}` | ⬜ |
| B-04 | `src/stores/fund.js` 的 `loadFromStorage` 是否对恢复后的数据自动触发一次 `refreshAllFunds` 以保证净值时效性 | ⬜ |
| B-05 | 所有 `Modal` / `Popconfirm` 等二次确认组件是否指定了 `getPopupContainer` 防止浮层定位异常 | ⬜ |
| B-06 | `AssetPieChart` 组件在窗口 `resize` 时是否调用了 `chart.resize()` | ⬜ |
| B-07 | `FundInput` 表单在添加成功后是否清空了输入项（避免连续添加时残留旧数据） | ⬜ |
| B-08 | `App.vue` 的 `onMounted` 中是否处理了 `loadFromStorage` 和 `refreshAllFunds` 可能抛出的异常 | ⬜ |
| B-09 | 代码中是否存在硬编码的魔法数字（如 `10000`、`6`），是否应提取为常量 | ⬜ |
| B-10 | `ProfitRanking` 排名列表在无数据时是否有「暂无数据」的空状态提示 | ⬜ |
| B-11 | `FundTable` 表格在无数据时是否有 `emptyText` / `a-empty` 空状态展示 | ⬜ |
