/**
 * FundVision 应用入口
 * - 注册 Pinia 状态管理
 * - 注册 Ant Design Vue 组件库（中文）
 * - 将 ECharts 挂载到全局属性
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import Antd from 'ant-design-vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import 'ant-design-vue/dist/reset.css'
import * as echarts from 'echarts'

import App from './App.vue'
import './style.css'

const app = createApp(App)

// 1. Pinia 状态管理
app.use(createPinia())

// 2. Ant Design Vue（中文语言包）
app.use(Antd, { locale: zhCN })

// 3. ECharts 全局挂载（组件中通过 $echarts 访问）
app.config.globalProperties.$echarts = echarts

app.mount('#app')
