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
- API 介面變更必須先更新 `docs/superpowers/specs/spec.md` 或對應的 API Schema。

### 2.2 實作與驗證 (Act & Validate)
- **Git 管理：** 採用 Feature Branch 模式。
- **煙霧測試 (Smoke Test)：** 每個模態 (Modality) 的 Adapter 必須附帶一個 Python 測試腳本，驗證「輸入 Prompt -> 產出檔案 -> 解析 Metadata」是否成功。
- **PR 稽核：** 所有 PR 必須通過 Linter (eslint/pyright) 檢查並經過至少一位其他角色的 Code Review。

---

## 2.3 流程節奏與驗收規範（Process Cadence）
> 決策日：2026-06-12

- **里程碑驗收閘**：每個里程碑（M0–M5）結束時，須經**使用者驗收 checkpoint** 才能進入下一里程碑。未通過驗收的功能需補齊後再關閉。
- **實作 → 審查 chain**：每個里程碑的實作由 implementer agent 執行，完成後必須交由**獨立 reviewer agent** 進行 code review；implementer 不得自審。Reviewer 意見由 PM（main agent）彙整後回饋給 implementer 修正。
- **規格優先**：任何實作前若需求與 `docs/superpowers/specs/spec.md` 不一致，須先更新 spec（走 spec-discuss → update-spec 流程），再進行實作。
- 本節規定適用於所有未完成的里程碑（M2 起）。

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
- **M1 (三週)：** 創作顧問原型。完成音樂模態 (ACE-Step-1.5) 與 Cold Start 範例生成。
- **M2 (四週)：** 核心與可攜性。
  - ComfyUI 完整深度整合：inpaint / img2img e2e、§5.11 多階段精修、§6.2 修圖策略決策樹
  - 顧問狀態機 SQLite 持久化（§5.12、§4.1.1）：session 表於 `memory.sqlite`，loop 直到 checklist 齊全跨重啟可繼續
  - 專案可攜性：zip 匯入/匯出、relative path normalize
  - 跨專案引用 RW Lock（§3 研究日誌條目 4）
  - ChromaDB RAG ingest / retrieve
- **M3 (四週)：** 離線與優化。
  - 離線三態（Auto / Always Offline / Always Online）
  - VRAM Warm 狀態（§3.4）
  - Worker runtime readiness（install / start / stop / smoke / managed_pid / readiness_note，§5.13）
  - Conversation 效能（分批載入、虛擬滾動，§5.14）
  - 所有 Worker Smoke Test 全數通過
- **M4 (四週)：** 訓練整合 + 打包。完整對應 spec §7.1.1：
  - `character sheets`（角色名、外觀錨點、觸發詞、禁忌特徵、參考圖）
  - `dataset packs`（蒐集來源、清洗狀態、tag、授權、切分方式）
  - `training recipes`（底模、rank、epoch、optimizer、caption strategy）
  - `lora stack presets`（角色 LoRA、服裝 LoRA、風格 LoRA 的常用組合）
  - kohya_ss LoRA 整合 end-to-end
  - GPT-SoVITS TTS fine-tune end-to-end
  - Portable Release：Tauri bundle + embedded Python + 附帶 uv/ffmpeg
  - Setup 錯誤 AI 解釋完整版（友善錯誤處理 §11.3）
- **M5 (持續)：** 打磨。
  - 版本樹狀 UI（升級為 parent-child 樹 + diff，§8.2 P1）
  - License Report 完整版（§2 Feature Matrix）
  - 跨專案引用廢棄遷移工具（把引用實體化為本地複本）
  - 日誌脫敏完整實施（§3 RESEARCH_LOG 條目 5）
