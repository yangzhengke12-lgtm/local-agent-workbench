<template>
  <!--
    AssetCards - 资产统计卡片行
    展示：总资产 / 总收益 / 收益率 三张卡片
  -->
  <a-row :gutter="16" class="asset-cards-row">
    <!-- 总资产卡片 -->
    <a-col :xs="24" :sm="8">
      <a-card class="stat-card">
        <div class="stat-label">💰 总资产</div>
        <div class="stat-value">{{ formatMoney(store.totalAssets) }}</div>
        <div class="stat-desc">持有基金总市值</div>
      </a-card>
    </a-col>

    <!-- 总收益卡片 -->
    <a-col :xs="24" :sm="8">
      <a-card class="stat-card" :class="profitClass">
        <div class="stat-label">📈 总收益</div>
        <div class="stat-value">{{ formatMoney(store.totalProfit) }}</div>
        <div class="stat-desc">浮动盈亏</div>
      </a-card>
    </a-col>

    <!-- 收益率卡片 -->
    <a-col :xs="24" :sm="8">
      <a-card class="stat-card" :class="profitClass">
        <div class="stat-label">📊 收益率</div>
        <div class="stat-value">{{ store.totalProfitRate.toFixed(2) }}%</div>
        <div class="stat-desc">总收益 / 总成本</div>
      </a-card>
    </a-col>
  </a-row>
</template>

<script setup>
/**
 * AssetCards.vue —— 资产统计卡片组件
 * 从 store 读取总资产、总收益、收益率并展示
 */
import { computed } from 'vue'
import { useFundStore } from '../stores/fund'

const store = useFundStore()

/** 根据盈亏决定卡片色调 */
const profitClass = computed(() => ({
  'profit-positive': store.totalProfit > 0,
  'profit-negative': store.totalProfit < 0
}))

/**
 * 格式化金额为带千分位的字符串
 * @param {number} value - 金额
 * @returns {string} 如 "¥12,345.67"
 */
function formatMoney(value) {
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  const fixed = abs.toFixed(2)
  const [intPart, decPart] = fixed.split('.')
  const intWithComma = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${sign}¥${intWithComma}.${decPart}`
}
</script>

<style scoped>
.asset-cards-row {
  margin: 16px 0;
}

.stat-card {
  text-align: center;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.stat-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  transform: translateY(-2px);
}

.stat-label {
  font-size: 14px;
  color: #888;
  margin-bottom: 4px;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #1a1a2e;
  margin: 8px 0;
  word-break: break-all;
}

.stat-desc {
  font-size: 12px;
  color: #aaa;
}

/* 盈亏颜色 */
.profit-positive .stat-value {
  color: #e63946;
}

.profit-negative .stat-value {
  color: #2a9d2f;
}
</style>
