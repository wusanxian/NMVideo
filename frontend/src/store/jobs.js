import { defineStore } from 'pinia'
import { api } from '../api'

export const useJobsStore = defineStore('jobs', {
  state: () => ({
    jobs: [],
    loading: false,
    submitting: false,
  }),
  getters: {
    active: (s) => s.jobs.filter((j) => j.status === 'pending' || j.status === 'running'),
    done: (s) => s.jobs.filter((j) => j.status === 'done'),
  },
  actions: {
    async fetch() {
      this.loading = true
      try {
        this.jobs = await api.jobs()
      } finally {
        this.loading = false
      }
    },
    async submit(params) {
      this.submitting = true
      try {
        const job = await api.createJob(params)
        await this.fetch()
        return job
      } finally {
        this.submitting = false
      }
    },
    async cancel(id) {
      await api.cancelJob(id)
      await this.fetch()
    },
    async retry(id) {
      await api.retryJob(id)
      await this.fetch()
    },
    async remove(id) {
      await api.deleteJob(id)
      await this.fetch()
    },
  },
})
