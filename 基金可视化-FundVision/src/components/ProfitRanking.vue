<template>
  <!--
    ProfitRanking - 收益排行列表
    按收益率降序展示各基金的盈利表现
  -->
  <a-card title="🏆 收益排行" class="ranking-card">
    <a-list
      v-if="store.rankedFunds.length > 0"
      :data-source="store.rankedFunds"
      size="small"
    >
      <template #renderItem="{ item, index }">
        <a-list-item>
          <template #extra>
            <a-tag :color="item.profitRate >= 0 ? 'red' : 'green'">
              {{ item.profitRate >= 0 ? '+' : '' }}{{ item.profitRate.toFixed(2) }}%
            </a-tag>
          </template>
          <a-list-item-meta>
            <template #title>
              <span class="rank-index">#{{ index + 1 }}</span>
              <span class="fund-name">{{ item.name }}</span>
              <span class="fund-code">{{ item.code }}</span>
            </template>
            <template #description>
              收益：{{ formatMoney(item.profit) }}
            </template>
          </a-list-item-meta>
        </a-list-item>
      </template>
    </a-list>
    <a-empty v-else description="暂无持仓数据，请先添加基金" />
  </a-card>
</template>

<script setup>
/**
 * ProfitRanking.vue —— 收益排行组件
 * 按收益率降序排列，展示基金名称、代码、收益率和收益金额
 */
import { useFundStore } from '../stores/fund'

const store = useFundStore()

/**
 * 格式化金额
 * @param {number} value
 * @returns {string}
 */
function formatMoney(value) {
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  const [intPart, decPart] = abs.toFixed(2).split('.')
  const intWithComma = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${sign}¥${intWithComma}.${decPart}`
}
</script>

<style scoped>
.ranking-card {
  height: 100%;
}

.rank-index {
  display: inline-block;
  width: 32px;
  color: #faad14;
  font-weight: 700;
  font-size: 14px;
}

.fund-name {
  font-weight: 600;
  margin-right: 8px;
}

.fund-code {
  color: #999;
  font-size: 12px;
}
</style>
