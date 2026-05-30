/**
 * 集成测试：Store ↔ API ↔ Storage
 *
 * 测试 Pinia Store、天天基金 API（mock）、localStorage
 * 三者之间的完整协作流程。
 *
 * 运行方式：
 *   npx vitest run tests/integration/store-api-storage.test.js
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// ===================== Mock API =====================
const mockFetchFundNav = vi.fn()
vi.mock('../../src/api/fund.js', () => ({
  fetchFundNav: (...args) => mockFetchFundNav(...args)
}))

// ===================== Mock localStorage =====================
/** @type {Record<string, string>} */
let storageData = {}

beforeEach(() => {
  storageData = {}
  vi.stubGlobal('localStorage', {
    getItem: vi.fn((key) => storageData[key] ?? null),
    setItem: vi.fn((key, value) => { storageData[key] = String(value) }),
    removeItem: vi.fn((key) => { delete storageData[key] })
  })
  vi.clearAllMocks()
})

import { useFundStore } from '../../src/stores/fund.js'

function createStore() {
  const pinia = createPinia()
  setActivePinia(pinia)
  return useFundStore()
}

// ===================== 测试套件 =====================

describe('集成测试：Store + API + Storage 协作', () => {
  // ========== 场景 1: 用户录入新基金 ==========
  describe('场景 1: 用户录入新基金（完整流程）', () => {
    it('addFund → API → 计算 → 持久化，全链路验证', async () => {
      const store = createStore()

      // Step 1: 模拟 API 返回
      mockFetchFundNav.mockResolvedValue({
        fundcode: '000001',
        name: '华夏成长混合',
        gsz: '2.4500',
        dwjz: '2.4000',
        gszzl: '0.50',
        gztime: '2025-01-15 14:30:00'
      })

      // Step 2: 用户录入
      await store.addFund('000001', 1000, 2.000)

      // Step 3: 验证 Store 状态
      expect(store.funds).toHaveLength(1)
      const fund = store.funds[0]
      expect(fund.code).toBe('000001')
      expect(fund.name).toBe('华夏成长混合')
      expect(fund.currentNav).toBe(2.45)
      expect(fund.profit).toBeCloseTo(450, 0) // (2.45-2.0)*1000
      expect(fund.profitRate).toBeCloseTo(22.5, 1)

      // Step 4: 验证 localStorage 持久化
      const saved = JSON.parse(storageData['fund-vision-data'])
      expect(saved).toHaveLength(1)
      expect(saved[0].code).toBe('000001')
    })
  })

  // ========== 场景 2: 多基金 + 批量刷新 ==========
  describe('场景 2: 多基金持仓 + 批量刷新', () => {
    it('三只基金：添加 → 刷新 → 验证所有计算', async () => {
      const store = createStore()

      // 添加三只基金
      mockFetchFundNav.mockResolvedValueOnce({
        fundcode: '000001', name: '指数基金A', gsz: '1.500'
      })
      await store.addFund('000001', 100, 1.0)

      mockFetchFundNav.mockResolvedValueOnce({
        fundcode: '110022', name: '股票基金B', gsz: '2.000'
      })
      await store.addFund('110022', 200, 1.5)

      mockFetchFundNav.mockResolvedValueOnce({
        fundcode: '270048', name: '债券基金C', gsz: '1.200'
      })
      await store.addFund('270048', 300, 1.1)

      // 验证总资产
      // 基金A: 1.5*100=150, 基金B: 2.0*200=400, 基金C: 1.2*300=360
      expect(store.totalAssets).toBe(910)
      // 总收益: (0.5)*100 + (0.5)*200 + (0.1)*300 = 50+100+30 = 180
      expect(store.totalProfit).toBeCloseTo(180, 0)

      // 模拟刷新：全部涨了
      mockFetchFundNav
        .mockResolvedValueOnce({ fundcode: '000001', name: '指数基金A', gsz: '1.800' })
        .mockResolvedValueOnce({ fundcode: '110022', name: '股票基金B', gsz: '2.500' })
        .mockResolvedValueOnce({ fundcode: '270048', name: '债券基金C', gsz: '1.150' })

      await store.refreshAllFunds()

      // 验证刷新后的总资产
      // A: 1.8*100=180, B: 2.5*200=500, C: 1.15*300=345
      expect(store.totalAssets).toBe(1025)

      // 类型分布（3种类型）
      expect(store.typeDistribution).toHaveLength(3)
    })
  })

  // ========== 场景 3: 数据持久化往返 ==========
  describe('场景 3: localStorage 数据持久化往返', () => {
    it('关闭页面 → 重新打开 → 数据完整恢复', async () => {
      // 模拟第一次使用：添加基金
      const store1 = createStore()
      mockFetchFundNav.mockResolvedValue({
        fundcode: '000001', name: '持久化测试基金', gsz: '2.000'
      })
      await store1.addFund('000001', 500, 1.800)

      // 验证已保存到 localStorage
      expect(storageData['fund-vision-data']).toBeTruthy()

      // 模拟关闭后重新打开：创建新 store 实例
      const store2 = createStore()
      store2.loadFromStorage()

      // 验证数据完整恢复
      expect(store2.funds).toHaveLength(1)
      expect(store2.funds[0].code).toBe('000001')
      expect(store2.funds[0].name).toBe('持久化测试基金')
      expect(store2.funds[0].shares).toBe(500)
      expect(store2.funds[0].costNav).toBe(1.8)
      expect(store2.totalAssets).toBe(1000) // 2.0 * 500
    })
  })

  // ========== 场景 4: 收益变化跟踪 ==========
  describe('场景 4: 收益变化跟踪', () => {
    it('添加时亏损 → 刷新后盈利 → 收益率从负转正', async () => {
      const store = createStore()

      // 初始添加：成本 2.0，当前净值 1.6（亏损）
      mockFetchFundNav.mockResolvedValue({
        fundcode: '000001', name: '波动基金', gsz: '1.600'
      })
      await store.addFund('000001', 1000, 2.000)

      expect(store.totalProfit).toBe(-400)
      expect(store.totalProfitRate).toBeCloseTo(-20, 1)

      // 刷新：净值涨到 2.4（盈利）
      mockFetchFundNav.mockResolvedValue({
        fundcode: '000001', name: '波动基金', gsz: '2.400'
      })
      await store.refreshAllFunds()

      expect(store.totalProfit).toBe(400)
      expect(store.totalProfitRate).toBeCloseTo(20, 1)
    })
  })
})
