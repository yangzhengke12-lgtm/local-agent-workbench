/**
 * storage.js 单元测试
 *
 * 测试 localStorage 封装工具的三个导出函数：
 *   getItem / setItem / removeItem
 *
 * 运行方式：
 *   npx vitest run tests/unit/storage.test.js
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { getItem, setItem, removeItem } from '../../src/utils/storage.js'

// ===================== Mock localStorage =====================

/** @type {Record<string, string>} */
let storageMock = {}

// 使用 vi.stubGlobal 替换全局 localStorage
beforeEach(() => {
  storageMock = {}
  vi.stubGlobal('localStorage', {
    getItem: vi.fn((key) => storageMock[key] ?? null),
    setItem: vi.fn((key, value) => { storageMock[key] = String(value) }),
    removeItem: vi.fn((key) => { delete storageMock[key] })
  })
})

// ===================== 测试套件 =====================

describe('storage.js - localStorage 封装工具', () => {
  // ---------- getItem ----------
  describe('getItem', () => {
    it('当本地存储为空时，返回 null', () => {
      const result = getItem()
      expect(result).toBeNull()
    })

    it('当本地存储有合法 JSON 时，返回解析后的对象', () => {
      const data = [{ code: '000001', name: '测试基金', shares: 100 }]
      storageMock['fund-vision-data'] = JSON.stringify(data)

      const result = getItem()
      expect(result).toEqual(data)
    })

    it('当本地存储有损坏的 JSON 时，返回 null 且不抛出异常', () => {
      storageMock['fund-vision-data'] = '{broken json!!!'

      const result = getItem()
      expect(result).toBeNull()
    })

    it('支持自定义 key 参数读取', () => {
      const customData = { hello: 'world' }
      storageMock['my-custom-key'] = JSON.stringify(customData)

      const result = getItem('my-custom-key')
      expect(result).toEqual(customData)
    })
  })

  // ---------- setItem ----------
  describe('setItem', () => {
    it('将对象序列化为 JSON 后存入 localStorage', () => {
      const data = [{ code: '000001', shares: 100 }]
      setItem(data)

      expect(localStorage.setItem).toHaveBeenCalledWith(
        'fund-vision-data',
        JSON.stringify(data)
      )
    })

    it('支持自定义 key 参数写入', () => {
      const data = { custom: true }
      setItem(data, 'custom-key')

      expect(localStorage.setItem).toHaveBeenCalledWith(
        'custom-key',
        JSON.stringify(data)
      )
    })

    it('写入空数组时正常工作', () => {
      setItem([])

      expect(storageMock['fund-vision-data']).toBe('[]')
    })
  })

  // ---------- removeItem ----------
  describe('removeItem', () => {
    it('移除指定 key 的数据', () => {
      storageMock['fund-vision-data'] = 'some value'
      removeItem()

      expect(localStorage.removeItem).toHaveBeenCalledWith('fund-vision-data')
    })

    it('移除不存在的 key 时不抛出异常', () => {
      expect(() => removeItem('non-existent-key')).not.toThrow()
    })
  })

  // ---------- 端到端流程 ----------
  describe('完整存取流程', () => {
    it('setItem → getItem 往返一致', () => {
      const data = [
        { code: '000001', name: '基金A', shares: 100, costNav: 1.5, currentNav: 1.8, type: '指数型', profit: 30, profitRate: 20 }
      ]
      setItem(data)
      const result = getItem()
      expect(result).toEqual(data)
    })

    it('setItem → removeItem → getItem 返回 null', () => {
      setItem([{ code: '000001' }])
      removeItem()
      const result = getItem()
      expect(result).toBeNull()
    })
  })
})
