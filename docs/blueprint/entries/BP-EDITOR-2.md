---
id: BP-EDITOR-2
title: Mask-from-Regions API（bbox 遮罩自動產生）
system: editor
tags: [editor, mask, inpaint, comfyui, api]
status: 已完成
request_verbatim: "角色一致性自動精修迴圈 Feature B（@PM 派工 misakaAssetGene-refine-loop-260905，
  C-spec.md §4.2）：POST /assets/{asset_id}/mask，輸入 bbox regions（含 subtract/dilate/feather），
  輸出與來源同尺寸的白底黑遮罩 PNG，回傳 mask_asset_id，供 fidelity-loop 控制器（後續 Brief 2）
  自動組裝 RefineRequest.mask_asset_id 使用，不再需要人工先在 App 外部畫好遮罩上傳。"
decided_date: 2026-09-05
exec_links:
  - core/editor/mask.py
  - core/models/schemas.py
  - core/main.py
done_date: 2026-09-05
origin: "feat/mask-from-regions（Brief B，2026-09-05）：新路由
  POST /api/v1/projects/{project_id}/assets/{asset_id}/mask（core/main.py，緊接在既有
  assets/import 之後），純邏輯拆到新模組 core/editor/mask.py（此 repo venv 無 Pillow，
  PNG 讀寫皆為 stdlib struct+zlib 手刻）；schemas.py 新增 MaskRegion /
  MaskFromRegionsRequest / MaskFromRegionsResponse（插入於 RefineStrategy enum 之後，
  避開與同時進行中的 feat/refine-prompt-compose-negative 對 RefineRequest/AssetRecord/
  檔尾區段的編輯）。"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §10 基礎編輯工具 (Editor) — 承接 BP-EDITOR-1 手繪遮罩編輯器
---

## 設計說明

延續 `BP-EDITOR-1`（手繪 brush/bbox 遮罩編輯器）同一極性慣例——輸出白底黑遮罩，
ComfyUI `LoadImageMask(channel="red")` 讀紅色通道（`comfyui.py:316-319`）——新增一條
**伺服器端純邏輯**產生路徑：呼叫端只需給「哪些矩形要重繪／哪些矩形要排除」的像素座標，
不需要先在 App 外部手繪好再上傳（`H-report.md:36-38` 記錄的既有缺口）。

**規則（`core/editor/mask.py`）**：
- `regions`（聯集）與 `subtract`（自 `regions` 聯集中扣除）皆為
  `MaskRegion{bbox:[x0,y0,x1,y1], dilate=0, feather=0}`，`bbox` 為半開區間
  （`x0<=x<x1`、`y0<=y<y1`，Python slice 慣例）。
- **超界只 clamp，不拒絕**——回應 `clamped:true` 讓呼叫端知道有裁切發生；
  bbox 排序錯誤（`x1<=x0` 或 `y1<=y0`）才是硬性拒絕（400）。
- `dilate`：矩形四邊各向外擴 N px（再 clamp 到來源圖尺寸）。
- `feather`：擴張後矩形最外 N px 的邊界線性斜坡（`(dist_to_edge+1)/feather`），
  往內第 N px 起才是純白 1.0；`feather=0` 為硬邊。
- `coverage_ratio` = 全圖灰階平均值（`Σvalue / (width*height)`），會反映羽化造成的部分覆蓋。
- 輸出永遠與來源圖同寬高，8-bit RGB（三通道寫入同一灰階值，紅色通道即為
  `LoadImageMask` 實際讀取的值）。

**請求層級上限（2026-09-05 修正，`B-review.md` MAJOR 發現）**：原始實作對
`regions`/`subtract` 數量與來源圖尺寸皆無上限，`build_mask_png` 又對每個 region
各配置一個 `width*height` 浮點陣列——大圖＋大量 regions 可讓單一請求長時間佔用
CPU/記憶體卡死 core API。修法：
- `MaskRegion.dilate`/`feather`：`Field(ge=0, le=256)`（schemas.py）。
- `MaskFromRegionsRequest.regions`/`.subtract`：各 `max_length=32`（schemas.py）。
- `core/editor/mask.py` 新增模組層級常數 `MAX_MASK_PIXELS = 4096*4096`：
  `build_mask_png` 在配置任何 `width*height` 緩衝區**之前**先檢查來源圖
  `width*height`，超過即 raise `MaskRegionError`（既有型別，路由既有的
  `except (ImageHeaderError, MaskRegionError)` 直接回 400，未新增例外處理路徑）。
  未加新的 config/env（維持 merge-safety，不動 `config.py`/`.env.example`）。
- 記憶體/CPU 削減：`_accumulate_region`（原 `_region_contribution`）改為直接把
  每個 region 疊寫進共用的 `union`/`subtract_union` 緩衝區（僅在該 region 的
  dilate 後 bbox 範圍內迭代），不再為每個 region 各配置一份 `width*height`
  陣列後再逐像素合併——`union`/`subtract_union` 全程各只有一份
  `width*height` 緩衝區。像素結果與修法前逐位元組相同（原 14 條測試不變）。
- 本 repo 的 `RequestValidationError` 全域 handler（`main.py`）把所有
  pydantic 驗證錯誤（包含上述 `max_length`/`ge`/`le`）統一轉成 HTTP 400
  （非 FastAPI 預設 422），故新增測試斷言 400，而非字面上的 422。
- 新增測試（`tests/test_mask_from_regions.py`）：
  `test_mask_route_too_many_regions_rejected`、
  `test_mask_route_dilate_out_of_bounds_rejected`、
  `test_mask_route_oversize_source_returns_4xx`（假造 5000x5000 IHDR，
  不配置真實像素資料，驗證 cap 在配置緩衝區前就擋下）。

**Pillow 規避**：`uv sync --extra dev` 確認此 repo venv 未帶 Pillow；PNG 編碼
（`_write_png_rgb`，單一 `IDAT`、`filter type 0`）與來源圖尺寸探測（`read_image_size`，
解析 PNG `IHDR` / JPEG `SOF` marker）皆為 `struct`+`zlib` stdlib 手刻，未新增相依套件。

**路徑防護**：新路由讀來源圖 bytes 前，套用與 `GET /assets/{asset_id}/file`
（M5.3/M5.9）完全相同的 resolve→`relative_to(assets_root)` containment guard，
未重新發明路徑串接。輸出的遮罩透過既有 `GenerationService.import_asset`
（`service.py:314`，未修改）走 `assets/import` 同一條入庫路徑，`asset_type="mask"`。

**與併行中的 Feature A 分工**：`schemas.py` 新模型插入於 `RefineStrategy` enum
之後（約 L349 之後、`PromptDecompositionPass` 之前）——刻意避開同時進行中的
`feat/refine-prompt-compose-negative` 對 `RefineRequest`/`AssetRecord` 與檔尾區段
的編輯；`core/generation/service.py`、`adapters/comfyui.py`、`core/config.py`、
`.env.example` 本次完全未觸碰。

### 驗收證據

- `tests/test_mask_from_regions.py`（17 條，2026-09-05 修正後新增 3 條）：polarity
  （白=255 紅色通道）、union/dilate/subtract/feather 逐像素斷言、bbox clamp vs
  排序拒絕、stdlib PNG round-trip（自行從零寫的解碼器，非重用實作內部函式）、
  完整 API 路徑（建專案 → 匯入來源 PNG → 產生遮罩 → 經既有 `assets/{id}/file`
  路由取回位元組並逐像素核對）、請求層級上限（regions 過多／dilate 超界／
  來源圖超過 `MAX_MASK_PIXELS` 皆回 4xx）。
- `pytest -q`：559 passed, 2 skipped（併入本批 17 條後的總數；修正前 `fa99c7d`
  為 556 passed；base `a883e49` 為 542 passed）。
- `quality-gates/python/run.py l0`：G1/G2/G4 PASS（皆 0 new vs baseline）；
  G3(b) 對本批變更測試檔 PASS（assertion-presence，1 changed test file）。

**Consumed by**: `BP-REFINE-2`（角色一致性自動精修迴圈）——迴圈控制器每輪透過
`core.editor.mask.build_mask_png` + `subtract` 走同一段程式碼組裝遮罩（非自呼叫
HTTP），本條目的 `dilate`/`feather`/`subtract` 幾何規則即是迴圈每輪遮罩的實際行為。
