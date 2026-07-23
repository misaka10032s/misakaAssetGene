---
id: BP-PORTABILITY-1
title: 專案可攜性（zip 匯出 / 匯入 + 完整性 manifest）
system: portability
tags: [portability, zip, export, import, manifest]
status: 已完成
request_verbatim: "專案可攜性｜所有路徑相對化、匯出 zip 含完整性 manifest（spec.md §2 Feature Matrix，P0；§5.5 專案可攜性）"
decided_date: 2026-06-12
exec_links:
  - core/project/portability.py
done_date: 2026-06-12
origin: "M2（928dd77，2026-06-12）『zip import』隨 ComfyUI 全深度一併合併，@PM 登記 Chrome UI e2e 驗證『zip round-trip』"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.5 專案可攜性 (Portability)
---

## 設計說明

專案匯出成 zip 時所有路徑相對化（不寫死絕對路徑），內含 `export.manifest.json`（必要）與選配的 `license-report.json`；匯入時驗證 manifest、拒絕 zip-slip（惡意路徑逃逸）條目，並解決 id/name 碰撞（重新指派新 id，記錄 `origin_id` 於 project.json）。

### 現況核對（2026-07-23 盤點）

`core/project/portability.py` 存在，`MAX_UNCOMPRESSED_BYTES = 4 GB` 硬上限防 zip-bomb（對應 M2(c) review 修復項「zip-bomb budget」，928dd77），`ensure_relative()` 明確拒絕絕對路徑。@PM 登記 M2 Chrome UI e2e 驗證過真實 zip 匯出→匯入的完整回圈。
