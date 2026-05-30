/**
 * localStorage 封装工具
 * - 自动 JSON 序列化 / 反序列化
 * - 统一键名前缀，避免冲突
 * - 完善的异常处理
 */

const STORAGE_PREFIX = 'fund-vision_'

/**
 * 从 localStorage 读取并解析 JSON
 * @param {string} key - 键名（自动添加前缀）
 * @param {*} defaultValue - 读取失败时返回的默认值
 * @returns {*} 解析后的值
 */
export function getItem(key, defaultValue = null) {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + key)
    if (raw === null) return defaultValue
    return JSON.parse(raw)
  } catch (e) {
    console.error(`[storage] 读取 "${key}" 失败:`, e.message)
    return defaultValue
  }
}

/**
 * 将值 JSON 序列化后写入 localStorage
 * @param {string} key - 键名（自动添加前缀）
 * @param {*} value - 要存储的值
 */
export function setItem(key, value) {
  try {
    localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(value))
    return true
  } catch (e) {
    console.error(`[storage] 写入 "${key}" 失败:`, e.message)
    return false
  }
}

/**
 * 从 localStorage 中移除指定键
 * @param {string} key - 键名（自动添加前缀）
 */
export function removeItem(key) {
  try {
    localStorage.removeItem(STORAGE_PREFIX + key)
  } catch (e) {
    console.error(`[storage] 删除 "${key}" 失败:`, e.message)
  }
}
