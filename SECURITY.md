# Security

English | [日本語](docs/SECURITY_jp.md)

## Audit Summary

An internal security audit was conducted on **2026-05-09** covering the `novelai-sdk` dependency and the relay server in this repository (`src/python/`).

**Result: No security issues found.**

---

## Network Communication

The SDK communicates exclusively with the following official NovelAI endpoints over HTTPS:

| Host | Purpose |
|---|---|
| `https://image.novelai.net` | Image generation |
| `https://text.novelai.net` | Text generation |
| `https://api.novelai.net` | General API |

No data is sent to any third-party services. All three base URLs are configurable via environment variables (`NOVELAI_IMAGE_BASE`, `NOVELAI_TEXT_BASE`, `NOVELAI_API_BASE`) and default to the official NovelAI domains.

### Relay Server Topology

```
Browser
  ↓ HTTP (localhost:8000, local only)
Relay server (src/python/)
  ↓ HTTPS
image.novelai.net  ← Official NovelAI server
```

The relay server runs entirely on the local machine and does not expose any data externally.

---

## Authentication

- The API token is passed as a standard `Authorization: Bearer {token}` header.
- The token is never written to disk, logged, or included in request bodies or URLs.
- It is loaded from the `NOVELAI_API_KEY` / `NOVELAI_API_TOKEN` environment variable (or `.env` file) and held in memory only.

---

## Code Inspection

The following dangerous patterns were searched for and **not found** in `novelai-sdk`:

- Dynamic code execution: `eval()`, `exec()`, `compile()`, `__import__()`
- Shell access: `subprocess`, `os.system()`, `popen()`
- Obfuscated code or suspicious imports

File I/O is limited to reading user-supplied image files and writing generated images to a user-specified output directory via the CLI.

---

## Dependencies

All direct dependencies are well-known, actively maintained packages:

| Package | Purpose |
|---|---|
| `httpx` | HTTP client |
| `pydantic` | Data validation |
| `pillow` | Image processing |
| `numpy` | Array operations |
| `python-dotenv` | `.env` file loading |
| `rich` | Terminal UI |
| `typing-extensions` | Type hint backports |

---

## Credential Handling

- Store your API token in `.env` only. Never hardcode it in source files.
- `.env` is listed in `.gitignore` and will not be committed to the repository.
- Do not share `.env` or its contents publicly.

---

## Reporting a Vulnerability

If you discover a security issue in this repository, please open a [GitHub Issue](../../issues) marked **[Security]**, or contact the maintainer directly before public disclosure.
