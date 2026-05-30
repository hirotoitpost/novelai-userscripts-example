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

export interface I2iRequest {
  image: string
  strength?: number
  noise?: number
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
  i2i?: I2iRequest
}

export interface GenerateResponse {
  images: string[]
  format: string
}

export interface SubscriptionPerks {
  unlimitedImageGeneration: boolean
  imageGeneration: boolean
  voiceGeneration: boolean
  unlimitedMaxPriority: boolean
  maxPriorityActions: number
  startPriority: number
  moduleTrainingSteps: number
  contextTokens?: number
}

export interface SubscriptionResponse {
  tier: number         // 0=Free 1=Tablet 2=Scroll 3=Opus
  active: boolean
  expiresAt?: number
  perks: SubscriptionPerks
  trainingStepsLeft: {
    fixedTrainingStepsLeft: number
    purchasedTrainingSteps: number
  }
  accountType?: number
  isGracePeriod?: boolean
}

export interface MetadataExtractRequest {
  image: string
}

export interface MetadataExtractResponse {
  metadata: Record<string, unknown>
}

export interface MetadataEraseRequest {
  image: string
  target: 'alpha' | 'png_info' | 'both'
}

export interface MetadataEraseResponse {
  image: string
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

// ===== LLM feature types =====

export interface PromptFormatRequest {
  rough_prompt: string
  target_model?: string
}

export interface CharGenRequest {
  concept: string
  style?: string
}

export interface StoryDraftRequest {
  premise: string
  n_scenes?: number
}

export interface AuxTextRequest {
  concept: string
  target_model?: string
}

export interface MetadataGenRequest {
  concept: string
  target_model?: string
  size?: string
}

export interface ReversePromptRequest {
  image: string
}

// ===== Batch organize types =====

export interface BatchPreviewRequest {
  input_path: string
  similarity_threshold: number
}

export interface BatchFileInfo {
  path: string
  prompt: string
  date: string
  has_metadata: boolean
}

export interface BatchGroupInfo {
  group_name: string
  representative_prompt: string
  files: BatchFileInfo[]
  file_count: number
}

export interface BatchPreviewResponse {
  groups: BatchGroupInfo[]
  no_metadata_files: BatchFileInfo[]
  total_files: number
}

export interface BatchOrganizeRequest {
  input_path: string
  output_path: string
  operation: 'copy' | 'move' | 'clean_copy'
  similarity_threshold: number
  save_metadata_json: boolean
}

export interface BatchScanEvent {
  current: number
  total: number
  file: string
}

export interface BatchProgressEvent {
  current: number
  total: number
  file: string
  dest: string
  status: 'ok' | 'no_metadata' | 'error'
  message?: string
}

export interface BatchCompleteEvent {
  organized: number
  skipped_no_meta: number
  errors: number
}

export async function streamBatchOrganize(
  token: string,
  body: BatchOrganizeRequest,
  onScan: (e: BatchScanEvent) => void,
  onProgress: (e: BatchProgressEvent) => void,
  onComplete: (e: BatchCompleteEvent) => void,
  onError: (msg: string) => void,
): Promise<void> {
  const res = await fetch('/api/batch/organize', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  })

  if (!res.ok || !res.body) {
    const errData = await res.json().catch(() => ({ detail: res.statusText }))
    onError((errData as { detail?: string }).detail ?? `HTTP ${res.status}`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventType = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (line === '') {
        eventType = ''
      } else if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim()
      } else if (line.startsWith('data: ')) {
        let chunk: Record<string, unknown>
        try { chunk = JSON.parse(line.slice(6)) as Record<string, unknown> }
        catch { continue }

        if (eventType === 'error') {
          onError((chunk.detail as string | undefined) ?? 'エラー')
          return
        } else if (eventType === 'scan') {
          onScan(chunk as unknown as BatchScanEvent)
        } else if (eventType === 'progress') {
          onProgress(chunk as unknown as BatchProgressEvent)
        } else if (eventType === 'complete') {
          onComplete(chunk as unknown as BatchCompleteEvent)
        }
      }
    }
  }
}

// SSE streaming helper for LLM endpoints
// Handles: event: token | done | error
export async function streamLLM(
  token: string,
  path: string,
  body: unknown,
  onToken: (delta: string) => void,
  onDone: (fullText: string) => void,
  onError: (msg: string) => void,
): Promise<void> {
  const res = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  })

  if (!res.ok || !res.body) {
    const errData = await res.json().catch(() => ({ detail: res.statusText }))
    onError((errData as { detail?: string }).detail ?? `HTTP ${res.status}`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventType = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (line === '') {
        eventType = ''
      } else if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim()
      } else if (line.startsWith('data: ')) {
        let chunk: Record<string, unknown>
        try {
          chunk = JSON.parse(line.slice(6)) as Record<string, unknown>
        } catch {
          continue
        }
        if (eventType === 'error') {
          onError((chunk.detail as string | undefined) ?? 'ストリーミングエラー')
          return
        }
        if (eventType === 'token') {
          onToken((chunk.delta as string | undefined) ?? '')
        } else if (eventType === 'done') {
          onDone((chunk.full_text as string | undefined) ?? '')
        }
      }
    }
  }
}
