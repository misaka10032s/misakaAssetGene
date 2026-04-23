# MisakaAssetGene

## zh-TW

MisakaAssetGene 是桌面優先的多模態素材工作台，用顧問式對話整合圖像、台詞文字、角色語音、歌曲、影片與後續訓練流程。

### 啟動專案

1. 複製 `.env.example` 為 `.env`
2. 先執行一鍵 setup 安裝依賴與 Local LLM
3. 用單一指令啟動前端、後端與 local LLM

```powershell
npm run setup
npm run start:dev
```

### Port 與服務

- Frontend: `http://127.0.0.1:7501`
- Core API: `http://127.0.0.1:7500`
- Ollama: `http://127.0.0.1:11434`

### 常用指令

- `npm run start:dev`：啟動前端、後端，以及可選的 Ollama 自動啟動流程
- `npm run dev`：只啟動前端
- `npm run dev:core`：只啟動 FastAPI Core API
- `npm run build`：建置前端產物到 `dist/`
- `npm run manager`：查看整合管理資訊

### 模型路徑

- 預設模型搜尋路徑為專案內的 `.model/`
- 額外路徑可用 `MISAKA_EXTRA_MODEL_PATHS` 設定，使用分號分隔並依順位搜尋
- 若需自動啟動 Ollama，可把 `MISAKA_AUTO_START_OLLAMA=true`
- `MISAKA_LLM_PROVIDER_ORDER` 可設定 synopsis optimize 的 LLM 嘗試順序，預設先走 `ollama`
- Cloud provider 可透過 `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`GEMINI_API_KEY` 設定

## en

MisakaAssetGene is a desktop-first multimodal asset workspace that orchestrates images, dialogue text, voice, songs, video, and downstream training through a consultant-style workflow.

### Start the project

1. Copy `.env.example` to `.env`
2. Run one-click setup to install dependencies and the local LLM
3. Start the full local stack with one command

```powershell
npm run setup
npm run start:dev
```

### Ports and services

- Frontend: `http://127.0.0.1:7501`
- Core API: `http://127.0.0.1:7500`
- Ollama: `http://127.0.0.1:11434`

### Common commands

- `npm run start:dev`: start frontend, backend, and optional Ollama auto-start
- `npm run dev`: start the frontend only
- `npm run dev:core`: start the FastAPI Core API only
- `npm run build`: build the frontend into `dist/`
- `npm run manager`: inspect integration metadata

### Model paths

- The default model search root is `.model/` inside this repository
- Add more search locations with `MISAKA_EXTRA_MODEL_PATHS`, separated by semicolons and resolved in order
- Set `MISAKA_AUTO_START_OLLAMA=true` if you want the dev stack to start Ollama automatically
- `MISAKA_LLM_PROVIDER_ORDER` controls the synopsis optimize provider order, and defaults to `ollama`
- Configure cloud providers with `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `GEMINI_API_KEY`

## ja

MisakaAssetGene は、画像・台詞テキスト・音声・楽曲・動画・学習フローをコンサルタント型ワークフローで統合する、デスクトップ優先のマルチモーダル素材ワークスペースです。

### 起動方法

1. `.env.example` を `.env` にコピー
2. one-click setup で依存関係と Local LLM をインストール
3. 単一コマンドでローカルスタックを起動

```powershell
npm run setup
npm run start:dev
```

### ポートとサービス

- Frontend: `http://127.0.0.1:7501`
- Core API: `http://127.0.0.1:7500`
- Ollama: `http://127.0.0.1:11434`

### よく使うコマンド

- `npm run start:dev`: フロントエンド、バックエンド、任意の Ollama 自動起動をまとめて開始
- `npm run dev`: フロントエンドのみ起動
- `npm run dev:core`: FastAPI Core API のみ起動
- `npm run build`: フロントエンドを `dist/` にビルド
- `npm run manager`: 統合メタデータを確認

### モデルパス

- 既定のモデル検索ルートはこのリポジトリ内の `.model/`
- `MISAKA_EXTRA_MODEL_PATHS` にセミコロン区切りで追加パスを指定可能
- `MISAKA_AUTO_START_OLLAMA=true` を設定すると dev stack から Ollama を自動起動できます
- `MISAKA_LLM_PROVIDER_ORDER` で synopsis optimize に使う LLM の試行順を設定できます。既定値は `ollama` です
- Cloud provider は `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`GEMINI_API_KEY` で設定します
