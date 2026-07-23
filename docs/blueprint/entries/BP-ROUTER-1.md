---
id: BP-ROUTER-1
title: LLM Router（Ollama / llama.cpp / Anthropic / OpenAI / Gemini / OpenRouter）
system: router
tags: [llm, router, provider]
status: 已完成
request_verbatim: "LLM 路由｜統一介面切換 Ollama / llama.cpp / Anthropic / OpenAI / Gemini / OpenRouter（spec.md §2 Feature Matrix，P0）"
decided_date: 2026-05-07
exec_links:
  - core/llm/router.py
done_date: 2026-05-07
origin: "M1 commit ca99f10（2026-05-07）首次引入 core/llm/router.py；M3（7af6066/2e1aa19，2026-06-13）補上 provider gating 與 offline 三態整合"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §2 核心功能總覽 — LLM 路由
---

## 設計說明

`core/llm/router.py` 提供統一介面，讓顧問引擎與生成服務不用關心背後實際打哪個 LLM provider（本機 Ollama/llama.cpp，或雲端 Anthropic/OpenAI/Gemini/OpenRouter）。桌面應用預設 bundling 本機 Ollama（`.claude/CLAUDE.md` stack 說明），雲端 provider 為選配，並與離線三態（`BP-OFFLINE-1`）聯動 — Always Offline 時只允許本機 provider。

### 現況核對（2026-07-23 盤點）

`core/llm/router.py` 存在且被 `core/consultant/engine.py`、setup 友善錯誤 AI 解釋（M4.e，spec §11.3：Ollama→Anthropic→OpenAI→Gemini 注入式 client）等多處消費。@PM 登記 M4.e 對 API key 做了對抗式探測 + 4 項安全測試驗證「不洩漏 key」（commit b9e0d5f/877f876，2026-06-13）。
