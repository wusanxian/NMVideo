<template>
  <div class="page settings">
    <header class="page-head">
      <div>
        <h1>设置</h1>
        <div class="page-sub">模型服务、目录与运行环境、界面与更新，都在这一页。</div>
      </div>
      <router-link to="/projects" class="btn">← 返回工作台</router-link>
    </header>

    <!-- 顶部等宽分段 tab（对齐参考图，选中项亮一档） -->
    <Segmented v-model="tab" :options="tabs" variant="tabs" />

    <div class="set-lead">{{ lead }}</div>

    <ServicesPanel v-if="tab === 'services'" />
    <EnvPanel v-else-if="tab === 'env'" :launch="launch" @refresh="loadLaunch" />
    <PrefsPanel v-else :launch="launch" @refresh="loadLaunch" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import Segmented from '../components/Segmented.vue'
import ServicesPanel from '../components/settings/ServicesPanel.vue'
import EnvPanel from '../components/settings/EnvPanel.vue'
import PrefsPanel from '../components/settings/PrefsPanel.vue'

const route = useRoute()
const router = useRouter()

const TABS = ['services', 'env', 'prefs']
const tabs = [
  { value: 'services', label: '服务' },
  { value: 'env', label: '环境' },
  { value: 'prefs', label: '偏好' },
]

// 兼容旧链接：/settings?tab=comfy 之类的写法一律归到 env
function normalize(v) {
  if (v === 'comfy' || v === 'env') return 'env'
  if (v === 'prefs' || v === 'preferences') return 'prefs'
  return 'services'
}

const tab = ref(normalize(route.query.tab))
const launch = ref(emptyLaunch())

const lead = computed(() => ({
  services: '四类模型服务各自独立检测；配置存在后端 settings.json，重启不丢。',
  env: '这里的路径与状态都来自本机实况，改完地址记得点保存。',
  prefs: '界面、更新与存储。版本号来自打包信息，不是写死的。',
}[tab.value]))

function emptyLaunch() {
  return {
    version: '', steps: [],
    comfyui: { url: '', online: false },
    paths: { models: {}, outputs: {}, db: '', project: '', backend: '' },
    disk: {}, hardware: {}, resource: {},
  }
}

async function loadLaunch() {
  try {
    const d = await api.launchStatus()
    // 旧后端没有 resource 字段时补空对象，模板里 already 全都兜底，这里只是少写 ?.
    launch.value = { ...emptyLaunch(), ...d, resource: d.resource || {} }
  } catch {
    /* 后端离线时保留空态，页面不白 */
  }
}

// tab 写回 URL：刷新/回退都能停在同一页
watch(tab, (v) => {
  if (route.query.tab === v) return
  router.replace({ query: { ...route.query, tab: v } })
})
watch(() => route.query.tab, (v) => {
  const next = normalize(v)
  if (next !== tab.value) tab.value = next
})

onMounted(loadLaunch)
</script>

<style scoped>
.settings :deep(.set-tabs) { margin-bottom: 0; }
.page-head { margin-bottom: 16px; }
</style>
