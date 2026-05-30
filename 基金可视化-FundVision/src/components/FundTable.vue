<template>
  <!--
    FundTable - 基金持仓明细表格
    展示完整持仓信息，支持单只基金刷新和删除操作
  -->
  <a-card title="📋 基金持仓明细" class="fund-table-card" :bordered="true">
    <a-table
      :columns="columns"
      :data-source="store.funds"
      :loading="store.loading"
      row-key="code"
      :pagination="false"
      size="middle"
      :locale="{ emptyText: '暂无持仓基金，请在上方添加' }"
    >
      <!-- 自定义列：基金名称（含代码） -->
      <template #bodyCell="{ column, record }">
        <!-- 基金信息列 -->
        <template v-if="column.key === 'info'">
          <div class="fund-info">
            <span class="fund-name">{{ record.name }}</span>
            <span class="fund-code">{{ record.code }}</span>
          </div>
        </template>

        <!-- 净值列 -->
        <template v-else-if="column.key === 'currentNav'">
          <span v-if="record.loading" class="loading-text">更新中…</span>
          <span v-else class="nav-value">{{ record.currentNav.toFixed(4) }}</span>
          <div class="nav-time" v-if="record.updateTime && !record.loading">
            {{ record.updateTime }}
          </div>
        </template>

        <!-- 收益列 -->
        <template v-else-if="column.key === 'profit'">
          <span :class="getProfit(record) >= 0 ? 'profit-up' : 'profit-down'">
            {{ formatMoney(getProfit(record)) }}
          </span>
        </template>

        <!-- 收益率列 -->
        <template v-else-if="column.key === 'profitRate'">
          <span :class="getProfitRate(record) >= 0 ? 'profit-up' : 'profit-down'">
            {{ getProfitRate(record).toFixed(2) }}%
          </span>
        </template>

        <!-- 操作列 -->
        <template v-else-if="column.key === 'action'">
          <a-space>
            <a-button
              type="link"
              size="small"
              :loading="record.loading"
              @click="handleRefresh(record.code)"
            >
              🔄 刷新
            </a-button>
            <a-popconfirm
              title="确定要删除该基金吗？"
              ok-text="确定删除"
              cancel-text="取消"
              @confirm="handleDelete(record.code)"
            >
              <a-button type="link" size="small" danger> 🗑️ 删除</a-button>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>
  </a-card>
</template>

<script setup>
/**
 * FundTable.vue —— 基金持仓明细表格组件
 * 展示完整的基金持仓信息，支持单只基金的刷新与删除
 */
import { computed } from 'vue'
import { useFundStore } from '../stores/fund'

const store = useFundStore()

/** 表格列定义 */
const columns = [
  {
    title: '基金信息',
    key: 'info',
    width: 180
  },
  {
    title: '持有份额',
    dataIndex: 'shares',
    key: 'shares',
    width: 110,
    align: 'right',
    customRender: ({ text }) => (text ? text.toLocaleString() : '-')
  },
  {
    title: '成本净值',
    dataIndex: 'costNav',
    key: 'costNav',
    width: 110,
    align: 'right',
    customRender: ({ text }) => (text ? text.toFixed(4) : '-')
  },
  {
    title: '当前净值',
    key: 'currentNav',
    width: 140,
    align: 'right'
  },
  {
    title: '持仓收益',
    key: 'profit',
    width: 120,
    align: 'right'
  },
  {
    title: '收益率',
    key: 'profitRate',
    width: 100,
    align: 'right'
  },
  {
    title: '操作',
    key: 'action',
    width: 160,
    align: 'center'
  }
]

/**
 * 计算单只基金的持仓收益
 */
function getProfit(fund) {
  return (fund.currentNav - fund.costNav) * fund.shares
}

/**
 * 计算单只基金的收益率
 */
function getProfitRate(fund) {
  if (!fund.costNav || fund.costNav === 0) return 0
  return ((fund.currentNav - fund.costNav) / fund.costNav) * 100
}

/**
 * 格式化金额
 */
function formatMoney(value) {
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  const fixed = abs.toFixed(2)
  const [intPart, decPart] = fixed.split('.')
  const intWithComma = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${sign}¥${intWithComma}.${decPart}`
}

/** 刷新单只基金净值 */
async function handleRefresh(code) {
  await store.refreshSingleFund(code)
}

/** 删除基金 */
function handleDelete(code) {
  store.removeFund(code)
}
</script>

<style scoped>
.fund-table-card {
  margin-top: 16px;
}

.fund-info {
  display: flex;
  flex-direction: column;
}

.fund-name {
  font-weight: 500;
  color: #1a1a2e;
  font-size: 13px;
}

.fund-code {
  font-size: 11px;
  color: #999;
  margin-top: 2px;
}

.nav-value {
  font-weight: 500;
  color: #1a1a2e;
}

.nav-time {
  font-size: 11px;
  color: #aaa;
  margin-top: 2px;
}

.loading-text {
  color: #bbb;
  font-size: 12px;
}

.profit-up {
  color: #e63946;
  font-weight: 500;
}

.profit-down {
  color: #2a9d2f;
  font-weight: 500;
}
</style>
