---
id: BP-RAG-1
title: 專案記憶 RAG 策略與專案結構
system: rag
tags: [rag, memory, project-structure, embedding]
status: 已完成
request_verbatim: "專案記憶 (RAG)｜向量化歷史對話、素材描述、使用者偏好，生成前自動注入（spec.md §2 Feature Matrix，P0；§5.2 RAG 記憶策略）"
decided_date: 2026-05-07
exec_links:
  - core/project/manager.py
  - docs/superpowers/specs/spec.md
done_date: 2026-05-07
origin: "M1 commit ca99f10（2026-05-07）『M1~4 XD』首次引入 core/project 專案管理 + RAG 記憶；M2（928dd77，2026-06-12）針對 id 消毒/origins schema/上傳防護做安全加固"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.1 專案結構 / §5.2 RAG 記憶策略
---

## 設計說明

每個專案在磁碟上有固定結構（`project.json` + `style_guide.md` + 對話紀錄 + assets），`core/project/manager.py` 的 `ProjectManager` 負責建立/切換/讀取專案；spec §5.2 定義生成前把歷史對話、素材描述、使用者偏好向量化後注入 prompt context，不是每次都重新讀全部歷史。

### 現況核對（2026-07-23 盤點）

`ProjectManager.get_project()`/`select_project()`/`initialize_project()` 存在且對 `project_id` 做白名單驗證（`validate_project_id`，`^[a-z0-9_-]+$`，commit 062eb13 2026-06-20 修復路徑穿越漏洞，見 `BP-SECURITY-2`）。M2 review（928dd77）加固了 id 消毒與 origins schema。RAG 向量化注入的具體檢索邏輯未逐行核對嵌入模型選型（超出本次盤點範圍），但專案結構與記憶注入管線在 M1~M2 已隨顧問引擎（`BP-CONSULT-1`）與生成服務串接使用。
