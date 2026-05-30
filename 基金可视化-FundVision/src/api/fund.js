/**
 * 天天基金 API 服务
 * - 通过 Vite 代理（/fundapi）获取基金实时净值
 * - 处理 JSONP 响应格式：jsonpgz({...})
 */
import axios from 'axios'

/** 创建 axios 实例，baseURL 指向 Vite 代理路径 */
const http = axios.create({
  baseURL: '/fundapi',
  timeout: 10000
})

/**
 * 获取单只基金的实时净值信息
 * 天天基金接口返回 JSONP 文本：jsonpgz({ ... })
 * @param {string} code - 6 位基金代码，如 "000001"
 * @returns {Promise<object>} 解析后的基金净值数据
 */
export async function fetchFundInfo(code) {
  const url = `/js/${code}.js`
  const resp = await http.get(url)

  // 解析 JSONP：去除 jsonpgz( 和末尾 )
  const text = typeof resp.data === 'string' ? resp.data : JSON.stringify(resp.data)
  const jsonStr = text.replace(/^jsonpgz\(/, '').replace(/\);?\s*$/, '')
  const raw = JSON.parse(jsonStr)

  return {
    code: raw.fundcode,
    name: raw.name,
    navDate: raw.jzrq,                    // 净值日期
    nav: parseFloat(raw.dwjz) || 0,       // 单位净值（上一交易日）
    currentNav: parseFloat(raw.gsz) || parseFloat(raw.dwjz) || 0, // 实时估算净值（优先）
    estimateRate: parseFloat(raw.gszzl) || 0, // 估算涨幅（%）
    updateTime: raw.gztime || ''          // 估值时间
  }
}

/**
 * 批量获取多只基金的实时净值
 * 使用 allSettled 保证部分失败不影响整体
 * @param {string[]} codes - 基金代码数组
 * @returns {Promise<Map<string, object>>} 代码 → 净值数据的映射
 */
export async function fetchMultipleFunds(codes) {
  const results = await Promise.allSettled(
    codes.map(code => fetchFundInfo(code))
  )

  const map = new Map()
  results.forEach((result, idx) => {
    if (result.status === 'fulfilled') {
      map.set(codes[idx], result.value)
    } else {
      console.error(`[fund-api] 获取 ${codes[idx]} 失败:`, result.reason?.message)
    }
  })
  return map
}
