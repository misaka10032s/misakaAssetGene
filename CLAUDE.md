# MisakaAssetGene Claude 協作總則

本檔是專案層級的 Claude 指南。任何新需求、規格更新、開發規劃與實作討論，都先以 `spec.md` 為單一事實來源，再參考 `.plan/` 與 `.claude/` 內的模組化規則。

## 核心原則

1. **Spec-first：** 有新需求時，先用 `spec.md` 與 `architect` 角色討論可行性、架構影響、風險與落地方式，確認後才更新 `spec.md`。
2. **Plan-aware：** `.plan/DEVELOPMENT_PLAN.md` 定義開發角色與流程；`.plan/RESEARCH_LOG.md` 記錄研究結論與規格修正。完成的事項要在研究日誌中標註為 **「已完成」**。
3. **Repo boundary：** 第三方 repo 一律視為外部依賴，使用獨立 clone 或下載產物，不可被本專案 git 追蹤，不使用 submodule / subtree。
4. **Multimodal by default：** 功能設計不得只假設單一素材輸出；必須能處理圖像、文字台詞、角色語音、歌曲、影片、靜態動圖等複合交付。
5. **Open-source friendly：** 任何流程、規格與文件都應考慮外部貢獻者可讀性、可執行性與授權清晰度。

## 工作入口

- 規格討論：使用 `.claude/commands/spec-discuss.md`
- 規格同步：使用 `.claude/commands/update-spec.md`
- 規劃檢查：使用 `.claude/commands/review-plan.md`

## 規則模組

- `.claude/rules/spec-workflow.md`：需求到規格的標準流程
- `.claude/rules/multimodal-assets.md`：複合素材與輸出設計約束
- `.claude/rules/repo-hygiene.md`：repo 邊界、gitignore 與外部依賴規則
- `.claude/rules/community-workflow.md`：開源貢獻與 review 流程

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
