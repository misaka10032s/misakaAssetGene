# MisakaAssetGene

桌面優先的多模態素材工作台，用顧問式對話整合圖像、台詞文字、角色語音、歌曲、影片與後續訓練流程。

> 規格唯一事實來源：[`.claude/context/spec.md`](.claude/context/spec.md)（spec-first）。

---

## 技術棧

| 層級 | 技術 |
|------|------|
| 桌面殼層 | Tauri 2 |
| 前端 | Vue 3.5 + Vite 5 + TypeScript（strict） |
| 路由 / 狀態 | Vue Router 4 / Pinia 2 |
| 樣式 | UnoCSS 66 |
| i18n | Vue-i18n 9（zh-TW / en / ja） |
| Core API | Python + FastAPI + uvicorn |
| 本地 LLM | Ollama（外部，`11434`） |
| Workers | ComfyUI / ACE-Step / VoxCPM / GPT-SoVITS / Ultimate-RVC（外部依賴） |

---

## 應用區塊（App sections）

| 區塊 | 說明 |
|------|------|
| 顧問對話 | 以對話釐清需求，產生多模態素材計畫 |
| 專案工作台 | 建立 / 選擇專案、瀏覽資產與版本樹 |
| 生成與精修 | 圖像、台詞、語音、歌曲、影片的生成與 refine 工作流 |
| 訓練流程 | 後續模型訓練任務的提交與追蹤 |
| 整合管理 | tools / workers / 模型登錄、本地 LLM 與網路狀態 |
| 專案匯入匯出 | `*.misaka.zip` 封裝、授權報告與可攜性 |

---

## Port 與服務

所有本機服務均綁定 `127.0.0.1`，port 由 root `.env` 集中定義（test == prod ports）：

- Frontend: `http://127.0.0.1:8400`
- Core API: `http://127.0.0.1:8401`
- Ollama: `http://127.0.0.1:11434`（外部）

---

## 啟動專案

1. 複製 `.env.example` 為 `.env`
2. 執行一鍵 setup 安裝依賴與 Local LLM
3. 啟動前端、後端與 local LLM

```powershell
npm run setup
npm run start:dev
```

### 開發啟動器（dev launchers）

| 檔案 | 啟動內容 | Port |
|------|----------|------|
| `be.dev.cmd` | Core API（uvicorn `core.main:app`，`--reload`） | `127.0.0.1:8401` |
| `fe.dev.cmd` | 前端 Vite dev server | `127.0.0.1:8400` |

---

## 常用指令

- `npm run start:dev`：啟動前端、後端，以及可選的 Ollama 自動啟動流程
- `npm run dev`：只啟動前端
- `npm run dev:core`：只啟動 FastAPI Core API
- `npm run build`：建置前端產物到 `dist/`
- `npm run typecheck`：前端型別檢查（vue-tsc）
- `npm run doctor`：環境診斷
- `npm run manager`：查看整合管理資訊
- 前端 dev 模式內建 Vue component inspector：按右下角 inspector 按鈕，或按 `Ctrl+Shift` 切換後點選元件，可直接用 VS Code 開啟對應檔案與行數

---

## 環境變數

env 採集中管理、命名分流：root `.env` / `.env.example` 為單一來源；backend 讀 `MISAKA_*` 與 provider secrets，frontend 只讀 `VITE_MISAKA_*`。

| 變數 | 用途 |
|------|------|
| `MISAKA_ENV` | backend 開發模式（`dev` 啟用診斷輸出） |
| `VITE_MISAKA_ENV` | frontend 開發模式 |
| `MISAKA_FRONTEND_PORT` | 前端 dev server port（預設 `8400`） |
| `MISAKA_EXTRA_MODEL_PATHS` | 額外模型搜尋路徑（分號分隔、依序搜尋） |
| `MISAKA_AUTO_START_OLLAMA` | 設 `true` 由 dev stack 自動啟動 Ollama |
| `MISAKA_LLM_PROVIDER_ORDER` | synopsis optimize 的 LLM 嘗試順序（預設 `ollama`） |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | Cloud provider 金鑰 |

> 模型搜尋預設根目錄為專案內的 `.model/`。

---

## en

MisakaAssetGene is a desktop-first multimodal asset workspace that orchestrates images, dialogue text, voice, songs, video, and downstream training through a consultant-style workflow.

Single source of truth: [`.claude/context/spec.md`](.claude/context/spec.md) (spec-first).

### Start

```powershell
npm run setup
npm run start:dev
```

- Frontend: `http://127.0.0.1:8400`, Core API: `http://127.0.0.1:8401`, Ollama: `http://127.0.0.1:11434` (all `127.0.0.1`, test == prod ports)
- Dev launchers: `be.dev.cmd` (Core API :8401) / `fe.dev.cmd` (Frontend :8400)
- `npm run dev` (frontend only) / `npm run dev:core` (FastAPI only) / `npm run build` (build into `dist/`)
- env split: root `.env` is the single source; backend reads `MISAKA_*`, frontend reads `VITE_MISAKA_*` only
- Configure cloud providers with `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`; default model search root is `.model/`

---

## ja

MisakaAssetGene は、画像・台詞テキスト・音声・楽曲・動画・学習フローをコンサルタント型ワークフローで統合する、デスクトップ優先のマルチモーダル素材ワークスペースです。

単一の真実のソース：[`.claude/context/spec.md`](.claude/context/spec.md)（spec-first）。

### 起動

```powershell
npm run setup
npm run start:dev
```

- Frontend: `http://127.0.0.1:8400`、Core API: `http://127.0.0.1:8401`、Ollama: `http://127.0.0.1:11434`（すべて `127.0.0.1`、test == prod ports）
- dev launchers: `be.dev.cmd`（Core API :8401）/ `fe.dev.cmd`（Frontend :8400）
- `npm run dev`（フロントエンドのみ）/ `npm run dev:core`（FastAPI のみ）/ `npm run build`（`dist/` へビルド）
- env 分離：root `.env` が単一ソース。backend は `MISAKA_*`、frontend は `VITE_MISAKA_*` のみ
- Cloud provider は `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`GEMINI_API_KEY` で設定。既定のモデル検索ルートは `.model/`
