/**
 * fund.js API 单元测试
 *
 * 测试天天基金 API 封装：
 *   parseJsonp - JSONP 文本解析（私有函数，通过 fetchFundNav 间接测试）
 *   fetchFundNav - API 调用封装
 *
 * 运行方式：
 *   npx vitest run tests/unit/fund-api.test.js
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

// ===================== 每次重新导入以获取干净的 module =====================
/** @type {typeof import('../../src/api/fund.js')} */
let fundApi

beforeEach(async () => {
  // 清除模块缓存，确保每次测试获得全新实例
  vi.resetModules()
  fundApi = await import('../../src/api/fund.js')
})

// ===================== 工具函数：模拟 axios 响应 =====================

/**
 * 创建一个模拟的 axios 实例对象
 * @param {Function} mockGet - 模拟的 get 方法
 * @returns {object} 模拟 axios 实例
 */
function mockAxiosInstance(mockGet) {
  return {
    get: mockGet,
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() }
    }
  }
}

// ===================== 测试套件 =====================

describe('fund.js - 天天基金 API 封装', () => {
  // ---------- JSONP 解析（通过 fetchFundNav 间接测试）----------
  describe('parseJsonp（JSONP 响应解析）', () => {
    it('正确解析标准 JSONP 响应（带分号）', async () => {
      const mockGet = vi.fn().mockResolvedValue({
        data: 'jsonpgz({"fundcode":"000001","name":"华夏成长","dwjz":"1.2345","gsz":"1.2400","gszzl":"0.45","gztime":"2025-01-15 15:00:00"});'
      })
      vi.doMock('axios', () => ({
        default: { create: vi.fn(() => mockAxiosInstance(mockGet)) }
      }))

      const { fetchFundNav } = await import('../../src/api/fund.js')
      const result = await fetchFundNav('000001')

      expect(result).toEqual({
        fundcode: '000001',
        name: '华夏成长',
        dwjz: '1.2345',
        gsz: '1.2400',
        gszzl: '0.45',
        gztime: '2025-01-15 15:00:00'
      })
    })

    it('正确解析不带分号的 JSONP 响应', async () => {
      const mockGet = vi.fn().mockResolvedValue({
        data: 'jsonpgz({"fundcode":"000002","name":"测试基金","gsz":"2.000"})'
      })
      vi.doMock('axios', () => ({
        default: { create: vi.fn(() => mockAxiosInstance(mockGet)) }
      }))

      const { fetchFundNav } = await import('../../src/api/fund.js')
      const result = await fetchFundNav('000002')

      expect(result.fundcode).toBe('000002')
      expect(result.name).toBe('测试基金')
    })

    it('JSONP 格式异常时抛出错误', async () => {
      const mockGet = vi.fn().mockResolvedValue({
        data: 'not jsonp format at all!!!!'
      })
      vi.doMock('axios', () => ({
        default: { create: vi.fn(() => mockAxiosInstance(mockGet)) }
      }))

      const { fetchFundNav } = await import('../../src/api/fund.js')

      await expect(fetchFundNav('000003')).rejects.toThrow('净值失败')
    })
  })

  // ---------- API 调用 ----------
  describe('fetchFundNav', () => {
    it('使用正确的 API URL 路径：/js/{fundCode}.js', async () => {
      const mockGet = vi.fn().mockResolvedValue({
        data: 'jsonpgz({"fundcode":"000001","name":"测试","gsz":"1.0"});'
      })
      vi.doMock('axios', () => ({
        default: { create: vi.fn(() => mockAxiosInstance(mockGet)) }
      }))

      const { fetchFundNav } = await import('../../src/api/fund.js')
      await fetchFundNav('000001')

      // 验证被调用时的 URL
      expect(mockGet).toHaveBeenCalledWith('/js/000001.js')
    })

    it('网络请求失败时抛出友好的错误信息', async () => {
      const mockGet = vi.fn().mockRejectedValue(new Error('Network Error'))
      vi.doMock('axios', () => ({
        default: { create: vi.fn(() => mockAxiosInstance(mockGet)) }
      }))

      const { fetchFundNav } = await import('../../src/api/fund.js')

      await expect(fetchFundNav('000001')).rejects.toThrow('请检查代码是否正确')
    })

    it('优先使用估值 gsz 作为净值数据', async () => {
      const mockGet = vi.fn().mockResolvedValue({
        data: 'jsonpgz({"fundcode":"000001","name":"测试","dwjz":"1.1000","gsz":"1.1200"});'
      })
      vi.doMock('axios', () => ({
        default: { create: vi.fn(() => mockAxiosInstance(mockGet)) }
      }))

      const { fetchFundNav } = await import('../../src/api/fund.js')
      const result = await fetchFundNav('000001')

      // gsz 应该被保留（store 那边会用它作为 currentNav）
      expect(result.gsz).toBe('1.1200')
      expect(result.dwjz).toBe('1.1000')
    })
  })
})
