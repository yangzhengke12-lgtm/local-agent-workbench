/**
 * fund.js Pinia Store 单元测试
 *
 * 测试核心状态管理的所有 actions、getters 和辅助逻辑。
 * 需要先安装：
 *   npm install -D vitest @pinia/testing
 *
 * 运行方式：
 *   npx vitest run tests/unit/fund-store.test.js
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// ===================== Mock API 模块 =====================

/**
 * 模拟 fetchFundNav 返回的基金数据
 * 通过 vi.mock 拦截 import，让 store 调用的是 mock 版本
 */
const mockFetchFundNav = vi.fn()

vi.mock('../../src/api/fund.js', () => ({
  fetchFundNav: (...args) => mockFetchFundNav(...args)
}))

// ===================== Mock localStorage =====================

beforeEach(() => {
  vi.stubGlobal('localStorage', {
    getItem: vi.fn(() => null),
    setItem: vi.fn(),
    removeItem: vi.fn()
  })
})

// ===================== 导入（必须在 vi.mock 之后） =====================

/** @type {ReturnType<typeof import('../../src/stores/fund.js').useFundStore>} */
import { useFundStore } from '../../src/stores/fund.js'

// ===================== 辅助函数 =====================

/** 创建全新的 Pinia 实例和 Store */
function createStore() {
  const pinia = createPinia()
  setActivePinia(pinia)
  return useFundStore()
}

/**
 * 模拟一次成功的 API 返回
 * @returns {Promise<object>} 基金 API 数据
 */
function mockApiSuccess(overrides = {}) {
  const data = {
    fundcode: '000001',
    name: '华夏成长',
    gsz: '1.8000',
    dwjz: '1.7500',
    gszzl: '0.56',
    gztime: '2025-01-15 15:00:00',
    ...overrides
  }
  mockFetchFundNav.mockResolvedValue(data)
  return data
}

/**
 * 模拟 API 调用失败
 */
function mockApiFailure(message = 'Network Error') {
  mockFetchFundNav.mockRejectedValue(new Error(message))
}

// ===================== 测试套件 =====================

describe('fund.js - Pinia Store 核心状态管理', () => {
  // 每个测试前重置
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ========== 初始状态 ==========
  describe('初始状态', () => {
    it('funds 数组初始为空', () => {
      const store = createStore()
      expect(store.funds).toEqual([])
    })

    it('loading 初始为 false', () => {
      const store = createStore()
      expect(store.loading).toBe(false)
    })

    it('所有 getters 初始返回 0 或空', () => {
      const store = createStore()
      expect(store.totalAssets).toBe(0)
      expect(store.totalProfit).toBe(0)
      expect(store.totalProfitRate).toBe(0)
      expect(store.typeDistribution).toEqual([])
    })
  })

  // ========== addFund ==========
  describe('addFund - 添加基金', () => {
    it('成功添加一只基金，并自动计算收益和收益率', async () => {
      const store = createStore()
      mockApiSuccess({ fundcode: '000001', name: '测试基金', gsz: '2.000' })

      await store.addFund('000001', 100, 1.500)

      expect(store.funds).toHaveLength(1)
      const fund = store.funds[0]
      expect(fund.code).toBe('000001')
      expect(fund.name).toBe('测试基金')
      expect(fund.shares).toBe(100)
      expect(fund.costNav).toBe(1.5)
      expect(fund.currentNav).toBe(2.0)
      // 收益 = (2.0 - 1.5) × 100 = 50
      expect(fund.profit).toBe(50)
      // 收益率 = (2.0 - 1.5) / 1.5 × 100 = 33.33...
      expect(fund.profitRate).toBeCloseTo(33.333, 1)
    })

    it('添加基金后自动保存到 localStorage', async () => {
      const store = createStore()
      mockApiSuccess()

      await store.addFund('000001', 100, 1.5)

      expect(localStorage.setItem).toHaveBeenCalled()
    })

    it('重复添加相同代码的基金时抛出错误', async () => {
      const store = createStore()
      mockApiSuccess()

      await store.addFund('000001', 100, 1.5)
      // 第二次添加相同代码
      await expect(store.addFund('000001', 200, 2.0)).rejects.toThrow('已在持仓列表中')
    })

    it('API 失败时 loading 状态正确重置', async () => {
      const store = createStore()
      mockApiFailure()

      try { await store.addFund('000001', 100, 1.5) } catch {}

      expect(store.loading).toBe(false)
    })

    it('自动根据代码前缀判断基金类型', async () => {
      const store = createStore()
      mockApiSuccess({ fundcode: '270001', name: '债券基金', gsz: '1.200' })

      await store.addFund('270001', 100, 1.0)

      expect(store.funds[0].type).toBe('债券型') // 2xxxxx
    })
  })

  // ========== 基金类型判断 ==========
  describe('基金类型自动判断 (addFund 间接测试)', () => {
    const typeCases = [
      { code: '000001', expectedType: '指数型' },
      { code: '110022', expectedType: '股票型' },
      { code: '270048', expectedType: '债券型' },
      { code: '400030', expectedType: '货币型' },
      { code: '519066', expectedType: '混合型' },
      { code: '600001', expectedType: '其他' },
    ]

    typeCases.forEach(({ code, expectedType }) => {
      it(`基金代码 ${code} 应被判断为「${expectedType}」`, async () => {
        const store = createStore()
        mockApiSuccess({ fundcode: code, gsz: '1.000' })

        await store.addFund(code, 100, 1.0)

        expect(store.funds[0].type).toBe(expectedType)
      })
    })
  })

  // ========== removeFund ==========
  describe('removeFund - 删除基金', () => {
    it('根据代码删除指定基金', async () => {
      const store = createStore()
      mockApiSuccess({ fundcode: '000001', gsz: '1.500' })
      await store.addFund('000001', 100, 1.0)

      store.removeFund('000001')

      expect(store.funds).toHaveLength(0)
    })

    it('删除不存在的基金时不抛出异常', async () => {
      const store = createStore()

      expect(() => store.removeFund('nonexistent')).not.toThrow()
    })

    it('删除一只基金后不影响其他基金', async () => {
      const store = createStore()

      mockApiSuccess({ fundcode: '000001', name: '基金A', gsz: '1.0' })
      await store.addFund('000001', 100, 1.0)

      mockApiSuccess({ fundcode: '000002', name: '基金B', gsz: '2.0' })
      await store.addFund('000002', 200, 1.5)

      store.removeFund('000001')

      expect(store.funds).toHaveLength(1)
      expect(store.funds[0].code).toBe('000002')
    })

    it('删除后触发 localStorage 保存', async () => {
      const store = createStore()
      mockApiSuccess({ fundcode: '000001', gsz: '1.0' })
      await store.addFund('000001', 100, 1.0)

      vi.clearAllMocks() // 清除 addFund 的 setItem 调用记录
      store.removeFund('000001')

      expect(localStorage.setItem).toHaveBeenCalled()
    })
  })

  // ========== refreshAllFunds ==========
  describe('refreshAllFunds - 刷新所有基金净值', () => {
    it('刷新所有基金的当前净值和收益', async () => {
      const store = createStore()

      // 先添加两只基金
      mockApiSuccess({ fundcode: '000001', name: '基金A', gsz: '1.200' })
      await store.addFund('000001', 100, 1.0) // profit: (1.2-1.0)*100=20

      mockApiSuccess({ fundcode: '000002', name: '基金B', gsz: '1.100' })
      await store.addFund('000002', 200, 1.0) // profit: (1.1-1.0)*200=20

      // 模拟刷新时 API 返回新净值（涨了）
      mockApiSuccess({ fundcode: '000001', name: '基金A', gsz: '1.500' })
      mockApiSuccess({ fundcode: '000002', name: '基金B', gsz: '1.300' })

      await store.refreshAllFunds()

      // 基金A: (1.5-1.0)*100 = 50
      expect(store.funds[0].currentNav).toBe(1.5)
      expect(store.funds[0].profit).toBe(50)

      // 基金B: (1.3-1.0)*200 = 60
      expect(store.funds[1].currentNav).toBe(1.3)
      expect(store.funds[1].profit).toBe(60)
    })

    it('单只基金刷新失败不影响其他基金', async () => {
      const store = createStore()

      mockApiSuccess({ fundcode: '000001', name: '基金A', gsz: '1.200' })
      await store.addFund('000001', 100, 1.0)

      mockApiSuccess({ fundcode: '000002', name: '基金B', gsz: '1.100' })
      await store.addFund('000002', 200, 1.0)

      // 第一次调用（基金A）成功
      mockApiSuccess({ fundcode: '000001', name: '基金A', gsz: '1.500' })
      // 第二次调用（基金B）失败
      mockFetchFundNav
        .mockResolvedValueOnce({ fundcode: '000001', name: '基金A', gsz: '1.500', dwjz: '1.450' })
        .mockRejectedValueOnce(new Error('Timeout'))

      await store.refreshAllFunds()

      // 基金A 应被成功刷新
      expect(store.funds[0].currentNav).toBe(1.5)
      // 基金B 保持原值不变
      expect(store.funds[1].currentNav).toBe(1.1)
    })

    it('刷新后自动保存到 localStorage', async () => {
      const store = createStore()
      mockApiSuccess({ fundcode: '000001', gsz: '1.200' })
      await store.addFund('000001', 100, 1.0)

      vi.clearAllMocks()
      mockApiSuccess({ fundcode: '000001', gsz: '1.500' })
      await store.refreshAllFunds()

      expect(localStorage.setItem).toHaveBeenCalled()
    })
  })

  // ========== Getters ==========
  describe('Getters - 计算属性', () => {
    it('totalAssets: 正确计算总资产（∑ currentNav × shares）', async () => {
      const store = createStore()

      mockApiSuccess({ gsz: '2.0' })
      await store.addFund('000001', 100, 1.5) // 资产 = 2.0 × 100 = 200

      mockApiSuccess({ gsz: '3.0' })
      await store.addFund('000002', 50, 2.0)  // 资产 = 3.0 × 50 = 150

      expect(store.totalAssets).toBe(350)
    })

    it('totalProfit: 正确计算总收益（∑ (currentNav - costNav) × shares）', async () => {
      const store = createStore()

      mockApiSuccess({ gsz: '2.0' })
      await store.addFund('000001', 100, 1.5) // (2.0-1.5)*100 = 50

      mockApiSuccess({ gsz: '1.5' })
      await store.addFund('000002', 200, 2.0) // (1.5-2.0)*200 = -100

      expect(store.totalProfit).toBe(-50)
    })

    it('totalProfitRate: 正确计算总收益率', async () => {
      const store = createStore()

      mockApiSuccess({ gsz: '2.0' })
      await store.addFund('000001', 100, 1.0) // cost: 100, profit: 100

      // 总成本: 100, 总收益: 100, 收益率: 100%
      expect(store.totalProfitRate).toBeCloseTo(100, 1)
    })

    it('totalProfitRate: 总成本为 0 时返回 0', () => {
      const store = createStore()
      // funds 为空，总成本为 0
      expect(store.totalProfitRate).toBe(0)
    })

    it('typeDistribution: 按基金类型正确聚合资产', async () => {
      const store = createStore()

      // 添加一只股票型（1xxxxx）
      mockApiSuccess({ fundcode: '110022', gsz: '2.0' })
      await store.addFund('110022', 100, 1.0) // 资产: 200

      // 添加一只债券型（2xxxxx）
      mockApiSuccess({ fundcode: '270048', gsz: '1.5' })
      await store.addFund('270048', 200, 1.0) // 资产: 300

      const dist = store.typeDistribution
      expect(dist).toHaveLength(2)

      const stockType = dist.find((d) => d.type === '股票型')
      const bondType = dist.find((d) => d.type === '债券型')
      expect(stockType.value).toBe(200)
      expect(bondType.value).toBe(300)
    })
  })

  // ========== 数据持久化 ==========
  describe('loadFromStorage / saveToStorage', () => {
    it('loadFromStorage: 从 localStorage 加载数据并恢复到 funds', () => {
      const store = createStore()
      const savedData = [
        { code: '000001', name: '已保存基金', type: '指数型', shares: 100, costNav: 1.2, currentNav: 1.5, profit: 30, profitRate: 25 }
      ]
      localStorage.getItem.mockReturnValue(JSON.stringify(savedData))

      store.loadFromStorage()

      expect(store.funds).toEqual(savedData)
    })

    it('loadFromStorage: localStorage 为空时 funds 保持空', () => {
      const store = createStore()
      localStorage.getItem.mockReturnValue(null)

      store.loadFromStorage()

      expect(store.funds).toEqual([])
    })

    it('loadFromStorage: 数据格式不正确时不会崩溃', () => {
      const store = createStore()
      localStorage.getItem.mockReturnValue('not an array')

      store.loadFromStorage()

      // 应安全处理，funds 保持为空
      expect(store.funds).toEqual([])
    })

    it('saveToStorage: 将当前 funds 序列化后保存', async () => {
      const store = createStore()
      mockApiSuccess({ fundcode: '000001', gsz: '1.800' })
      await store.addFund('000001', 100, 1.5)

      const callArg = localStorage.setItem.mock.calls.at(-1)?.[1]
      const saved = JSON.parse(callArg)
      expect(saved).toHaveLength(1)
      expect(saved[0].code).toBe('000001')
    })
  })
})
