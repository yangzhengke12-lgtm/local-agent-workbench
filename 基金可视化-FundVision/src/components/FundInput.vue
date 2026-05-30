<template>
  <!--
    FundInput - 基金录入表单
    使用 Ant Design Vue 的 form/input/input-number/button 组件
    支持输入基金代码、持有份额、成本净值
  -->
  <a-card title="➕ 添加基金" class="fund-input-card" :bordered="true">
    <a-form
      :model="formState"
      layout="inline"
      @finish="handleAdd"
      style="flex-wrap: wrap; gap: 8px;"
    >
      <a-form-item
        name="code"
        :rules="[{ required: true, pattern: /^\d{6}$/, message: '请输入6位基金代码' }]"
      >
        <a-input
          v-model:value="formState.code"
          placeholder="基金代码（6位）"
          :maxlength="6"
          style="width: 160px"
          allow-clear
        />
      </a-form-item>

      <a-form-item
        name="shares"
        :rules="[{ required: true, message: '请输入持有份额' }]"
      >
        <a-input-number
          v-model:value="formState.shares"
          placeholder="持有份额"
          :min="0.01"
          :step="100"
          style="width: 160px"
        />
      </a-form-item>

      <a-form-item
        name="costNav"
        :rules="[{ required: true, message: '请输入成本净值' }]"
      >
        <a-input-number
          v-model:value="formState.costNav"
          placeholder="成本净值"
          :min="0.0001"
          :step="0.01"
          style="width: 160px"
        />
      </a-form-item>

      <a-form-item>
        <a-button
          type="primary"
          html-type="submit"
          :loading="adding"
        >
          {{ adding ? '查询中…' : '添加基金' }}
        </a-button>
      </a-form-item>
    </a-form>
  </a-card>
</template>

<script setup>
/**
 * FundInput.vue —— 基金录入表单组件
 * 收集用户输入的基金信息并提交到 Pinia Store
 */
import { reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useFundStore } from '../stores/fund'

const store = useFundStore()
const adding = ref(false)

/** 表单数据 */
const formState = reactive({
  code: '',
  shares: null,
  costNav: null
})

/** 处理添加基金 */
async function handleAdd() {
  if (!formState.code || !formState.shares || !formState.costNav) {
    message.warning('请完整填写基金代码、持有份额和成本净值')
    return
  }

  adding.value = true
  try {
    await store.addFund(formState.code, formState.shares, formState.costNav)
    message.success(`基金 ${formState.code} 添加成功！`)
    // 清空表单
    formState.code = ''
    formState.shares = null
    formState.costNav = null
  } catch (err) {
    message.error(err.message || '添加失败，请检查基金代码是否正确')
  } finally {
    adding.value = false
  }
}
</script>

<style scoped>
.fund-input-card {
  margin-bottom: 16px;
}

.fund-input-card :deep(.ant-card-body) {
  padding: 16px 24px;
}
</style>
