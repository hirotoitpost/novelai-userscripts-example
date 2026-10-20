// 画像生成の設定の選択肢。画像生成ページとキャラ別データセットのページで共用する。

export const MODELS = [
  { value: 'nai-diffusion-5-full',      label: 'NAI Diffusion V5 Full' },
  { value: 'nai-diffusion-5-curated',   label: 'NAI Diffusion V5 Curated' },
  { value: 'nai-diffusion-4-5-full',    label: 'NAI Diffusion V4.5 Full' },
  { value: 'nai-diffusion-4-5-curated', label: 'NAI Diffusion V4.5 Curated' },
  { value: 'nai-diffusion-4-full',      label: 'NAI Diffusion V4 Full' },
  { value: 'nai-diffusion-4-curated',   label: 'NAI Diffusion V4 Curated' },
  { value: 'nai-diffusion-3',           label: 'NAI Diffusion V3' },
] as const

export const SIZES = [
  { value: 'portrait',        label: 'Portrait  (832×1216)',        width: 832,  height: 1216 },
  { value: 'landscape',       label: 'Landscape (1216×832)',        width: 1216, height: 832 },
  { value: 'square',          label: 'Square    (1024×1024)',       width: 1024, height: 1024 },
  { value: 'large_portrait',  label: 'Portrait Large (1024×1536)',  width: 1024, height: 1536 },
  { value: 'large_landscape', label: 'Landscape Large (1536×1024)', width: 1536, height: 1024 },
] as const

export const SAMPLERS = [
  { value: 'k_euler_ancestral',    label: 'Euler Ancestral' },
  { value: 'k_euler',              label: 'Euler' },
  { value: 'k_dpm_2',              label: 'DPM2' },
  { value: 'k_dpm_2_ancestral',    label: 'DPM2 Ancestral' },
  { value: 'k_dpmpp_2m',           label: 'DPM++ 2M' },
  { value: 'k_dpmpp_2s_ancestral', label: 'DPM++ 2S Ancestral' },
  { value: 'k_dpmpp_sde',          label: 'DPM++ SDE' },
  { value: 'ddim',                 label: 'DDIM' },
] as const

export const NOISE_SCHEDULES = ['karras', 'exponential', 'polyexponential'] as const

export const UC_PRESETS = [
  { value: 'none',        label: 'なし' },
  { value: 'light',       label: 'ライト' },
  { value: 'strong',      label: 'ストロング' },
  { value: 'human_focus', label: '人物重視' },
  { value: 'furry_focus', label: 'ファーリー重視' },
] as const
