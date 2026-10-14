import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/generate' },
  { path: '/generate', name: 'generate', component: () => import('./views/GenerateView.vue') },
  { path: '/queue', name: 'queue', component: () => import('./views/QueueView.vue') },
  { path: '/gallery', name: 'gallery', component: () => import('./views/GalleryView.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
