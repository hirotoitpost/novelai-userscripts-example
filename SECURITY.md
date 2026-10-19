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

## Multi-user mode (`APP_MODE=multi`)

When several invited people share one server:

- **Who can log in**: only emails invited with `scripts/manage_users.py add`. Emails are stored as an HMAC derived from
  `APP_MASTER_KEY`, not in plain text.
- **Proof of identity**: login takes the NovelAI email, password and a novelai.net access token. The server checks that the
  keystore of that token opens with the key derived from the email and password. Passwords are not stored.
- **In the browser**: only an `HttpOnly`, `SameSite=Strict` session cookie. NovelAI tokens and the keystore key never reach the
  browser; any `Authorization` header from the browser is dropped. Writes from other origins are refused, and CORS allows
  only `APP_ALLOWED_ORIGINS`.
- **On the server**: NovelAI tokens and the keystore key are encrypted with a per-user key (PyNaCl `SecretBox`), wrapped by
  `APP_MASTER_KEY`. Keep the master key out of the repository (Secret Manager when hosted); losing or changing it makes
  every invite and user unrecognisable.
- **Separation**: each user has their own database and folder; code that opens user data without a known user fails rather
  than falling back to shared data. The operator's `.env` token is never used for other people's requests.
- **Admin**: the admin page, `.env` viewing and content-guard changes are admin-only; `.env` cannot be edited from the page.
- **Audit log**: logins (including refused ones), token changes, admin and guard changes and CLI operations are recorded
  without emails or tokens (`scripts/manage_users.py audit`, or the admin page).
- **Limits**: per-user daily limits and one-at-a-time for the operator's GPU (Ollama / vLLM) and ComfyUI.
- **Backups and deletion**: `scripts/manage_users.py delete` removes a user's data from the server; copies inside earlier
  backups remain until they are pruned.

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
