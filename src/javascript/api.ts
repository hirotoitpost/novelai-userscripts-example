const BASE = ''  // Vite proxy が /api/ をバックエンドへ転送

export async function apiFetch<T>(
  token: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: body !== undefined ? 'POST' : 'GET',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  const data = await res.json().catch(() => ({ detail: res.statusText }))
  if (!res.ok) {
    throw new Error(data.detail ?? `HTTP ${res.status}`)
  }
  return data as T
}

export interface GenerateRequest {
  prompt: string
  negative_prompt?: string
  model: string
  size: string | [number, number]
  steps: number
  scale: number
  seed?: number
  quality: boolean
  uc_preset: string
  n_samples: number
}

export interface GenerateResponse {
  images: string[]
  format: string
}

export interface AnlasEstimateRequest {
  params: GenerateRequest
  is_opus?: boolean
}

export interface AnlasEstimateResponse {
  model: string
  total_anlas: number
  base_anlas: number
  character_reference_anlas: number
  vibe_encoding_anlas: number
  vibe_reference_anlas: number
  per_image_anlas: number
  requested_samples: number
  billable_samples: number
  strength_factor: number
  opus_discount_applied: boolean
}
