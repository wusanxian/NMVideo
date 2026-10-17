import { defineStore } from 'pinia'
import { api } from '../api'

export const useProjectStore = defineStore('project', {
  state: () => ({
    projects: [],
    current: null,       // { ...project, scenes: [{...scene, shots: [...] }] }
    currentProjectId: localStorage.getItem('aivideo.currentProject') || '',
    loading: false,
    submitting: false,
  }),
  actions: {
    async fetchProjects() {
      this.loading = true
      try {
        this.projects = await api.projects()
      } finally {
        this.loading = false
      }
    },
    async createProject(name, description = '') {
      const p = await api.createProject({ name, description })
      await this.fetchProjects()
      return p
    },
    async saveProject(name, description) {
      const data = {}
      if (name !== undefined) data.name = name
      if (description !== undefined) data.description = description
      await api.updateProject(this.current.id, data)
      await this.refresh()
      await this.fetchProjects()
    },
    async removeProject(id) {
      await api.deleteProject(id)
      await this.fetchProjects()
    },
    async openProject(id) {
      this.loading = true
      try {
        this.current = await api.getProject(id)
        this.setCurrentProjectId(id)
      } finally {
        this.loading = false
      }
      return this.current
    },
    setCurrentProjectId(id) {
      this.currentProjectId = id
      if (id) localStorage.setItem('aivideo.currentProject', id)
      else localStorage.removeItem('aivideo.currentProject')
    },
    /** 侧栏/首页进入项目内页面前的守卫：没有当前项目时返回 false（调用方引导去作品列表） */
    hasCurrent() {
      return !!this.currentProjectId
    },
    async refresh() {
      if (this.current) {
        this.current = await api.getProject(this.current.id)
      }
    },
    clearCurrent() {
      this.current = null
    },

    // ---------- 场景 ----------
    async addScene(data = {}) {
      const sc = await api.createScene(this.current.id, { title: '新场景', ...data })
      await this.refresh()
      return sc
    },
    async saveScene(sceneId, data) {
      await api.updateScene(sceneId, data)
      await this.refresh()
    },
    async removeScene(sceneId) {
      await api.deleteScene(sceneId)
      await this.refresh()
    },

    // ---------- 分镜 ----------
    async addShot(sceneId, data = {}) {
      const sh = await api.createShot(sceneId, data)
      await this.refresh()
      return sh
    },
    async saveShot(shotId, data) {
      await api.updateShot(shotId, data)
      await this.refresh()
    },
    async removeShot(shotId) {
      await api.deleteShot(shotId)
      await this.refresh()
    },
    async reorderShots(sceneId, ids) {
      await api.reorderShots(sceneId, ids)
      await this.refresh()
    },

    // ---------- 生成 / 审核 ----------
    async generate(shotId, params) {
      this.submitting = true
      try {
        const job = await api.generateShot(shotId, params)
        await this.refresh()
        return job
      } finally {
        this.submitting = false
      }
    },
    async review(shotId, action, versionId = '', stage = 'video') {
      await api.reviewShot(shotId, action, versionId, '', stage)
      await this.refresh()
    },
    async adoptVersion(shotId, versionId) {
      await api.adoptVersion(shotId, versionId)
      await this.refresh()
    },
    async deleteVersion(shotId, versionId) {
      await api.deleteVersion(shotId, versionId)
      await this.refresh()
    },

    // ---------- 剧本 ----------
    async saveScript(text) {
      await api.saveScript(this.current.id, text)
      await this.refresh()
    },
    async importScript(text, mode = 'rebuild', extra = {}) {
      // extra：3A 预览绑定（expected_revision / content_hash / confirm）
      const r = await api.importScript(this.current.id, text, mode, extra)
      await this.refresh()
      return r
    },

    // ---------- 时间线 ----------
    async loadTimeline() {
      return api.timeline(this.current.id)
    },
    async syncTimeline() {
      const items = await api.syncTimeline(this.current.id)
      return items
    },
    async updateTimelineItem(itemId, data) {
      return api.updateTimelineItem(itemId, data)
    },
    async reorderTimeline(ids) {
      return api.reorderTimeline(this.current.id, ids)
    },
    async removeTimelineItem(itemId) {
      await api.removeTimelineItem(itemId)
    },

    // ---------- 导出 ----------
    async createExport() {
      const exp = await api.createExport(this.current.id)
      return exp
    },
    async pollExport(id) {
      return api.getExport(id)
    },
  },
})
