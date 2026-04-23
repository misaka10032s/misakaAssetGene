# MisakaAssetGene Claude 協作總則

本檔是專案層級的 Claude 指南。任何新需求、規格更新、開發規劃與實作討論，都先以 `spec.md` 為單一事實來源，再參考 `.plan/` 與 `.claude/` 內的模組化規則。

## 核心原則

1. **Spec-first：** 有新需求時，先用 `spec.md` 與 `architect` 角色討論可行性、架構影響、風險與落地方式，確認後才更新 `spec.md`。
2. **Plan-aware：** `.plan/DEVELOPMENT_PLAN.md` 定義開發角色與流程；`.plan/RESEARCH_LOG.md` 記錄研究結論與規格修正。完成的事項要在研究日誌中標註為 **「已完成」**。
3. **Repo boundary：** 第三方 repo 一律視為外部依賴，使用獨立 clone 或下載產物，不可被本專案 git 追蹤，不使用 submodule / subtree。
4. **Multimodal by default：** 功能設計不得只假設單一素材輸出；必須能處理圖像、文字台詞、角色語音、歌曲、影片、靜態動圖等複合交付。
5. **Open-source friendly：** 任何流程、規格與文件都應考慮外部貢獻者可讀性、可執行性與授權清晰度。
6. **Truthful delivery:** 不得把骨架、stub、PoC 說成完整里程碑完成；回報時要明確區分「已完成」、「部分完成」、「未完成」。

## 工作入口

- 規格討論：使用 `.claude/commands/spec-discuss.md`
- 規格同步：使用 `.claude/commands/update-spec.md`
- 規劃檢查：使用 `.claude/commands/review-plan.md`

## 規則模組

- `.claude/rules/spec-workflow.md`：需求到規格的標準流程
- `.claude/rules/multimodal-assets.md`：複合素材與輸出設計約束
- `.claude/rules/repo-hygiene.md`：repo 邊界、gitignore 與外部依賴規則
- `.claude/rules/community-workflow.md`：開源貢獻與 review 流程
- `.claude/rules/frontend-standards.md`：前端 i18n、型別、RWD、樣式與註解規範

## 開發模式與檢測規範

1. **開發期診斷輸出必須受 mode / env 控制。**
   - Python backend 使用 `MISAKA_ENV=dev`
   - Frontend / Vite 使用 `VITE_MISAKA_ENV=dev` 與 `--mode development`
   - production build 不得預設輸出開發期 debug 訊息
2. **build 與 dev 要隔離。**
   - dev server、typecheck、build、doctor、manager 都必須有明確命令入口
   - 驗證時需說明是 dev 驗證、build 驗證、還是 API/行為驗證
3. **開發期訊息只服務於驗證，不可污染正式使用者體驗。**
4. **若新增診斷輸出，必須同時寫明啟動方式、預期訊息、以及停用條件。**
5. **env 採集中管理、命名分流。**
   - root `.env` / `.env.example` 為單一來源
   - backend 讀 `MISAKA_*` 與 provider secrets
   - frontend 只讀 `VITE_MISAKA_*`
   - 不允許前後端各自維護互相漂移的 env 檔語意

## 回報格式（每次開發進度都必須包含）

1. **現在進度到哪裡**：對應 `spec.md` / milestone / 條目
2. **你該如何驗證**：命令、頁面、API、預期輸出
3. **目前判定**：已完成 / 部分完成 / 未完成
4. **下一步做什麼**：下一個最合理的開發或驗收動作

若是里程碑驗收，還要額外列出：
- 哪些條目通過
- 哪些條目仍缺
- 哪些只是 scaffold / stub

## 角色分工

| 角色 | 主要職責 |
| --- | --- |
| `architect` | 需求可行性、系統分層、規格把關 |
| `backend` | FastAPI 核心、檔案系統、專案管理、metadata |
| `ai-ml` | RAG、prompt engineering、LLM 路由、生成/訓練工作流 |
| `frontend` | Tauri/Vue UI、版本樹、資產瀏覽與互動 |
| `ui-ux` | 對話體驗、視覺層次、引導與易用性 |
| `devops` | setup、打包、跨平台安裝、工具與 worker 管理 |
| `qa-sdet` | smoke / integration / E2E 測試策略 |
| `security` | 權限邊界、命令安全、敏感資訊脫敏 |

詳細 persona 見 `.claude/agents/`。
