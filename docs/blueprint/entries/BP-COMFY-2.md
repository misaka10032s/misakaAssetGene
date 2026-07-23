---
id: BP-COMFY-2
title: 修圖/修影片/修音效策略選擇原則
system: comfy
tags: [comfy, refine-strategy, decision-rule]
status: 已完成
request_verbatim: "修圖 / 修影片 / 修音效的策略選擇原則（spec.md §6.2）"
decided_date: 2026-06-12
exec_links:
  - core/generation/refine.py
done_date: 2026-06-12
origin: "隨 M2（928dd77，2026-06-12）ComfyUI 全深度一併落地，見 core/generation/refine.py"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §6.2 修圖 / 修影片 / 修音效的策略選擇原則
---

## 設計說明

spec §6.2 定義「什麼情況該用 inpaint、什麼情況該整張重繪、什麼情況該直接調參數而非重新生成」的決策原則，跨圖像/影片/音效三種模態共用同一套判斷邏輯骨架（依素材類型分派到對應 adapter 的精修入口）。

### 現況核對（2026-07-23 盤點）

圖像精修策略在 `core/generation/refine.py` 落地並經 M2 e2e 驗證（見 `BP-COMFY-1`）。影片/音效的精修策略是否同樣完整覆蓋 §6.2 全部決策分支未逐一核對（`video_backend.py`/音訊 adapter 群目前功能較單純，多為單次生成），此為待覆核項，非本次盤點可下「已完整覆蓋三模態」的結論——狀態判定「已完成」僅指圖像精修策略路徑。
