import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * Vite 配置文件
 * - 配置开发服务器代理，解决天天基金 API 跨域问题
 * - 代理 /fundapi → https://fundgz.1234567.com.cn
 */
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    proxy: {
      '/fundapi': {
        target: 'https://fundgz.1234567.com.cn',
        changeOrigin: true,
        // 重写路径：去掉 /fundapi 前缀
        rewrite: (path) => path.replace(/^\/fundapi/, ''),
        secure: true,
        // 设置 Referer 头，避免被天天基金拦截
        headers: {
          Referer: 'https://fundgz.1234567.com.cn/'
        }
      }
    }
  }
})
