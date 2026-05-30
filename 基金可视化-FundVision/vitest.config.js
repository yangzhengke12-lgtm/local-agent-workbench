/**
 * vitest 测试配置文件
 *
 * 与 Vite 原生集成，共享大部分配置。
 * 安装依赖后即可运行：
 *   npm install -D vitest @vue/test-utils jsdom
 *   npx vitest run
 */
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  test: {
    // 使用 jsdom 模拟浏览器环境（localStorage、DOM API 等）
    environment: 'jsdom',

    // 全局变量（无需在每个测试文件中手动 import describe/it/expect）
    globals: true,

    // 测试文件匹配模式
    include: ['tests/**/*.test.js'],

    // 排除目录
    exclude: ['node_modules', 'dist'],

    // 覆盖率配置（可选）
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      include: ['src/**/*.js'],
      exclude: ['src/main.js', 'src/App.vue']
    },

    // 路径别名（与 vite.config.js 保持一致）
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src')
      }
    }
  }
})
