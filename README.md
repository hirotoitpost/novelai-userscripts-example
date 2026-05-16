# NovelAI Userscripts Example

English | [日本語](docs/README_jp.md)

A full-stack UI application for NovelAI image generation, built on top of the [novelai-sdk](https://github.com/caru-ini/novelai-sdk).  
Login with your NovelAI account and generate images directly from your browser.

> **Disclaimer:** This project is an independent, personal work and is in no way affiliated with, endorsed by, or officially connected to the developers of [novelai-sdk](https://github.com/caru-ini/novelai-sdk) or NovelAI Inc.

## Features

| Status | Feature |
|--------|---------|
| ✅ | NovelAI account login (email + password) |
| ✅ | Image generation with full parameter control |
| ✅ | Settings persistence (localStorage) |
| ✅ | Anlas cost preview before generation |
| ✅ | Streaming generation with live intermediate preview (SSE) |
| ✅ | Anlas consumption estimation (backend) |
| ✅ | Image metadata extraction / erasure |
| ✅ | Metadata management UI |
| ✅ | Image-to-Image mode |
| ✅ | AI assistant (LLM-powered prompt tools via vLLM) |
| 🔄 | Inpainting (planned) |
| 🔄 | Character Reference UI (planned) |
| 🔄 | Vibe Transfer / ControlNet UI (planned) |

## Requirements

- **Python**: 3.10 or higher
- **Node.js**: 20 or higher
- **uv**: Latest (Python package manager)
- **Docker + NVIDIA GPU** (optional): Required for the AI assistant (vLLM)

## Quick Start

### 1. Clone & install dependencies

```bash
git clone https://github.com/hirotoitpost/novelai-userscripts-example.git
cd novelai-userscripts-example

# Python dependencies (creates .venv automatically)
uv sync

# Node.js dependencies
npm install
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env if needed — no API token required for email/password login
```

To enable the AI assistant, also add the following to `.env`:

```
VLLM_BASE_URL=http://localhost:8001/v1
VLLM_MODEL=Qwen/Qwen2-VL-7B-Instruct
VLLM_VISION_BASE_URL=http://localhost:8001/v1
VLLM_VISION_MODEL=Qwen/Qwen2-VL-7B-Instruct
HF_TOKEN=hf_...   # Hugging Face token for model download
```

### 3. Start services

Open **two terminals**:

```bash
# Terminal 1 — FastAPI backend (port 8000)
uv run python -m uvicorn python.server:app --app-dir src --host 127.0.0.1 --port 8000 --reload

# Terminal 2 — Vite frontend (port 5173)
npm run dev

# Terminal 3 (optional) — vLLM server for AI assistant (requires NVIDIA GPU + Docker)
docker compose up -d
```

Open **http://localhost:5173** in your browser.

### 4. Log in

Enter your NovelAI account **email** and **password**.  
The backend derives the access key using argon2id (same algorithm as the official client) and authenticates with NovelAI — your raw password is never stored.

> **Alternative:** Set `NOVELAI_API_TOKEN=pst-...` in `.env` to skip the login screen and use a [persistent API token](https://docs.novelai.net) instead.

## Usage

### Image Generation

1. Click **「画像生成」** on the home screen
2. Enter a prompt in the left sidebar
3. Adjust parameters (model, size, steps, scale, seed…)
4. Check the **estimated Anlas cost** shown below the generate button
5. Click **「生成する」** or press **Ctrl+Enter**
6. Intermediate preview frames appear live as the model generates each step
7. The final image appears on the right — use the **Download** button to save it

> All prompt and parameter settings are automatically saved to `localStorage` and
> restored on your next visit.

### Prompt & Image Privacy

| Data | Where it lives | Persisted? |
|------|---------------|------------|
| Prompt text | React state (browser memory) | No |
| Generated image | Browser memory (base64) | No — download to save |
| Access token | `localStorage` (browser only) | Until logout |
| Email / Password | Used once for key derivation, then discarded | Never |

## Development

```bash
# Format code
uv run poe fmt

# Lint
uv run poe lint

# Type check
uv run poe check

# Run tests
uv run pytest
```

### Commit Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
{feat|fix|docs|style|refactor|test|chore}: Short description
```

## Project Structure

```
.
├── src/
│   ├── python/                  # FastAPI backend
│   │   ├── server.py            # App entry point
│   │   ├── client.py            # NovelAI client (Bearer token per-request)
│   │   ├── auth_utils.py        # argon2id key derivation + login
│   │   ├── models.py            # Pydantic request/response models
│   │   └── routes/
│   │       ├── auth.py          # POST /api/auth/login
│   │       ├── image.py         # POST /api/image/generate (+ stream, anlas)
│   │       └── metadata.py      # POST /api/metadata/extract|erase
│   └── javascript/              # React + TypeScript frontend
│       ├── api.ts               # Authenticated fetch utility
│       ├── App.tsx              # Router setup
│       ├── context/
│       │   └── AuthContext.tsx  # Login state (localStorage)
│       └── pages/
│           ├── Login.tsx/css    # Email + password login screen
│           ├── Home.tsx/css     # Dashboard
│           └── ImageGenerate.tsx/css  # Image generation UI
├── scripts/
│   └── generate_cat_garden.py  # Standalone SDK usage example
├── tests/                       # pytest test files
├── docs/                        # Additional documentation
│   ├── README_jp.md             # Japanese README
│   └── architecture.md          # System architecture & API reference
├── index.html                   # Vite entry point
├── vite.config.ts               # Vite config (API proxy to :8000)
├── pyproject.toml               # Python dependencies & tools
├── package.json                 # Node.js dependencies
└── .env.example                 # Environment variable template
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/login` | Email + password → access token |
| `POST` | `/api/image/generate` | Generate image(s) |
| `POST` | `/api/image/generate/stream` | Streaming generation (SSE) |
| `POST` | `/api/image/anlas` | Estimate Anlas cost |
| `POST` | `/api/metadata/extract` | Extract image metadata |
| `POST` | `/api/metadata/erase` | Erase image metadata |
| `POST` | `/api/llm/prompt-format` | Format prompt with LLM (SSE) |
| `POST` | `/api/llm/char-gen` | Generate character tags (SSE) |
| `POST` | `/api/llm/story-draft` | Generate story draft (SSE) |
| `POST` | `/api/llm/aux-text` | Generate positive/negative pair (SSE) |
| `POST` | `/api/llm/metadata-gen` | Generate full generation params (SSE) |
| `POST` | `/api/llm/reverse-prompt` | Reverse prompt from image (SSE) |

Full interactive docs available at **http://localhost:8000/docs** while the backend is running.

## License

MIT — see [LICENSE](LICENSE) for details.

## Related Projects

- [novelai-sdk](https://github.com/caru-ini/novelai-sdk) – Community Python SDK (third-party)
- [novelai-image-metadata](https://github.com/NovelAI/novelai-image-metadata) – Metadata tools
- [novelai-script-examples](https://github.com/NovelAI/novelai-script-examples) – Script examples
