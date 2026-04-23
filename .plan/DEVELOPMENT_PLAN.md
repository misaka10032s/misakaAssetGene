# 開發與測試計畫 (DEVELOPMENT_PLAN.md)

> **專案：** MisakaAssetGene  
> **版本：** v1.0  
> **目的：** 定義開發流程、角色編制與品質保證策略。

---

## 1. 開發角色定義 (The Dream Team)

| 角色 | 職責重點 |
| :--- | :--- |
| **架構師 (Architect)** | 定義系統分層、VRAM Scheduler 邏輯、各模態 Adapter Interface。 |
| **後端工程師 (Backend)** | 實作 FastAPI 核心、Worker 子進程管理、檔案系統操作、Metadata 嵌入、專案管理。 |
| **AI 應用工程師 (AI/ML)** | RAG 記憶設計 (ChromaDB)、Prompt 工程、LLM 路由、Worker 邏輯封裝。 |
| **前端工程師 (Frontend)** | 實作 Tauri 介面、版本樹狀 UI (SVG/Canvas)、資產瀏覽器、Chat 互動、跨專案引用 Chip。 |
| **UI/UX 設計師** | 設計創作顧問對話流、風格指南編輯器、一鍵安裝 (Setup) 介面視覺、跨專案引用的視覺層次。 |
| **分發與運維 (DevOps)** | 撰寫 `setup.ps1/sh`、uv 打包策略、處理 Embedded Python 的各平台相容性。 |
| **測試工程師 (QA/SDET)** | 撰寫各 Worker Smoke Test、跨平台安裝測試、RAG 準確度測試、Tauri E2E 測試。 |
| **安全專家 (Security)** | 稽核 Agent 執行權限，確保所有 Shell 命令都在安全邊界內，負責對話內容脫敏策略。 |

---

## 2. 開發工作流 (Workflows)

### 2.1 研究與策略 (Research & Strategy)
- 所有 Feature 在開發前必須撰寫 **「技術方案筆記」**，說明是否引用現有庫。
- API 介面變更必須先更新 `spec.md` 或對應的 API Schema。

### 2.2 實作與驗證 (Act & Validate)
- **Git 管理：** 採用 Feature Branch 模式。
- **煙霧測試 (Smoke Test)：** 每個模態 (Modality) 的 Adapter 必須附帶一個 Python 測試腳本，驗證「輸入 Prompt -> 產出檔案 -> 解析 Metadata」是否成功。
- **PR 稽核：** 所有 PR 必須通過 Linter (eslint/pyright) 檢查並經過至少一位其他角色的 Code Review。

---

## 3. 測試策略 (Testing Strategy)

我們採用四層測試模型以確保軟體穩定性：

| 測試層次 | 工具 | 說明 |
| :--- | :--- | :--- |
| **單元測試 (Unit)** | `pytest`, `vitest` | 針對路徑解析、Metadata 讀寫、向量檢索、相對路徑轉換等核心算法。 |
| **整合測試 (Integration)** | `FastAPI TestClient` | 驗證 Core Service 與 Backend Workers 之間的 HTTP 通訊與 Port 偵測。 |
| **冒煙測試 (Smoke)** | `scripts/smoke/` | 針對特定模態執行一組最小生成任務，確保模型能正確加載且無 OOM。 |
| **端到端測試 (E2E)** | `Playwright (Tauri)` | 模擬使用者從「建立專案」到「產出素材」的全流程視覺化操作。 |

### 3.1 跨平台安裝測試 (Compatibility)
- 在 Windows (10/11)、macOS (Intel/M-series)、Ubuntu Linux 下執行 `./scripts/setup.sh`。
- 驗證「零安裝」是否成立，即不依賴系統原有的 Python 或 CUDA 驅動 (除基礎 Driver 外)。

---

## 4. 專案里程碑 (Key Milestones)

- **M0 (兩週)：** 基礎建設。Tauri + FastAPI 框架、專案結構、uv 環境自動下載。
- **M1 (三週)：** 創作顧問原型。完成音樂模態 (MusicGen) 與 Cold Start 範例生成。
- **M2 (四週)：** 核心與可攜性。完成 ComfyUI 整合、RAG 檢索、跨專案引用與專案 zip 匯入。
- **M3 (四週)：** 離線與優化。完成離線三態偵測、VRAM 熱切換、所有 Worker 的 Smoke Test。
- **M4 (四週)：** 訓練與打包。完成 LoRA/TTS 訓練整合、Portable Release 打包與 Setup 錯誤 AI 解釋。
- **M5 (持續)：** 打磨。樹狀 UI 視覺化、License Report、跨專案引用廢棄遷移。
