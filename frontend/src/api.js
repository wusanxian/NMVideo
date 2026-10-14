const BASE = '/api'

async function request(path, options = {}) {
  const resp = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`
    try {
      const body = await resp.json()
      if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return resp.json()
}

export const api = {
  engines: () => request('/engines'),
  jobs: () => request('/jobs?limit=200'),
  createJob: (data) => request('/jobs', { method: 'POST', body: JSON.stringify(data) }),
  cancelJob: (id) => request(`/jobs/${id}/cancel`, { method: 'POST' }),
  deleteJob: (id) => request(`/jobs/${id}`, { method: 'DELETE' }),
  hardware: () => request('/hardware'),
  presets: () => request('/presets'),
  jobMetadata: (id) => request(`/jobs/${id}/metadata`),
}
