// ==UserScript==
// @name         NovelAI Local API Example
// @namespace    https://novelai.net/
// @version      0.1.0
// @description  Userscript that calls the local FastAPI relay server (novelai-userscripts-example)
// @author       hirotoitpost
// @match        https://novelai.net/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==

(function () {
  "use strict";

  const API_BASE = "http://127.0.0.1:8000";

  // ─── Core HTTP helper ────────────────────────────────────────────────────────

  function request(method, path, body) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url: `${API_BASE}${path}`,
        headers: { "Content-Type": "application/json" },
        data: body !== undefined ? JSON.stringify(body) : undefined,
        onload(res) {
          if (res.status >= 200 && res.status < 300) {
            resolve(JSON.parse(res.responseText));
          } else {
            let detail;
            try {
              detail = JSON.parse(res.responseText).detail;
            } catch {
              detail = res.responseText;
            }
            reject(new Error(`HTTP ${res.status}: ${detail}`));
          }
        },
        onerror(err) {
          reject(new Error("Network error — is the relay server running?"));
        },
      });
    });
  }

  // ─── Image generation ────────────────────────────────────────────────────────

  /**
   * Generate images via the local relay server.
   * @param {Object} params - GenerateImageRequest fields
   * @returns {Promise<{images: string[], format: string}>}
   *   images: array of base64-encoded image strings
   */
  async function generateImage(params) {
    return request("POST", "/api/image/generate", params);
  }

  /**
   * Estimate Anlas cost without calling the NovelAI API.
   * @param {Object} params - GenerateImageRequest fields
   * @param {boolean} isOpus
   * @returns {Promise<Object>} AnlasEstimateResponse
   */
  async function estimateAnlas(params, isOpus = false) {
    return request("POST", "/api/image/anlas", { params, is_opus: isOpus });
  }

  /**
   * Stream image generation via SSE.
   * @param {Object} params - GenerateImageRequest fields
   * @param {function} onChunk - called with each {event_type, samp_ix, step_ix, gen_id, sigma, image}
   * @returns {Promise<void>}
   */
  function generateImageStream(params, onChunk) {
    return new Promise((resolve, reject) => {
      let buffer = "";

      GM_xmlhttpRequest({
        method: "POST",
        url: `${API_BASE}/api/image/generate/stream`,
        headers: { "Content-Type": "application/json" },
        data: JSON.stringify(params),
        responseType: "stream",
        onloadstart(res) {
          const reader = res.response.getReader();
          const decoder = new TextDecoder();

          function read() {
            reader
              .read()
              .then(({ done, value }) => {
                if (done) {
                  resolve();
                  return;
                }
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop();
                for (const line of lines) {
                  if (line.startsWith("data: ")) {
                    try {
                      const chunk = JSON.parse(line.slice(6));
                      if (chunk.event_type === "error") {
                        reject(new Error(chunk.detail));
                        return;
                      }
                      onChunk(chunk);
                    } catch {}
                  }
                }
                read();
              })
              .catch(reject);
          }
          read();
        },
        onerror() {
          reject(new Error("Stream connection failed"));
        },
      });
    });
  }

  // ─── Metadata utilities ──────────────────────────────────────────────────────

  /**
   * Extract stealth PNG metadata from a NovelAI-generated image.
   * @param {string} imageBase64 - raw base64 (no data URI prefix)
   * @returns {Promise<{metadata: Object}>}
   */
  async function extractMetadata(imageBase64) {
    return request("POST", "/api/metadata/extract", { image: imageBase64 });
  }

  /**
   * Erase metadata from an image.
   * @param {string} imageBase64 - raw base64 (no data URI prefix)
   * @param {"alpha"|"png_info"|"both"} target
   * @returns {Promise<{image: string}>} cleaned image as base64
   */
  async function eraseMetadata(imageBase64, target = "both") {
    return request("POST", "/api/metadata/erase", { image: imageBase64, target });
  }

  // ─── Helper: image element from base64 ──────────────────────────────────────

  function b64ToImg(b64, fmt = "png") {
    const img = document.createElement("img");
    img.src = `data:image/${fmt};base64,${b64}`;
    return img;
  }

  // ─── Demo UI ─────────────────────────────────────────────────────────────────

  function buildUI() {
    const panel = document.createElement("div");
    panel.style.cssText = `
      position: fixed; bottom: 20px; right: 20px; z-index: 9999;
      background: #1a1a2e; border: 1px solid #7c3aed; border-radius: 12px;
      padding: 12px; display: flex; flex-direction: column; gap: 8px;
      font-family: sans-serif; font-size: 13px; color: #e0e0e0; width: 220px;
    `;

    const title = document.createElement("div");
    title.textContent = "NovelAI Local API";
    title.style.cssText = "font-weight: bold; color: #a78bfa; text-align: center;";

    const promptInput = document.createElement("textarea");
    promptInput.placeholder = "Prompt...";
    promptInput.value = "1girl, masterpiece, high quality";
    promptInput.rows = 3;
    promptInput.style.cssText =
      "width: 100%; box-sizing: border-box; background: #0f0f1a; color: #e0e0e0; border: 1px solid #444; border-radius: 6px; padding: 4px; resize: vertical;";

    const anlasBtn = document.createElement("button");
    anlasBtn.textContent = "Estimate Anlas";
    _styleBtn(anlasBtn, "#374151");

    const anlasResult = document.createElement("div");
    anlasResult.style.cssText = "color: #9ca3af; font-size: 11px; text-align: center; min-height: 16px;";

    const genBtn = document.createElement("button");
    genBtn.textContent = "Generate Image";
    _styleBtn(genBtn, "#7c3aed");

    const preview = document.createElement("div");
    preview.style.cssText = "text-align: center;";

    anlasBtn.addEventListener("click", async () => {
      anlasResult.textContent = "Estimating…";
      try {
        const est = await estimateAnlas({ prompt: promptInput.value });
        anlasResult.textContent = `~${est.total_anlas} Anlas`;
      } catch (err) {
        anlasResult.textContent = `Error: ${err.message}`;
      }
    });

    genBtn.addEventListener("click", async () => {
      genBtn.textContent = "Generating…";
      genBtn.disabled = true;
      preview.innerHTML = "";

      try {
        const result = await generateImage({ prompt: promptInput.value });
        const img = b64ToImg(result.images[0], result.format);
        img.style.cssText = "max-width: 100%; border-radius: 8px; margin-top: 4px;";
        preview.appendChild(img);
      } catch (err) {
        preview.textContent = `Error: ${err.message}`;
      } finally {
        genBtn.textContent = "Generate Image";
        genBtn.disabled = false;
      }
    });

    panel.append(title, promptInput, anlasBtn, anlasResult, genBtn, preview);
    document.body.appendChild(panel);
  }

  function _styleBtn(btn, bg) {
    btn.style.cssText = `
      width: 100%; padding: 6px; background: ${bg}; color: #fff;
      border: none; border-radius: 6px; cursor: pointer; font-size: 13px;
    `;
  }

  // ─── Expose API on window for advanced use ───────────────────────────────────

  window.naiLocalAPI = {
    generateImage,
    generateImageStream,
    estimateAnlas,
    extractMetadata,
    eraseMetadata,
  };

  // ─── Init ─────────────────────────────────────────────────────────────────────

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildUI);
  } else {
    buildUI();
  }
})();
