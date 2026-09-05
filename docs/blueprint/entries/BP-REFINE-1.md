---
id: BP-REFINE-1
title: refine 跨輪 prompt 疊加（prompt_mode）+ negative prompt 全流程
system: refine
tags: [refine, prompt-compose, negative-prompt, comfyui, lineage]
status: 已完成
request_verbatim: "GOAL A1 — effective prompt composition on refine. Every generated asset must persist the prompt that actually produced it (AssetRecord.effective_prompt, likewise effective_negative), for txt2img jobs AND refine children. refine_asset composes the child's prompt as compose(parent.effective_prompt, request.instruction, mode) where mode is a new RefineRequest.prompt_mode enum: append (default) / replace (explicit opt-in). GOAL A2 — negative prompt end-to-end. params.negative (str) honoured in _build_workflow for all three recipes ... sourced from a new MISAKA_COMFYUI_DEFAULT_NEGATIVE env. Refine inherits parent effective_negative unless params.negative overrides; persist on child. @PM 登記 misakaAssetGene-refine-loop-260905。"
decided_date: 2026-09-05
exec_links:
  - core/models/schemas.py
  - core/generation/refine.py
  - core/generation/service.py
  - core/generation/adapters/comfyui.py
  - core/config.py
  - .env.example
  - scripts/smoke/comfyui.py
done_date: 2026-09-05
origin: "2026-09-04/05 手動生成→refine 多輪迭代測試（@PM state/runs/misakaAssetGene-gen-test-260904/H-report.md）發現兩個自動化迭代循環的前置缺口：① refine 的 instruction 是該輪唯一 positive prompt，不會疊加 parent 的既有 prompt（core/generation/service.py:293 舊碼 prompt=request.instruction 逐字取代），導致某一輪補上的元素（雙刀）在下一輪遮罩覆蓋到同一區域、instruction 未重申時被吃掉；② 完全沒有 negative prompt 參數入口，core/generation/adapters/comfyui.py 的 DEFAULT_NEGATIVE_PROMPT 是寫死常數，job/refine params 塞什麼 key 都覆蓋不了它。@PM 登記 misakaAssetGene-refine-loop-260905，2026-09-05 落地兩者。"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.11 圖像多階段生成與局部精修（parent-child lineage / prompt delta）、§6.2 修圖策略選擇原則（可調參數詞彙）
---

## 設計說明

本次落地兩個獨立但相關的缺口，兩者都是「手動可以跑通、但自動化 GPT 式多輪迭代目前不成立」的前置阻塞（H-report §3/§4）。

### A1：refine 跨輪 prompt 疊加（`prompt_mode`）

1. **`AssetRecord.effective_prompt` / `effective_negative`**（`core/models/schemas.py`）— 新增兩個可選欄位，記錄「實際產出這個版本的 prompt / negative」。舊資產（無此欄位）留空，不做 migration script；讀取時由 `GenerationService._resolve_effective_prompt` / `_resolve_effective_negative` 回退到該資產原始 `job_id` 對應 job 的 `prompt` / `params.negative`，全無 job（例如純 import）則回退空字串 / `None`。
2. **`RefineRequest.prompt_mode: RefinePromptMode`**（`core/models/schemas.py`，新 enum `append` / `replace`）— `append`（預設）＝ parent 的 effective prompt + `", "` + instruction，並用 `dict.fromkeys` 去重相同的逗號分隔 tag（`core/generation/refine.py:_dedupe_comma_tags`，沿用 `GenerationService.refine_asset` 既有的 metadata tag 去重寫法，**不另寫第二套去重**；`decompose_prompt` 的 stage 級去重是不同粒度，不適用於扁平 tag 去重，故未重用）；`replace`＝今日既有行為（instruction 整個取代），明確 opt-in。`remove:<tags>` 模式不在本次範圍。
3. **`refine_planner.compose_prompt(parent_prompt, instruction, mode)`**（`core/generation/refine.py`，純函式）— `service.py:refine_asset` 呼叫它算出 `composed_prompt`，寫入 `refine_job.prompt`（原本是 `request.instruction` 逐字取代）；`plan.prompt_delta` 維持只記錄 instruction 本身（「這輪加了什麼」），不受影響。
4. **`_persist_generated_artifact`**（`core/generation/service.py`）— 產出的 `AssetRecord.effective_prompt = job.prompt`（對 txt2img root job 就是原始 consultant prompt，對 refine child 就是 composed prompt，兩者統一由同一欄位承載，不需要額外解析）。

### A2：negative prompt 全流程

1. **`core/config.py` / `.env.example`** — 新增 `MISAKA_COMFYUI_DEFAULT_NEGATIVE`（settings 欄位 `misaka_comfyui_default_negative_prompt`），取代原本 `comfyui.py` 內寫死的模組常數 `DEFAULT_NEGATIVE_PROMPT`（已刪除）。預設值是一般化、非 NSFW 特化的品質類負面詞（模糊/解剖錯誤/多肢/浮水印等），非逐字沿用使用者提供的參考 workflow 負面詞。
2. **`refine_planner.TUNABLE_PARAMS`**（`core/generation/refine.py`）— 加入 `"negative"`，讓 refine `params.negative` 覆寫不被 §6.2 planner 的可調參數白名單濾掉（一般生成 job 的 `JobExecutionPatch.params` 本來就沒有白名單限制，直接可用）。
3. **`GenerationService.refine_asset`**（`core/generation/service.py`）— refine 若「未帶」`params.negative`（key 不存在），回填 parent 的 `effective_negative`（`_resolve_effective_negative`）；帶了就以顯式覆寫為準 —— **含顯式空字串 `""`**（「這輪我要無負面詞」），一律用 `"negative" not in resolved_params` 的**存在性**判斷，不用真值判斷（`if not resolved_params.get("negative")` 會把顯式 `""` 誤判成「沒帶」而繼承掉，2026-09-05 review 後修正）。
4. **`comfyui.py:execute`** — `negative_prompt = params["negative"] if "negative" in params else get_settings().misaka_comfyui_default_negative_prompt`（存在性判斷，同上；舊寫法 `params.get("negative") or <default>` 會把顯式 `""` 落回設定預設值），`_build_workflow` 本身在 txt2img/img2img/inpaint 三種 recipe 都已統一吃 `negative_prompt` 參數（節點 "7" `CLIPTextEncode`），不需要改 `_build_workflow` 本身。
5. **`_persist_generated_artifact`** — IMAGE 產物的 `effective_negative`：`"negative" in job.params` 則直接持久化該顯式值（**含 `""`**，代表「這個版本明確無負面詞」）；否則套用設定的預設值（隱式套用時仍如實記錄，而非留空造成失真）。
6. **`scripts/smoke/comfyui.py`** — 同步移除對已刪除常數的引用，改讀 `get_settings().misaka_comfyui_default_negative_prompt`。

### 範圍外（跟隨追蹤）

- `remove:<tags>` prompt_mode、mask 產生/編輯 API、自動化多輪 GPT 迭代迴圈本身：另外排期（見 H-report §4 結論）。
- 前端目前沒有 UI 可設定 `prompt_mode` / `negative`，只能透過 API 直接帶入；資料層與 API 已就緒。
- 非 comfyui 的其他 adapter（voice/music/video）不在本次範圍。

### 驗收證據

- 單元測試：`tests/test_refine_strategy.py`（`compose_prompt` append/replace/dedup/無 parent prompt 回退，含大小寫不敏感 dedup）、`tests/test_refine_service.py`（child 持久化 `effective_prompt`/`effective_negative`、negative 覆寫優先於繼承、replace 模式忽略 parent、無 `effective_prompt` 的舊資產仍可正常 refine、**顯式空字串 negative 不被繼承值覆蓋**）、`tests/test_comfyui_adapter.py`（txt2img/img2img/inpaint 三種 recipe 的 negative `CLIPTextEncode` 節點吃到設定預設值 / 顯式覆寫 / **顯式空字串**，mock httpx）。
- 2026-09-05 review 後修正：`service.py:303`、`comfyui.py:47`、`service.py:723-726` 三處 negative 真值判斷改存在性判斷；`refine.py:_dedupe_comma_tags` 改大小寫/空白不敏感。`pytest -q`：555 passed, 2 skipped（既有 552 passed 全部維持綠燈，無迴歸）。
- `quality-gates/python/run.py l0`：G1/G2/G3b/G4 全部 PASS（G1 ruff baseline 178→178，一筆新增 `RefinePromptMode` 的 `UP042` 以行內 `# noqa` 註記維持與同檔其餘 10 個 `(str, Enum)` enum 一致風格，未動 baseline 機制本身；另兩筆既有「未使用 import」pre-existing 因新測試實際用到而合法收斂，178 pre-existing 已 `--update-baseline` 收斂為 resolved-only）。
- `quality-gates/python/run.py l1`：G5 diff coverage PASS（>=60%）。
- 真實 API 路徑（8402，ComfyUI 未啟動）：見 run dir `state/runs/misakaAssetGene-refine-loop-260905/A-impl.md`。

**Consumed by**: `BP-REFINE-2`（角色一致性自動精修迴圈）——`prompt_mode=append` 是
每輪 instruction 疊加既有通過項 fix_tags 的前提；`effective_negative` 繼承機制是
迴圈每輪 refine 呼叫直接沿用、不重新指定 negative 的原因。
