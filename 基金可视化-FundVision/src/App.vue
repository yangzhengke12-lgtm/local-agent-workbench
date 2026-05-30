<template>
  <!--
    FundVision 主布局
    - 顶部标题栏
    - 基金录入表单
    - 资产统计卡片
    - 饼图 + 收益排行（左右并排）
    - 基金持仓表格
  -->
  <div class="app-container">
    <!-- 顶部标题栏 -->
    <header class="app-header">
      <h1 class="app-title">📊 FundVision 基金可视化看板</h1>
      <span class="app-subtitle">实时追踪 · 一目了然</span>
    </header>

    <!-- 基金录入表单 -->
    <FundInput />

    <!-- 资产统计卡片 -->
    <AssetCards />

    <!-- 中间区域：饼图 + 收益排行 -->
    <a-row :gutter="16" class="middle-row">
      <a-col :xs="24" :lg="12">
        <AssetPieChart />
      </a-col>
      <a-col :xs="24" :lg="12">
        <ProfitRanking />
      </a-col>
    </a-row>

    <!-- 基金持仓表格 -->
    <FundTable />
  </div>
</template>

<script setup>
/**
 * App.vue —— FundVision 根组件
 * 组合所有子组件，构成完整的基金看板页面
 */
import { onMounted, watch } from 'vue'
import { useFundStore } from './stores/fund'
import FundInput from './components/FundInput.vue'
import AssetCards from './components/AssetCards.vue'
import AssetPieChart from './components/AssetPieChart.vue'
import ProfitRanking from './components/ProfitRanking.vue'
import FundTable from './components/FundTable.vue'

const store = useFundStore()

// 应用启动时从 localStorage 恢复数据，并自动刷新净值
onMounted(async () => {
  store.loadFromStorage()
  if (store.funds.length > 0) {
    // 静默刷新，不阻塞 UI
    store.refreshAllFunds().catch(err => {
      console.error('初始刷新失败:', err)
    })
  }
})

// 防抖自动保存：任何 funds 变化 500ms 后自动持久化
// 防止 Vite HMR 重置 Pinia 状态导致数据丢失
let saveTimer = null
watch(() => store.funds, () => {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    store.saveToStorage()
  }, 500)
}, { deep: true })
</script>

<style scoped>
.app-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 16px 24px 40px;
  min-height: 100vh;
  background: #f5f5f5;
}

.app-header {
  text-align: center;
  padding: 24px 0 8px;
}

.app-title {
  font-size: 26px;
  font-weight: 700;
  color: #1a1a2e;
  margin: 0;
  display: inline-block;
}

.app-subtitle {
  display: block;
  color: #888;
  font-size: 14px;
  margin-top: 4px;
}

.middle-row {
  margin: 16px 0;
}
</style>
