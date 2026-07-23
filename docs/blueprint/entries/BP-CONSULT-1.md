---
id: BP-CONSULT-1
title: 顧問對話流程狀態機（SQLite 持久化）
system: consult
tags: [consultant, dialogue, state-machine, sqlite]
status: 已完成
request_verbatim: "顧問引擎｜分輪漸進提問、候選選項、摘要確認、對話式修正（spec.md §2 Feature Matrix，P0；§4.1 對話流程狀態機；§4.1.1 會話狀態持久化，決策 2026-06-12）"
decided_date: 2026-06-12
exec_links:
  - core/consultant/engine.py
  - core/consultant/state_machine.py
  - core/consultant/session_store.py
done_date: 2026-06-12
origin: "2026-06-12 spec-sync 決策『consultant session state → backend SQLite』，merge commit 5fe572b（reviewed PASS）；M2（928dd77，2026-06-12）落地 SQLite 持久化"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §4.1 對話流程狀態機 / §4.1.1 會話狀態持久化
---

## 設計說明

顧問引擎以分輪漸進提問（不是一次列出所有問題）、每輪附候選選項、最後摘要確認，並支援對話式修正（使用者可回頭改前面的答案）。`core/consultant/state_machine.py` 定義對話狀態轉移，`core/consultant/engine.py` 是主引擎，`core/consultant/session_store.py` 把會話狀態持久化到後端 SQLite（2026-06-12 決策，取代原本僅存於記憶體的方案，讓桌面 App 重啟後對話不遺失）。

### 現況核對（2026-07-23 盤點）

三個檔案皆存在（`engine.py` 172 行、`state_machine.py` 152 行、`session_store.py` 160 行）。@PM 登記 M2「consultant state machine w/ SQLite persistence (§5.12)」隨 inpaint/img2img e2e 一起於 928dd77 合併，70 tests green，並有 Chrome UI e2e 實測驗證。
