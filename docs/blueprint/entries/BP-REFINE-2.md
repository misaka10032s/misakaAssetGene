---
id: BP-REFINE-2
title: 角色一致性自動精修迴圈（Fidelity Loop）
system: refine
tags: [refine, fidelity, vlm-critic, consultant, suggestion-card, comfyui, ollama]
status: 已完成
request_verbatim: "角色一致性自動精修迴圈（@PM 派工 misakaAssetGene-refine-loop-260905，
  C-spec.md）：單角色/單服裝變體/單張立繪的『出圖 → VLM 逐項檢查 → 自動遮罩局部重繪 →
  再檢查』≤N 輪迴圈；checklist 由角色 SSOT（setting.md/outfits.md）動態解析；以顧問建議
  卡片呈現，可逐輪確認或 per-project auto-loop。Brief 3（本條目落地範圍）：blueprint 條目、
  FidelitySuggestionCard 建議卡片、專案設定 auto_loop_enabled、整合測試（真 Ollama +
  真 ComfyUI 跑 1 輪 critique）。"
decided_date: 2026-09-05
exec_links:
  - core/consultant/fidelity.py
  - core/consultant/fidelity_loop.py
  - core/consultant/fidelity_store.py
  - core/consultant/fidelity_service.py
  - core/consultant/fidelity_suggestion.py
  - core/llm/vision.py
  - core/llm/providers/ollama.py
  - core/llm/providers/openai.py
  - core/models/schemas.py
  - core/project/manager.py
  - core/main.py
done_date: 2026-09-06
origin: "H-report.md（@PM state/runs/misakaAssetGene-gen-test-260904）手動 3 輪迭代驗收
  發現兩個結構性缺口：refine instruction 逐字取代 parent prompt（→ BP-REFINE-1 解決）、
  無 mask 產生 API（→ BP-EDITOR-2 解決）。C-spec.md 在兩者之上規格化整個自動迴圈，拆三個
  Brief 依序派工：Brief 1（checklist 解析器 + VLM 判官，commit 96c67f8）、Brief 2（迴圈
  控制器 + 持久化 + API + SSE，commit 1bb43bf，複審修正 214ea1d）、Brief 3（本條目：blueprint
  + 建議卡片 + 專案設定 + 整合測試）。"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.15 角色一致性檢查（Fidelity Critic）— Brief 1/2/3 全部落地於此節
---

## 設計說明

延續 `BP-REFINE-1`（prompt_mode 疊加 + negative prompt）與 `BP-EDITOR-2`
（bbox→mask 端點）兩個前置缺口的解決，本條目落地「角色一致性檢查」的完整自動化
迴圈——出圖後不再需要人工逐項比對角色設定文件，而是讓 VLM 判官讀角色 SSOT
動態解析出的 checklist，逐項判斷通過/失敗，失敗項自動組裝局部遮罩＋重繪
instruction，重跑到全過或達輪數上限。三個 Brief 依序落地：

### Brief 1：Checklist 解析器 + VLM 判官（commit `96c67f8`）

`core/consultant/fidelity.py`：`parse_character_checklist(setting_md, outfits_md,
outfit_variant)` 從角色 SSOT（`setting.md` 的 `## 🎨 外型特徵` 逐 bullet、
`outfits.md` 指定服裝變體的巢狀子 bullet）動態解析出 `FidelityCheck` 清單
（`id`/`label_zh`/`pass_criteria` 原文逐字/`region_hint` 六分區/`fix_tags`/
`source`）——關鍵詞啟發式，非翻譯，會有已知誤判（記錄於模組 docstring）。
`CharacterSheet.sheet_source_path`（可選）指向角色資料夾，永遠即時讀檔，
不快取進 SQLite。

`core/llm/vision.py` + `providers/ollama.py`/`providers/openai.py`：新
`critique_image` 呼叫（Ollama `/api/chat` 本機首選，`MISAKA_OLLAMA_VISION_MODEL`
預設 `qwen2.5vl:7b`；OpenAI Chat Completions 雲端備援，離線閘門沿用
`router.py` 規則）。三道防幻覺閘門（僅降級 fail→pass，絕不影響 pass）：
① bbox 為 null 或面積 > 60% 全圖 → 過於空泛；② 兩輪一致性 AND（一 fail 一
pass 視為 pass）；③ bbox 與 `region_hint` 頭到腳分區不相容。

**真實驗收證據**（真 Ollama + 真 H-3-final.png，19 checks 兩輪，29.7s
wall-clock）：2 fail / 19，兩個 fail 皆有合理定位未被閘門誤殺；`setting-6`
模型漏答與 `outfits-6` 兩輪不一致兩種邊界情況皆正確優雅處理（詳見
`state/runs/misakaAssetGene-refine-loop-260905/C1-impl.md`）。

### Brief 2：迴圈控制器 + 持久化 + API + SSE（commit `1bb43bf`，複審修正 `214ea1d`）

`core/consultant/fidelity_loop.py`（純邏輯，I/O 全注入）：狀態機
`PENDING_CRITIQUE → CRITIQUING →`（全過 `PASSED`；有 fail 未達上限
`AWAITING_USER`；達上限仍有 fail `STOPPED_MAX_ROUNDS`；退步
`STOPPED_REGRESSION_RECOVERED` 回退 `best_asset_id`）。每輪組裝
`plan_round`：top-k(k≤2) 失敗項依 confidence 降冪、貪婪跳過 bbox 重疊者；
遮罩 `dilate=12/feather=8`，`subtract`＝同批已過關且與（膨脹後）選中區域
重疊的既有 bbox——**但絕不挖空選中失敗項本身的修補區**（複審 MAJOR#3
發現的真實缺陷：候選 bbox 與失敗項原始 bbox 的 IoU ≥ 0.3 時整筆排除、
< 0.3 才保留並裁掉重疊部分）；`instruction`＝選中失敗項 ∪ 重疊已過關項
`fix_tags`（去重、保序、上限 60）；失敗面積 > 40% 顯式 `img2img`，否則交給
`refine.py` 依 `mask_asset_id` 自動選 `INPAINT`。

`core/consultant/fidelity_store.py`：`fidelity_loops`/`fidelity_loop_rounds`
兩表，與 `SessionStore`/`AssetStore` 同一 `memory.sqlite`。
`core/consultant/fidelity_service.py`：串接純控制器與真實 I/O（判官/遮罩/
refine 三步皆可注入 callable，測試永不觸碰真實 Ollama/ComfyUI）。四支 API
（啟動/推進/查詢/SSE）。複審發現並修正 3 個 MAJOR：`advance()` 並行防護
（per-loop lock + `claim_round` 原子 `UPDATE...WHERE`，衝突 409）、基準判定
例外防護（比照既有 refine round 包 try/except 設 `FAILED`）、subtract 挖空
選中失敗區的真實缺陷（即時驗收自證）。

### Brief 3（本條目）：Blueprint + 建議卡片 + 專案設定 + 整合測試

**`FidelitySuggestionCard`**（`core/models/schemas.py` 新 schema，
`core/consultant/fidelity_suggestion.py` 純邏輯建構）：比照
`TrainingSuggestionCard`（spec §4.4/§5.12.1）「建議卡片、不自動執行」的設計
原則，但觸發條件不同——訓練卡片是「這次對話意圖」觸發（`planner.py` 分析
prompt/modality），本卡片的條件是**專案層級的事實**、與當前對話內容無關：
專案已有至少一個 IMAGE asset（`asset_type == "image"`，排除遮罩）**且**
至少一個 `CharacterSheet.sheet_source_path` 已設定。因為這個條件需要讀
專案 asset 清單與 character-sheet store，超出無狀態 planner 的存取範圍，
所以卡片組裝移到路由層（`core/main.py:_attach_fidelity_suggestion_cards`），
在 `POST .../consultant/clarify`、`POST .../consultant/session`、
`POST .../consultant/session/advance` 三條回應算好後附加到
`ClarifyResult.fidelity_suggestion_cards`（最多一張：最新建立的 IMAGE
asset ＋ 第一個有 `sheet_source_path` 的 CharacterSheet；`outfit_variant_
choices` 即時讀該角色 `outfits.md` 解析，讀取失敗降級為空清單而非讓整個
clarify 回應失敗）。`select_fidelity_candidate`/`build_fidelity_suggestion_
cards` 保持純函式（`outfit_variant_resolver` 注入），12 條單元測試涵蓋
「無 asset」「有 asset 但 sheet 無 source_path」「只有遮罩 asset（不算
IMAGE）」「多 asset 取最新」「多 sheet 取第一個有 source_path 的」等條件
組合。

**專案設定 `auto_loop_enabled`**（`ProjectSummary.auto_loop_enabled: bool
= False`，`core/project/manager.py` 持久化於 `project.json`，新
`PATCH /api/v1/projects/{project_id}/settings`）：`FidelityLoopStartRequest.
auto_continue` 型別改為 `bool | None`（原 `bool = False`）——`None`（省略）
代表「採用專案設定」，路由層（`core/main.py:start_fidelity_loop`）在呼叫
`FidelityService.start_loop` **之前**解出具體 `bool`，顯式帶入的
`True`/`False` 永遠優先；`fidelity_service.py` 對應保留一行
`bool(request.auto_continue)` 防禦性轉型（型別系統要求，非主要解析點，
主要解析點是路由層）。

**整合測試**（`tests/test_fidelity_loop_integration.py`，`pytest.mark.slow`）：
先檢查 Ollama `/api/tags` 是否列出 `MISAKA_OLLAMA_VISION_MODEL`、ComfyUI
`/system_stats`（`workers/manifest.json` 的 health_check URL）是否可達，
任一條件不成立就 `pytest.skip`（附原因字串），絕不讓一般 `pytest -q` 因缺
本機服務而變紅。條件成立時，對 `tests/fixtures/fidelity_solid_64x64.png`
（合成純色 PNG，stdlib 手刻，非 Pillow）跑**恰好一次**真實
`core.llm.vision.critique`（round-0 critique 的等價呼叫）——只斷言「回傳
N 條結果、每條 gate 欄位型別正確」，不斷言判定內容正確（純色圖顯然不會
真的通過角色比對）。ComfyUI 只做可達性探測（GET，無 payload），此測試**從
不**呼叫 `refine_asset`/`execute_job`，不會觸發任何真實 ComfyUI 生成。
本機環境當下 Ollama + ComfyUI 皆可達，此測試在本次驗收中**真實執行並通過**
（13.2s wall-clock，見下方驗收證據），非僅邏輯走查。

### 範圍外（前端待辦，C-spec.md §5）

- bbox 徽章疊圖（在版面上標出判官定位的失敗區域）。
- 啟動卡片 UI（渲染 `FidelitySuggestionCard` 為可點擊按鈕）+ auto-loop 開關
  UI（讀寫 `auto_loop_enabled`）。
- SSE 進度顯示 + 遮罩預覽。
- 版本樹 `best_asset_id` 標記。

後端資料層與 API 皆已就緒，前端目前完全沒有 UI 呈現這些欄位。

### 已知殘留缺口（登記，非本條目阻斷）

`params.negative: null` 被三處存在性判斷（`service.py:303`、
`comfyui.py:47`、`service.py:723`，皆屬 `BP-REFINE-1`）當成「已帶」，把
JSON `null` 持久化成字串 `"None"` 送進 ComfyUI（A 複審 2026-09-05 發現，
非阻斷，一行修正：`None` 視同缺鍵）。與本條目的 `auto_continue: bool |
None` 存在性/省略語意是同一類問題但不同欄位、不同檔案，**未**在 Brief 3
一併修正，於此登記追蹤（@PM `projects/misakaAssetGene.md`）。

### 驗收證據

- 單元測試：`tests/test_fidelity_parser.py`（Brief 1/2，解析器 + WAIST/TORSO
  優先序回歸）、`tests/test_vision_critic.py`（Brief 1，VLM 三閘門）、
  `tests/test_fidelity_loop_controller.py`（Brief 2，控制器 27 條）、
  `tests/test_fidelity_store.py`（Brief 2，持久化 9 條）、
  `tests/test_fidelity_loop_api.py`（Brief 2，API/SSE/併發/例外防護 10 條）、
  `tests/test_fidelity_suggestion_card.py`（Brief 3，純邏輯 12 條：emission
  條件全組合）、`tests/test_fidelity_suggestion_api.py`（Brief 3，API 12 條：
  clarify/session 卡片出現、`PATCH .../settings`、`auto_continue` 省略/顯式/
  專案預設三態）、`tests/test_fidelity_loop_integration.py`（Brief 3，整合
  1 條，`slow`，skip-if-unavailable）。
- `pytest -q`：**691 passed, 2 skipped**（Brief 2 完成時基準 666 passed，
  本次新增 25 條，含真實執行並通過的整合測試；skip 數與 Brief 2 完全相同，
  兩者皆為既有、與本次無關的既有 skip）。
- `quality-gates/python/run.py l0`：`[G1] PASS - 178 total, 0 new`／
  `[G2] PASS - 50 total, 0 new`／`[G3b]`／`[G4] PASS - 2 total, 0 new`。
- Blueprint lint：`py -3.11 D:/backup/CSIA/@PM/.claude/tools/blueprint-build.py
  --lint <repo>` PASS（見 `state/runs/misakaAssetGene-refine-loop-260905/
  C3-impl.md` 完整輸出）。
- 真實 API 路徑（8405，本機 Ollama/ComfyUI 皆可達）：建專案 → 建
  CharacterSheet（`sheet_source_path` 指向合成測試角色資料夾）→ 匯入 IMAGE
  asset → `POST .../consultant/clarify` 回應含
  `FidelitySuggestionCard`（見 `C3-impl.md` 附完整 JSON）→
  `PATCH .../settings {"auto_loop_enabled": true}` → `GET .../projects/{id}`
  回應 `auto_loop_enabled: true`。
