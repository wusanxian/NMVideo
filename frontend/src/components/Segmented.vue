<template>
  <!--
    等宽分段控件（设置页的「下载 / 偏好 / 环境」与「自动 / 仅代理 / 仅直连」共用）。
    variant="tabs" 走顶部大分段样式，默认走表单里的小分段样式。
    options: [{ value, label, disabled, hint }]
  -->
  <div :class="[variant === 'tabs' ? 'set-tabs' : 'set-seg', { grow }]" role="tablist">
    <button
      v-for="o in options"
      :key="o.value"
      type="button"
      role="tab"
      :class="{ active: o.value === modelValue }"
      :disabled="o.disabled"
      :title="o.hint || ''"
      :aria-selected="o.value === modelValue"
      @click="$emit('update:modelValue', o.value)"
    >{{ o.label }}</button>
  </div>
</template>

<script setup>
defineProps({
  modelValue: { type: [String, Number], default: '' },
  options: { type: Array, default: () => [] },
  grow: { type: Boolean, default: false },
  variant: { type: String, default: 'seg' },
})
defineEmits(['update:modelValue'])
</script>
