import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'home', component: () => import('./views/HomeView.vue') },
  { path: '/launch', name: 'launch', component: () => import('./views/LaunchView.vue') },
  { path: '/generate', name: 'generate', component: () => import('./views/GenerateView.vue') },
  { path: '/queue', name: 'queue', component: () => import('./views/QueueView.vue') },
  { path: '/gallery', name: 'gallery', component: () => import('./views/GalleryView.vue') },
  { path: '/projects', name: 'projects', component: () => import('./views/ProjectsView.vue') },
  { path: '/novel', name: 'novel', component: () => import('./views/NovelAdaptationView.vue') },
  { path: '/projects/:id', name: 'project', component: () => import('./views/ProjectView.vue'), props: true },
  { path: '/comfy', name: 'comfy', component: () => import('./views/ComfySetupView.vue') },
  // 设置页：服务 / 环境 / 偏好三段（版式对齐 FreeVideo 设置弹窗）
  { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
  // 老链接不断：原来「模型服务」页的入口统一并进设置页
  { path: '/services', redirect: (to) => ({ path: '/settings', query: { ...to.query, tab: 'services' } }) },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
