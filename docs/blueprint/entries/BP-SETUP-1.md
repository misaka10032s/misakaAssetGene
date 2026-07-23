---
id: BP-SETUP-1
title: Setup 體驗（uv-bootstrap + 階段化進度 + 友善錯誤 + AI 診斷）
system: setup
tags: [setup, uv, error-handling, ai-explain]
status: 已完成
request_verbatim: "Setup 友善錯誤處理｜階段化進度、已知錯誤白名單 + 友善訊息、未知錯誤可 AI 解釋（spec.md §2 Feature Matrix，P0；§11.1~§11.4）（@PM 登記 M4.e，2026-06-13）"
decided_date: 2026-06-13
exec_links:
  - scripts/setup.ps1
  - scripts/setup.sh
  - scripts/doctor.py
done_date: 2026-06-13
origin: "M4.e commit b9e0d5f（2026-06-13）『feat(setup): uv-bootstrap + setup.log + known-error whitelist + setup-error AI explanation (M4.e; Tauri bundler deferred)』，303 tests passed，reviewer 確認無 API key 洩漏（對抗式探測 + 4 項安全測試）"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §11.1 入口 / §11.2 階段化進度 UI / §11.3 友善錯誤處理 / §11.4 原則
---

## 設計說明

Setup 不假設使用者已裝好 Python（改用 uv 自舉，7 個階段），全程寫 `setup.log`；遇到已知的 7 類常見錯誤（缺依賴、port 占用等）用白名單比對給出友善訊息，遇到白名單外的未知錯誤則呼叫 LLM Router（Ollama→Anthropic→OpenAI→Gemini，注入式 client）產生 AI 解釋，且任何寫入 log/console 前都先做 API key 脫敏。

### 現況核對（2026-07-23 盤點）

@PM 登記 303 tests passed，reviewer 對 API key 脫敏做了「adversarial probe」（對抗式探測，故意嘗試讓 key 洩漏）+ 4 項專屬安全測試，確認無洩漏。同一 commit 訊息明確標注「Tauri bundler deferred」——即這次一起決策把 Tauri 跨平台打包留到之後（見 `BP-SETUP-2`）。
