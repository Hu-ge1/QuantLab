const BASE = '/api'

async function request(path: string, options: RequestInit = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json()
}

export const gridApi = {
  list: () => request('/grid'),
  create: (body: Record<string, unknown>) => request('/grid', { method: 'POST', body: JSON.stringify(body) }),
  get: (id: number) => request(`/grid/${id}`),
  update: (id: number, body: Record<string, unknown>) => request(`/grid/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  remove: (id: number) => request(`/grid/${id}`, { method: 'DELETE' }),
  start: (id: number) => request(`/grid/${id}/start`, { method: 'POST' }),
  pause: (id: number) => request(`/grid/${id}/pause`, { method: 'POST' }),
  stop: (id: number) => request(`/grid/${id}/stop`, { method: 'POST' }),
  tick: (id: number, price: number) => request(`/grid/${id}/tick`, { method: 'POST', body: JSON.stringify({ price }) }),
  fills: (id: number, limit = 100) => request(`/grid/${id}/fills?limit=${limit}`),
  submitQmt: (id: number) => request(`/grid/${id}/submit-qmt`, { method: 'POST' }),
  td9Preview: (symbol: string) => request(`/grid/preview/td9/${encodeURIComponent(symbol)}`),
}
