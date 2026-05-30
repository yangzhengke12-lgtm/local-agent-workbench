# FundVision - 基金可视化看板 Web 应用

## 技术栈
Vue 3 (Composition API) + Vite + Pinia + Ant Design Vue 4 + ECharts + Axios

## 项目目录
已在 C:\Users\YzK12\Desktop\my-agent\fund-vision 通过 npm create vite 初始化，依赖已安装。

## 核心功能
1. 用户手动录入基金代码、持有份额、成本净值
2. 调用天天基金API自动获取最新净值（API: https://fundgz.1234567.com.cn/js/基金代码.js）
3. 自动计算持仓收益、收益率、总资产
4. 可视化展示：总资产/总收益/收益率卡片、资产配置饼图、收益排行列表
5. 基金持仓表格（支持添加、删除、刷新）
6. 数据持久化到浏览器 localStorage

## 需要创建的文件和内容

### 1. vite.config.js（覆盖已有文件）
配置代理解决天天基金API跨域问题，代理 /fundapi 到 https://fundgz.1234567.com.cn

### 2. src/utils/storage.js
localStorage 封装，提供 getItem/setItem/removeItem，处理 JSON 序列化和异常

### 3. src/api/fund.js
通过 axios 调用天天基金 API 获取基金实时净值，使用 Vite proxy 路径 /fundapi

### 4. src/stores/fund.js（Pinia Store）
核心状态管理：
- state: funds 数组（每项含 code/name/shares/costNav/currentNav/profit/profitRate）
- actions: addFund/removeFund/refreshAllFunds/loadFromStorage/saveToStorage
- getters: totalAssets/totalProfit/totalProfitRate/typeDistribution

### 5. src/main.js（覆盖已有文件）
注册 Pinia、Ant Design Vue（中文）、ECharts（全局挂载）

### 6. src/App.vue（覆盖已有文件）
整体布局：顶部标题栏 + 资产卡片行 + 饼图+排行并排 + 持仓表格

### 7. src/components/FundInput.vue
基金录入表单：基金代码输入框 + 持有份额输入框 + 成本净值输入框 + 添加按钮，使用 Ant Design Vue 的 form/input/button 组件

### 8. src/components/AssetCards.vue
三张统计卡片：总资产、总收益、收益率，使用 Ant Design Vue 的 Card/Statistic 组件

### 9. src/components/AssetPieChart.vue
按基金类型分布的饼图，使用 ECharts（通过 vue-echarts 或直接 init）

### 10. src/components/ProfitRanking.vue
收益排行列表，按收益率降序排列，用 Ant Design Vue 的 Table 或 List

### 11. src/components/FundTable.vue
基金持仓表格，列：代码/名称/份额/成本净值/当前净值/收益/收益率/操作（删除+刷新），使用 Ant Design Vue 的 Table 组件

## 代码要求
- 使用 Vue 3 Composition API（<script setup>）
- 所有文件添加详细中文注释
- 响应式设计，支持 PC 端
- 组件化开发，代码结构清晰
