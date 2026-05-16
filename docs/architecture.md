# Architecture

## Overview

```
Browser (localhost:5173)
    │
    │  /api/* (Vite proxy)
    ▼
FastAPI Backend (localhost:8000)
    │
    │  Bearer token
    ▼
NovelAI API (api.novelai.net / image.novelai.net)
```

The frontend is a React SPA served by Vite's dev server.  
All `/api/` requests are proxied to the FastAPI backend, which in turn calls the NovelAI API using the novelai-sdk.

---

## Authentication Flow

```
User enters email + password
        │
        ▼
POST /api/auth/login
        │
        │  auth_utils.py
        │  1. pre_salt = password[:6] + email + "novelai_data_access_key"
        │  2. salt     = blake2b(pre_salt, digest_size=16)
        │  3. key      = argon2id(password, salt, t=2, m=~1953KiB, p=1, len=64)
        │  4. access_key = urlsafe_b64encode(key)[:64]
        │
        ▼
POST https://api.novelai.net/user/login
  { "key": access_key }
        │
        ▼
{ "accessToken": "..." }  ← stored in localStorage as nai_token
        │
        ▼
All subsequent API calls include:
  Authorization: Bearer <accessToken>
```

Key derivation algorithm is identical to NovelAI's official web client  
(reference: https://gist.github.com/mnixry/9657bb1fb4cbb0f73c18076642555371).

---

## Backend Structure

```
src/python/
├── server.py        # FastAPI app, lifespan, CORS, router registration
├── client.py        # get_client() dependency — per-request AsyncNovelAI
├── auth_utils.py    # Key derivation + /user/login call
├── models.py        # All Pydantic request/response schemas
└── routes/
    ├── auth.py      # POST /api/auth/login
    ├── image.py     # Image generation endpoints
    └── metadata.py  # Metadata endpoints (no auth required)
```

### Client Dependency

`get_client()` is an async generator FastAPI dependency:

1. If `Authorization: Bearer <token>` header present → creates a fresh `AsyncNovelAI(api_key=token)`, yields it, then closes it after the request.
2. Otherwise → yields the global fallback client initialized from `NOVELAI_API_TOKEN` env var.

This keeps the backend stateless with respect to user sessions.

---

## Frontend Structure

```
src/javascript/
├── api.ts                     # apiFetch<T>(token, path, body?) utility
├── App.tsx                    # BrowserRouter + route definitions
├── main.tsx                   # React entry point
├── context/
│   └── AuthContext.tsx        # token state, login(), logout()
└── pages/
    ├── Login.tsx / Login.css  # Email + password form
    ├── Home.tsx / Home.css    # Dashboard / feature cards
    └── ImageGenerate.tsx / ImageGenerate.css
```

### Auth State

```
AuthContext
  token       ← localStorage.getItem('nai_token')  (null if logged out)
  login(t)    ← localStorage.setItem + setToken
  logout()    ← localStorage.removeItem + setToken(null)
```

`ProtectedRoute` redirects to `/login` when `token` is null.

---

## API Reference

Base URL: `http://localhost:8000`  
Interactive docs: `http://localhost:8000/docs`

All image/metadata endpoints require `Authorization: Bearer <token>` header  
(except `/api/metadata/extract` and `/api/metadata/erase` which are auth-optional).

### POST /api/auth/login

```json
Request:  { "email": "...", "password": "..." }
Response: { "access_token": "..." }
Errors:   401 login failed, 500 unexpected error
```

### POST /api/image/generate

```json
Request: {
  "prompt":          "string (required)",
  "negative_prompt": "string | null",
  "model":           "nai-diffusion-4-5-full | ...",
  "size":            "portrait | landscape | square | large_portrait | large_landscape",
  "steps":           28,
  "scale":           6.0,
  "seed":            null,
  "quality":         true,
  "uc_preset":       "light | strong | human_focus | furry_focus",
  "n_samples":       1
}
Response: { "images": ["base64..."], "format": "png" }
```

### POST /api/image/generate/stream

Same request body as `/generate`.  
Response: `text/event-stream` (SSE)

```
event: intermediate
data: {"event_type":"intermediate","samp_ix":0,"step_ix":5,"gen_id":...,"sigma":...,"image":"base64..."}

event: final
data: {"event_type":"final",...}

event: error
data: {"detail": "..."}
```

### POST /api/image/anlas

```json
Request:  { "params": <GenerateImageRequest>, "is_opus": false }
Response: {
  "model": "...",
  "total_anlas": 10,
  "base_anlas": 10,
  "per_image_anlas": 10,
  "requested_samples": 1,
  "billable_samples": 1,
  "opus_discount_applied": false,
  ...
}
```

### POST /api/metadata/extract

```json
Request:  { "image": "base64 or data:image/...;base64,..." }
Response: { "metadata": { "Comment": "{...}", ... } }
```

### POST /api/metadata/erase

```json
Request:  { "image": "base64...", "target": "alpha | png_info | both" }
Response: { "image": "base64..." }
```

---

## Data Flow: Image Generation

```
User fills prompt + params
        │  onClick / Ctrl+Enter
        ▼
apiFetch(token, '/api/image/generate', body)
        │  POST with Authorization header
        ▼
routes/image.py  generate_image()
        │  builds GenerateImageParams
        ▼
AsyncNovelAI.image.generate(params)
        │  POST https://image.novelai.net/ai/generate-image
        ▼
PIL.Image list
        │  base64 encode each image
        ▼
{ "images": ["base64..."], "format": "png" }
        │
        ▼
<img src="data:image/png;base64,...">   (browser memory only)
```

---

## Security Notes

- **Passwords** are never logged, stored, or transmitted in plaintext. Key derivation runs server-side; only the derived key touches the wire.
- **Access tokens** are stored in `localStorage`. For production use, consider `httpOnly` cookies instead.
- **Generated images** exist only in browser memory (base64 string in React state). They are not written to disk unless the user explicitly downloads them.
- The backend CORS policy is currently open (`allow_origins=["*"]`). Restrict this for any non-local deployment.
