/**
 * FundVision 核心状态管理（Pinia Store）
 *
 * 职责：
 *   - 维护基金持仓列表（funds 数组）
 *   - 提供增删改查与刷新操作
 *   - 自动计算总资产、总收益、收益率等派生状态
 *   - 数据持久化到 localStorage
 */
import { defineStore } from 'pinia'
import { getItem, setItem } from '../utils/storage'
import { fetchMultipleFunds } from '../api/fund'

/** localStorage 键名 */
const STORAGE_KEY = 'funds'

/**
 * 根据基金名称推断基金类型，用于饼图分类展示
 * @param {string} name - 基金名称
 * @returns {string} 类型标签
 */
function inferCategory(name) {
  if (!name) return '其他'
  if (name.includes('货币')) return '货币型'
  if (name.includes('债券') || name.includes('债')) return '债券型'
  if (name.includes('指数')) return '指数型'
  if (name.includes('ETF')) return '指数型'
  if (name.includes('混合')) return '混合型'
  if (name.includes('股票')) return '股票型'
  if (name.includes('QDII')) return 'QDII'
  return '其他'
}

export const useFundStore = defineStore('fund', {
  // ==================== 状态 ====================
  state: () => ({
    /**
     * 基金持仓列表
     * 每项结构：
     *   code        - 基金代码（唯一标识）
     *   name        - 基金名称（来自 API）
     *   category    - 基金类型（从名称推断）
     *   shares      - 持有份额
     *   costNav     - 成本净值
     *   currentNav  - 当前净值（来自 API 实时估值）
     *   navDate     - 净值日期
     *   updateTime  - 估值时间
     *   loading     - 是否正在刷新
     */
    funds: [],
    /** 全局加载状态 */
    loading: false
  }),

  // ==================== 派生状态（Getters） ====================
  getters: {
    /**
     * 总资产 = Σ(每只基金持有份额 × 当前净值)
     */
    totalAssets: (state) => {
      return state.funds.reduce((sum, f) => {
        return sum + (f.shares || 0) * (f.currentNav || 0)
      }, 0)
    },

    /**
     * 总收益 = Σ((当前净值 - 成本净值) × 持有份额)
     */
    totalProfit: (state) => {
      return state.funds.reduce((sum, f) => {
        return sum + ((f.currentNav || 0) - (f.costNav || 0)) * (f.shares || 0)
      }, 0)
    },

    /**
     * 总收益率 = 总收益 / 总成本
     */
    totalProfitRate: (state) => {
      const totalCost = state.funds.reduce((sum, f) => {
        return sum + (f.costNav || 0) * (f.shares || 0)
      }, 0)
      if (totalCost === 0) return 0
      return ((state.funds.reduce((sum, f) => {
        return sum + ((f.currentNav || 0) - (f.costNav || 0)) * (f.shares || 0)
      }, 0)) / totalCost) * 100
    },

    /**
     * 按基金类型分布的统计数据，供饼图使用
     * 返回 { 类型名: 资产总额 } 的映射
     */
    typeDistribution: (state) => {
      const dist = {}
      state.funds.forEach(f => {
        const cat = f.category || '其他'
        const asset = (f.shares || 0) * (f.currentNav || 0)
        dist[cat] = (dist[cat] || 0) + asset
      })
      return dist
    },

    /**
     * 按收益率降序排列的基金列表，供排行组件使用
     */
    rankedFunds: (state) => {
      return [...state.funds]
        .map(f => ({
          ...f,
          profit: ((f.currentNav || 0) - (f.costNav || 0)) * (f.shares || 0),
          profitRate: (f.costNav || 0) !== 0
            ? (((f.currentNav || 0) - (f.costNav || 0)) / f.costNav) * 100
            : 0
        }))
        .sort((a, b) => b.profitRate - a.profitRate)
    }
  },

  // ==================== 操作（Actions） ====================
  actions: {
    /**
     * 添加基金到持仓列表
     * @param {string} code   - 6 位基金代码
     * @param {number} shares - 持有份额
     * @param {number} costNav - 成本净值
     */
    async addFund(code, shares, costNav) {
      // 去重检查
      if (this.funds.some(f => f.code === code)) {
        throw new Error(`基金 ${code} 已在持仓列表中`)
      }

      // 临时插入一条"加载中"的占位记录
      const temp = {
        code,
        name: '加载中…',
        category: '其他',
        shares: parseFloat(shares),
        costNav: parseFloat(costNav),
        currentNav: 0,
        navDate: '',
        updateTime: '',
        loading: true
      }
      this.funds.push(temp)

      try {
        const map = await fetchMultipleFunds([code])
        const info = map.get(code)
        if (info) {
          // 用 API 返回的数据更新这条记录
          const idx = this.funds.findIndex(f => f.code === code)
          if (idx !== -1) {
            this.funds[idx] = {
              ...this.funds[idx],
              name: info.name,
              category: inferCategory(info.name),
              currentNav: info.currentNav,
              navDate: info.navDate,
              updateTime: info.updateTime,
              loading: false
            }
          }
        } else {
          throw new Error('API 未返回该基金数据')
        }
      } catch (err) {
        // 请求失败则移除占位记录
        this.funds = this.funds.filter(f => f.code !== code)
        throw err
      }

      this.saveToStorage()
    },

    /**
     * 从持仓列表中删除指定基金
     * @param {string} code - 基金代码
     */
    removeFund(code) {
      this.funds = this.funds.filter(f => f.code !== code)
      this.saveToStorage()
    },

    /**
     * 刷新所有基金的实时净值
     */
    async refreshAllFunds() {
      if (this.funds.length === 0) return

      this.loading = true
      // 标记所有基金为加载中
      this.funds.forEach(f => { f.loading = true })

      const codes = this.funds.map(f => f.code)
      try {
        const map = await fetchMultipleFunds(codes)
        this.funds.forEach(f => {
          const info = map.get(f.code)
          if (info) {
            f.name = info.name
            f.category = inferCategory(info.name)
            f.currentNav = info.currentNav
            f.navDate = info.navDate
            f.updateTime = info.updateTime
            f.loading = false
          } else {
            f.loading = false
          }
        })
        this.saveToStorage()
      } finally {
        this.loading = false
      }
    },

    /**
     * 刷新单只基金
     * @param {string} code - 基金代码
     */
    async refreshSingleFund(code) {
      const fund = this.funds.find(f => f.code === code)
      if (!fund) return

      fund.loading = true
      try {
        const map = await fetchMultipleFunds([code])
        const info = map.get(code)
        if (info) {
          fund.name = info.name
          fund.category = inferCategory(info.name)
          fund.currentNav = info.currentNav
          fund.navDate = info.navDate
          fund.updateTime = info.updateTime
        }
      } finally {
        fund.loading = false
        this.saveToStorage()
      }
    },

    /**
     * 从 localStorage 加载持仓数据
     */
    loadFromStorage() {
      const saved = getItem(STORAGE_KEY, [])
      if (Array.isArray(saved) && saved.length > 0) {
        this.funds = saved.map(f => ({
          code: f.code || '',
          name: f.name || '未知基金',
          category: f.category || inferCategory(f.name),
          shares: parseFloat(f.shares) || 0,
          costNav: parseFloat(f.costNav) || 0,
          currentNav: parseFloat(f.currentNav) || 0,
          navDate: f.navDate || '',
          updateTime: f.updateTime || '',
          loading: false
        }))
      }
    },

    /**
     * 将当前持仓数据持久化到 localStorage
     */
    saveToStorage() {
      const toSave = this.funds.map(f => ({
        code: f.code,
        name: f.name,
        category: f.category,
        shares: f.shares,
        costNav: f.costNav,
        currentNav: f.currentNav,
        navDate: f.navDate,
        updateTime: f.updateTime
      }))
      const ok = setItem(STORAGE_KEY, toSave)
      if (ok) {
        console.log(`[fund store] 已保存 ${toSave.length} 只基金到 localStorage`)
      } else {
        console.error('[fund store] 保存失败！数据可能在页面刷新后丢失')
      }
      return ok
    }
  }
})
