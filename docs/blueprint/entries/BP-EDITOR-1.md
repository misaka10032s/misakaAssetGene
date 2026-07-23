---
id: BP-EDITOR-1
title: Inpaint Mask 遮罩編輯器
system: editor
tags: [editor, mask, inpaint, canvas]
status: 已完成
request_verbatim: "基礎編輯｜BPM、音量 normalize、亮度/對比、影片裁切、格式轉換（spec.md §2 Feature Matrix；§10 基礎編輯工具）（@PM 登記 M5.9，2026-06-14）"
decided_date: 2026-06-14
exec_links:
  - frontend/src/components/InpaintMaskEditor.vue
  - core/main.py
done_date: 2026-06-14
origin: "M5.9 commit ef5641b（2026-06-14）『feat(frontend): inpaint mask-painting canvas editor (brush + bbox) wired to refine (M5.9)』，reviewer 驗證 mask polarity + 新路由安全性"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §10 基礎編輯工具 (Editor)
---

## 設計說明

畫布式遮罩編輯器（brush 塗抹/擦除/矩形選取/清除/undo），輸出白底黑遮罩 PNG（符合 ComfyUI `LoadImageMask` 的 channel=red 慣例），直接接進 `BP-COMFY-1` 的 inpaint 精修流程。新增 `GET /assets/{id}/file` 路由供編輯器讀取原圖，內建路徑防護。

### 現況核對（2026-07-23 盤點）

`frontend/src/components/InpaintMaskEditor.vue` 存在；`core/main.py:633` 確認 `/api/v1/projects/{project_id}/assets/{asset_id}/file` 路由存在。@PM 登記 reviewer 專門驗證了「遮罩極性（白/黑對應是否正確）」與「新路由的路徑防護」兩項，PASS。**範圍澄清**：spec §10「基礎編輯工具」條列項目其實還包含 BPM 偵測、音量 normalize、亮度/對比調整、影片裁切、格式轉換等——本條目僅涵蓋 Inpaint Mask 編輯器這一項（M5.9 唯一有登記證據的子功能）；其餘音訊/影片基礎編輯項目在 @PM roadmap 與程式碼中均未找到對應實作，未另立條目（規模太小、證據不足以判定狀態），列為待覆核的落差。
