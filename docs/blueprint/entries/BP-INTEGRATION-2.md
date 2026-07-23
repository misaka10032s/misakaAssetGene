---
id: BP-INTEGRATION-2
title: Manifest / Model Registry 查詢 API
system: integration
tags: [integration, manifest, model-registry, readonly]
status: 開發中
request_verbatim: "整合管理 (Tools+Workers+Models)（spec.md §9.2 Manifest schemas；§9.3 Model Registry）"
decided_date: 2026-05-07
exec_links:
  - core/integration/tools.py
  - core/integration/model_registry.py
  - core/models/registry.json
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §9.2 Manifest schemas / §9.3 Model Registry
---

## 設計說明

spec §9.2/§9.3 描述 tools/workers/models 三類物件各有自己的 manifest schema（版本、repo url、安裝指令等），Model Registry 額外分類管理實際的模型檔案（checkpoint/LoRA 等）及其授權/NSFW 屬性（供 `BP-LICENSE-1` 讀取）。

### 現況核對（2026-07-23 盤點）

`core/integration/tools.py`（16 行）與 `core/integration/model_registry.py`（11 行）目前都只有**唯讀列舉**能力（`ToolsService.list_tools()` 讀 manifest 回傳工具清單；`ModelRegistryService.list_categories()` 讀 `core/models/registry.json` 回傳分類清單），沒有新增/移除/版本切換等寫入能力——對比 `BP-INTEGRATION-1` 的 `WorkersService`（575 行，含安裝/啟停/smoke 全套生命週期），Tools 與 Model Registry 這兩塊明顯薄很多。狀態判定為「開發中」（讀取路徑可用，管理路徑未做），而非「已完成」。
