<template>
  <aside class="sidebar">
    <div class="logo">🎬 AI 视频工坊</div>
    <nav>
      <router-link to="/generate">✨ 生成视频</router-link>
      <router-link to="/queue">
        📋 任务队列
        <span v-if="activeCount" class="nav-badge">{{ activeCount }}</span>
      </router-link>
      <router-link to="/gallery">🖼 作品画廊</router-link>
    </nav>
    <div class="sidebar-footer">
      <div class="engine-status" v-for="e in engines" :key="e.id">
        <span class="dot" :class="e.status"></span>
        <span>{{ e.name }}</span>
      </div>
      <div class="engine-status" v-if="!engines.length" style="color: var(--red)">
        <span class="dot" style="background: var(--red)"></span>
        <span>后端未连接</span>
      </div>
    </div>
  </aside>
  <main class="main">
    <router-view />
  </main>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from './api'
import { useJobsStore } from './store/jobs'

const store = useJobsStore()
const engines = ref([])
const activeCount = computed(() => store.active.length)

onMounted(async () => {
  store.fetch()
  try {
    engines.value = await api.engines()
  } catch {
    engines.value = []
  }
})
</script>
