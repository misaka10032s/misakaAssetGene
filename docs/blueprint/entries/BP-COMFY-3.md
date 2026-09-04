---
id: BP-COMFY-3
title: 一般生成 job 的可調參數（checkpoint/steps/cfg/…）與 checkpoint 解析順序
system: comfy
tags: [comfyui, checkpoint, job-params, txt2img, resolver]
status: 已完成
request_verbatim: "Normal generation jobs currently cannot carry any generation parameters ... fix it so a job can be patched with params, newly built jobs are seeded with a configurable default checkpoint, checkpoint resolution order becomes explicit override → configured default → live[0] fallback. 使用者指定預設 checkpoint 為 novaAnimeXL_ilV180.safetensors（@PM 登記 260904 misakaAssetGene-job-params）"
decided_date: 2026-09-05
exec_links:
  - core/models/schemas.py
  - core/generation/service.py
  - core/generation/adapters/comfyui.py
  - core/config.py
  - .env.example
done_date: 2026-09-05
origin: "2026-09-04 端到端生成測試發現：一般（非 refine）job 完全無法帶生成參數，`JobExecutionPatch` 只有 worker/recipe/source_asset_id/mask_asset_id，`_build_job` 從不寫入 `params`，導致 `_resolve_checkpoint_name` 落回 `live[0]`（ComfyUI 存活清單依字母序的第一個）——三張圖全部落到 `3x3x3mixxl_3dV01`（3D 風格 checkpoint），而非使用者要的動漫風格 checkpoint。@PM 登記 misakaAssetGene-job-params-260904，2026-09-05 落地。"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §6.2 精修策略可調參數詞彙（checkpoint/steps/cfg/width/height/sampler/scheduler/seed）
---

## 設計說明

`RefineRequest.params` 一直支援可調生成參數（checkpoint/steps/cfg/denoise/width/height/sampler/scheduler/seed），並由 `comfyui._sampler_inputs` / `_build_workflow` 消費；但「一般生成 job」（非 refine，由 consultant `record_plan` → `_build_job` 建立）完全沒有這條路徑：`JobExecutionPatch` 只有 worker/recipe/source_asset_id/mask_asset_id 四個欄位，`_build_job` 也從未寫入 `params`，所以一般 job 只能吃 `_resolve_checkpoint_name` 的最終回退 `live[0]`（ComfyUI 存活 checkpoint 清單依字母序排序後的第一個）——完全與使用者想要的風格無關。

### 本次落地（2026-09-05）

1. **`JobExecutionPatch.params`**（`core/models/schemas.py`）— 新增 `params: dict[str, Any]`，沿用 `RefineRequest.params` 既有詞彙，不另造一套。
2. **`GenerationService.update_job`**（`core/generation/service.py`）— PATCH 進來的 `params` 用 merge（`{**current.params, **patch.params}`）而非整包覆蓋，避免只改 checkpoint 卻把已設定的 steps/cfg 沖掉。
3. **`GenerationService._build_job`**（`core/generation/service.py`）— IMAGE 模態新建 job 時，`params["checkpoint"]` 直接種入設定的預設值（`get_settings().misaka_comfyui_default_checkpoint`），讓 job 在使用者動手 PATCH 之前就已經是合理的預設，而非等到 adapter 執行時才臨時決定。
4. **`core/config.py` / `.env.example`** — 新增 `MISAKA_COMFYUI_DEFAULT_CHECKPOINT`（預設 `novaAnimeXL_ilV180.safetensors`，使用者指定值），後端一律經 `get_settings()` 讀取，不散落 `os.environ`。
5. **`_resolve_checkpoint_name`**（`core/generation/adapters/comfyui.py`）— 解析順序改為：
   1. `override`（job/refine `params.checkpoint`/`ckpt_name`，存在於 live 或本機才採用）
   2. `default`（設定的預設值，**只在 live 清單裡有才採用**；不在的話記一筆 warning 並往下 fallthrough，不 raise）
   3. `live[0]`（既有行為，spec §5.13 live-first）
   4. `local[0]`（live 不可達時的既有回退）

### 範圍外（跟隨追蹤）

- 前端目前沒有 UI 可編輯這些 job params（只能透過 API 直接 PATCH）——留待前端排期補上一個 params 編輯面板（checkpoint 下拉、steps/cfg/width/height 數值輸入），資料層與 API 已就緒，純前端工作。
- 影片/音效 adapter 的參數詞彙不在本次範圍內，僅涵蓋 `comfyui.py`（圖像 txt2img/img2img/inpaint）。

### 驗收證據

- 單元測試：`tests/test_job_generation_params.py`（PATCH 持久化、merge 語意、`_build_job` 種預設值、override→default→live[0] 端到端到 `CheckpointLoaderSimple.ckpt_name`）+ `tests/test_comfyui_adapter.py`（resolver 三層順序、default 不在 live 清單時的 warning fallthrough）。
- 真實 API 路徑驗證（8402，ComfyUI 未啟動，僅驗證持久化與 workflow 組裝邏輯）：建立專案 → `consultant/clarify` → job 建立時已帶 `params.checkpoint = "novaAnimeXL_ilV180.safetensors"` → `PATCH .../jobs/{id}` 帶 `params={"checkpoint":"userChosenAnime.safetensors","steps":30,"cfg":6}` → `GET .../workspace` 回讀確認三個 key 均持久化，且 checkpoint 已被覆蓋。
