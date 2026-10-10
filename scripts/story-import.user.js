// ==UserScript==
// @name         NovelAI Story Importer (novelai-userscripts-example)
// @namespace    https://novelai.net/
// @version      0.1.0
// @description  Reads the clipboard (filled by the "export-story.naiscript" in-app script) and imports it straight into novelai-userscripts-example — no manual copy/paste into the app needed.
// @author       hirotoitpost
// @match        https://novelai.net/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

// 使い方:
//   1. NovelAIのエディタで scripts/export-story.naiscript の「本文をコピー」ボタンを押す
//      (api.v1.document.scan() で取得した本文がクリップボードに入る)
//   2. このスクリプトが左下に出すパネルの「クリップボードから取り込む」を押す
//      → クリップボードのテキストをそのまま /api/story/import に送る
//
// NovelAIのネイティブスクリプト(export-story.naiscript)はAPIサンドボックス内で動作し
// ネットワークアクセス手段を持たないため、バックエンドへ直接送信できない。
// このスクリプトはページ本体のコンテキストで動くTampermonkeyユーザースクリプトとして、
// クリップボードを橋渡しに使うことでその制約を回避する。

(function () {
  "use strict";

  const API_BASE = "http://127.0.0.1:8000";
  const DEFAULT_N_SCENES = 4;
  const DEFAULT_PANELS_PER_PAGE = 4;

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
        onerror() {
          reject(new Error("ネットワークエラー — ローカルサーバーが起動しているか確認してください"));
        },
      });
    });
  }

  async function importFromClipboard() {
    let text;
    try {
      text = await navigator.clipboard.readText();
    } catch {
      throw new Error("クリップボードを読み取れませんでした(ブラウザの権限設定を確認してください)");
    }
    if (!text || !text.trim()) {
      throw new Error('クリップボードが空です。先にNovelAI側で「本文をコピー」を押してください');
    }
    return request("POST", "/api/story/import", {
      text,
      n_scenes: DEFAULT_N_SCENES,
      panels_per_page: DEFAULT_PANELS_PER_PAGE,
    });
  }

  function buildUI() {
    const panel = document.createElement("div");
    panel.style.cssText = `
      position: fixed; bottom: 20px; left: 20px; z-index: 9999;
      background: #1a1a2e; border: 1px solid #7c3aed; border-radius: 12px;
      padding: 10px 12px; display: flex; flex-direction: column; gap: 6px;
      font-family: sans-serif; font-size: 12px; color: #e0e0e0; width: 200px;
    `;

    const title = document.createElement("div");
    title.textContent = "物語インポート";
    title.style.cssText = "font-weight: bold; color: #a78bfa; text-align: center;";

    const btn = document.createElement("button");
    btn.textContent = "クリップボードから取り込む";
    btn.style.cssText =
      "padding: 6px; background: #7c3aed; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 12px;";

    const status = document.createElement("div");
    status.style.cssText = "text-align: center; min-height: 14px; color: #9ca3af; word-break: break-word;";

    btn.addEventListener("click", async () => {
      btn.disabled = true;
      status.textContent = "取り込み中...";
      try {
        const story = await importFromClipboard();
        status.textContent = `取り込みました(ID ${story.id}) — アプリ側でシーン分割へ進めます`;
      } catch (err) {
        status.textContent = `エラー: ${err.message}`;
      } finally {
        btn.disabled = false;
      }
    });

    panel.append(title, btn, status);
    document.body.appendChild(panel);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildUI);
  } else {
    buildUI();
  }
})();
