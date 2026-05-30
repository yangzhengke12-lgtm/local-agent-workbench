<template>
  <!--
    AssetPieChart - 资产配置饼图
    使用 ECharts 按基金类型展示资产分布
  -->
  <a-card title="🍩 资产配置分布" class="pie-chart-card">
    <div
      v-if="hasData"
      ref="chartRef"
      class="chart-container"
    ></div>
    <a-empty v-else description="暂无持仓数据" />
  </a-card>
</template>

<script setup>
/**
 * AssetPieChart.vue —— 按基金类型分布的饼图组件
 * 监听 store.typeDistribution 变化，自动更新 ECharts 图表
 */
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
import { useFundStore } from '../stores/fund'

const store = useFundStore()

const chartRef = ref(null)
let chartInstance = null

/** 是否有数据可渲染 */
const hasData = computed(() => store.funds.length > 0)

/**
 * 将 typeDistribution 对象转换为 ECharts 饼图所需的数组格式
 * { '混合型': 1000, '指数型': 500 } → [{ name: '混合型', value: 1000 }, ...]
 */
function buildChartData() {
  const dist = store.typeDistribution
  return Object.entries(dist)
    .filter(([, value]) => value > 0)
    .map(([name, value]) => ({ name, value: Number(value.toFixed(2)) }))
}

/** 初始化或更新 ECharts 饼图 */
function renderChart() {
  const data = buildChartData()
  if (!chartRef.value || data.length === 0) return

  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value)
  }

  chartInstance.setOption(
    {
      tooltip: {
        trigger: 'item',
        formatter: '{b}: ¥{c} ({d}%)'
      },
      legend: {
        orient: 'horizontal',
        bottom: 0,
        textStyle: { fontSize: 12 }
      },
      series: [
        {
          type: 'pie',
          radius: ['45%', '72%'],
          center: ['50%', '48%'],
          avoidLabelOverlap: true,
          itemStyle: {
            borderRadius: 4,
            borderColor: '#fff',
            borderWidth: 2
          },
          label: {
            show: true,
            formatter: '{b}\n{d}%'
          },
          emphasis: {
            label: { fontSize: 16, fontWeight: 'bold' },
            scaleSize: 8
          },
          data
        }
      ]
    },
    true // notMerge = true，每次全量替换
  )
}

/** 响应窗口大小变化 */
function handleResize() {
  chartInstance?.resize()
}

// 监听数据变化，自动重绘
watch(() => store.typeDistribution, () => {
  renderChart()
}, { deep: true })

onMounted(() => {
  renderChart()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chartInstance?.dispose()
  chartInstance = null
})
</script>

<style scoped>
.pie-chart-card {
  height: 100%;
}

.chart-container {
  width: 100%;
  height: 340px;
}
</style>
