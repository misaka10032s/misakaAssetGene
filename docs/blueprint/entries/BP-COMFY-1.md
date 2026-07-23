---
id: BP-COMFY-1
title: ComfyUI Pipeline（inpaint / img2img / 多階段精修）
system: comfy
tags: [comfyui, inpaint, img2img, refine, multi-stage]
status: 已完成
request_verbatim: "M2 — ComfyUI 全深度（inpaint/img2img e2e, §5.11 多階段精修, §6.2 精修策略）（@PM 登記 M2，merged 928dd77）"
decided_date: 2026-06-12
exec_links:
  - core/generation/adapters/comfyui.py
  - core/generation/refine.py
done_date: 2026-06-12
origin: "M2 merge commit 928dd77（2026-06-12）『fix(security): M2(c) review findings — id sanitization, origins schema, upload guard, zip-bomb budget』；@PM 登記 70 tests green，3 review chains PASS，Chrome UI e2e 驗證真實 ComfyUI 生成 + img2img refine lineage + zip round-trip"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.11 圖像多階段生成與局部精修
---

## 設計說明

spec §5.11 定義圖像可多階段生成：先產出草圖/base，再局部精修（inpaint 遮罩區域重繪）或整張 img2img 微調，每次精修都在版本樹（`BP-VERSIONTREE-1`）上留下 parent-child 血緣。`core/generation/refine.py`（223 行）承載精修策略邏輯，與 `comfyui.py` adapter 協作。

### 現況核對（2026-07-23 盤點）

@PM 登記為 M2 里程碑（928dd77），並經三次 implementer→reviewer 審查鏈全數 PASS，Chrome UI e2e 對真實 ComfyUI 實例驗證過生成 + img2img refine 血緣鏈 + zip 匯入回圈。Inpaint 遮罩繪製 UI 屬另一條目（`BP-EDITOR-1`，M5.9 才補齊前端畫布，本條目涵蓋後端 pipeline 與精修策略）。
