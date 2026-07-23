---
id: BP-INTEGRATION-1
title: Tools & Workers 整合管理（安裝/啟停/健康檢查/Smoke Test）
system: integration
tags: [integration, workers, tools, lifecycle, smoke-test]
status: 已完成
request_verbatim: "整合管理 (Tools+Workers+Models)｜統一選單腳本 + WebUI：版本檢查、更新、回滾、smoke test（spec.md §2 Feature Matrix，P0；§3.3 Tools & Workers 整合管理架構；§9.1 統一選單腳本；§9.4 WebUI 整合；§9.6 Smoke Test）"
decided_date: 2026-05-07
exec_links:
  - core/integration/workers.py
  - frontend/src/pages/IntegrationManager.vue
  - scripts/smoke
done_date: 2026-05-07
origin: "M1 commit ca99f10（2026-05-07）首次引入 core/integration/；WorkersService 的安裝/啟停/健康檢查/smoke 方法群持續擴充至 M3（worker readiness，見 BP-OFFLINE-1）"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §3.3 Tools & Workers 整合管理架構 / §9.1 統一選單腳本 / §9.4 WebUI 整合 / §9.6 Smoke Test
---

## 設計說明

`core/integration/workers.py`（`WorkersService`，575 行）是「Tools+Workers+Models」三類被管理物件中 Workers 這條的核心：`list_workers()`（列出 + 快取 2 秒）、`install_worker()`（git clone --filter=blob:none + checkout 推薦 commit + 執行安裝指令）、`start_worker()`/`stop_worker()`（子行程生命週期）、`smoke_test_worker()`（跑 `scripts/smoke/<worker>.py`）、健康檢查（HTTP 探活 + PID 存活雙重判斷）。前端 `IntegrationManager.vue` 是對應 WebUI（§9.4），對外路由 `POST /api/v1/workers/{name}/install|start|stop|smoke`。

### 現況核對（2026-07-23 盤點）

`scripts/smoke/` 下 6 個 worker 各有獨立 smoke 腳本（ace_step/comfyui/gpt_sovits/stable_audio_tools/ultimate_rvc/voxcpm），`core/main.py` 確認掛載 install/start/stop/smoke 四組路由（`core/main.py:1267-1320`）。§9.1「統一選單腳本」在桌面 App 情境下以此 WebUI + 路由取代傳統 CLI 選單腳本形式落地。
