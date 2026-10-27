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
  { path: '/services', name: 'services', component: () => import('./views/ServicesSetupView.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
