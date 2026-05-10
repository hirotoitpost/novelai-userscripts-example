# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- Image-to-Image UI
- Inpainting UI
- Character Reference UI
- Vibe Transfer / ControlNet UI
- Metadata management UI
- Batch generation UI

---

## [0.3.0] - 2026-05-10

### Added

- **Image generation page** (`/generate`) with full parameter control
  - Prompt / negative prompt textareas
  - Model selector (V4.5 Full/Curated, V4 Full/Curated, V3)
  - Size selector (Portrait, Landscape, Square, Large variants)
  - Steps (1–50) and Scale (0–10) sliders
  - Seed input, UC preset selector, quality tag toggle
  - Ctrl+Enter keyboard shortcut to trigger generation
  - Download and re-generate buttons on result
- **`src/javascript/api.ts`** – authenticated `fetch` utility (Bearer token injection)
- **Home dashboard** now links to the image generation page

### Changed

- Home page: 画像生成 card is now clickable and navigates to `/generate`

---

## [0.2.0] - 2026-05-10

### Added

- **NovelAI account login** (email + password)
  - `src/python/auth_utils.py`: argon2id key derivation matching the official client
  - `POST /api/auth/login` endpoint (`src/python/routes/auth.py`)
- **React frontend** with routing (`react-router-dom`)
  - Login page with NovelAI dark theme
  - Home dashboard with feature overview
  - Protected routes (redirect to `/login` when unauthenticated)
- **Dynamic Bearer token support** – every API request uses the token from the
  `Authorization` header; falls back to `NOVELAI_API_TOKEN` env var
- **Vite proxy** – `/api/*` forwarded to `http://127.0.0.1:8000`
- `argon2-cffi` and `httpx` added to Python dependencies

### Security

- Prompts and generated images live in browser memory only; never persisted
- Raw password discarded immediately after key derivation
- Access token stored in `localStorage` (browser-local, cleared on logout)
- `outputs/` and `samples/` excluded from git (binary files / third-party HTML)

---

## [0.1.0] - 2026-05-06

### Added

- **FastAPI relay server** (`src/python/`) wrapping novelai-sdk
  - `POST /api/image/generate` – image generation
  - `POST /api/image/generate/stream` – SSE streaming generation
  - `POST /api/image/anlas` – Anlas consumption estimate
  - `POST /api/metadata/extract` – PNG metadata extraction
  - `POST /api/metadata/erase` – metadata erasure
- Pydantic v2 request/response models (`src/python/models.py`)
- React + TypeScript scaffold with Vite
- `scripts/generate_cat_garden.py` – standalone SDK usage example
- Project initialization: LICENSE, README, CONTRIBUTING, SECURITY
