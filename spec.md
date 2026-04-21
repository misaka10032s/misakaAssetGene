# MisakaAssetGene — 專案規格書 (Spec)

> 對話式 AI 創作顧問：整合圖像、聲音、影片的**生成 + 訓練**工作台，以專案為單位維持記憶與風格一致性，為不懂技術的個人創作者（遊戲開發、內容創作）而設計。

---

## 1. 專案定位 (Positioning)

### 1.1 是什麼
- **個人用桌面軟體**，clone 下來自己跑，**不是 SaaS 平台**。
- **整合層 (Orchestration Hub)**：把散落的開源 repo（ComfyUI、AudioCraft、GPT-SoVITS、kohya_ss…）包成統一介面，用一位「AI 顧問」引導使用者完成生成與訓練。
- **對話為主、參數為輔**：使用者只需用自然語言描述需求，顧問負責把模糊語意轉成精確的生成參數。

### 1.2 明確的非目標 (Non-Goals)
| 不做 | 原因 |
|---|---|
| 使用者帳號 / 雲端同步 / 協作 | 單機個人用 |
| 付費計費 / 用量儀表板 | 使用 cloud API 時，由 provider 自己的後台警告用量 |
| API 金鑰加密保險箱 | 本地 `.env` + `.gitignore` 即可 |
| 內容審查 / 安全過濾 | 刻意避免，local 生成的意義之一就是自由；違法內容散播責任歸使用者 |
| 網頁版 / 手機版 | 桌面專屬，需要直接存取 GPU 與檔案系統 |
| 多租戶 / 權限控制 | 同上 |

### 1.3 目標使用者
- 獨立遊戲開發者、同人作者、TTRPG GM、小型工作室。
- **不懂 Python、不會裝 CUDA、不想讀 ComfyUI 節點圖**，但願意裝一個桌面 app。
- 可能有一張消費級 GPU（8–24GB VRAM），或願意付費用 cloud API。

---

## 2. 核心功能總覽 (Feature Matrix)

| 模組 | 功能 | 優先級 |
|---|---|---|
| **顧問引擎** | 分輪漸進提問、候選選項、摘要確認、對話式修正 | P0 |
| **專案管理** | 建立/切換/匯出專案、專案層級設定 | P0 |
| **專案記憶 (RAG)** | 向量化歷史對話、素材描述、使用者偏好，生成前自動注入 | P0 |
| **多模態生成** | 圖像、BGM、SFX、角色配音、影片 | P0 |
| **版本控制** | 樹狀 (parent-child) 版本、隨時 rollback、對比 diff | P0 |
| **Local 模型管理** | 下載、切換、刪除 checkpoints / LoRA / GGUF / TTS 模型 | P0 |
| **LLM 路由** | 統一介面切換 Ollama / llama.cpp / Anthropic / OpenAI / Gemini / OpenRouter | P0 |
| **訓練整合** | 圖像 LoRA、TTS voice clone、（未來）音樂風格微調 | P1 |
| **基礎編輯** | BPM、音量 normalize、亮度/對比、影片裁切、格式轉換 | P1 |
| **風格指南** | 專案級 style guide（色票、關鍵字、參考、固定 seed） | P1 |
| **批次變體** | 一次產 N 個候選讓使用者挑 | P1 |
| **Prompt 模板庫** | 儲存/匯入常用模板 | P2 |
| **遊戲引擎匯出** | 匯出為 Unity / Godot / Unreal 可用的資料夾結構與 metadata | P2 |

P0 = MVP；P1 = v1.0；P2 = 之後

---

## 3. 系統架構 (Architecture)

### 3.1 整體分層

```
┌───────────────────────────────────────────────────────────┐
│  UI Layer — Tauri (Rust shell) + Vue3 + TypeScript        │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────────┐    │
│  │ Chat    │ │ Asset   │ │ Project  │ │ Model        │    │
│  │ Panel   │ │ Browser │ │ Manager  │ │ Manager      │    │
│  └─────────┘ └─────────┘ └──────────┘ └──────────────┘    │
└──────────────────────────┬────────────────────────────────┘
                           │ IPC (Tauri invoke) / WebSocket
┌──────────────────────────▼────────────────────────────────┐
│  Core Service — Python (FastAPI, local 127.0.0.1)         │
│  ┌──────────────────┐  ┌─────────────────────────────┐    │
│  │ Consultant       │  │ Project / Memory (RAG)      │    │
│  │ Engine           │──│ - SQLite (metadata)         │    │
│  │ (LLM router)     │  │ - ChromaDB (vector)         │    │
│  └────────┬─────────┘  └─────────────────────────────┘    │
│           │                                               │
│  ┌────────▼─────────────────────────────────────────┐     │
│  │ Generation Orchestrator (adapter pattern)        │     │
│  │  Image │ Music │ SFX │ Voice │ Video             │     │
│  └────────┬─────────────────────────────────────────┘     │
│           │                                               │
│  ┌────────▼──────────┐  ┌─────────────────┐               │
│  │ Training Engine   │  │ Editor Tools    │               │
│  │ (LoRA, TTS clone) │  │ (FFmpeg/PIL)    │               │
│  └────────┬──────────┘  └─────────────────┘               │
└───────────┼───────────────────────────────────────────────┘
            │ HTTP / subprocess
┌───────────▼───────────────────────────────────────────────┐
│  Backend Workers (各自獨立 process，可 on-demand 啟停)     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │
│  │ ComfyUI  │ │AudioCraft│ │GPT-SoVITS│ │ Ollama   │      │
│  │ (img/vid)│ │(music/sfx)│ │ /F5-TTS │ │ /llama   │      │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘      │
└───────────────────────────────────────────────────────────┘
```

### 3.2 為什麼這樣分

- **Tauri 而非 Electron**：體積小 10×，冷啟動快，Rust 原生整合 OS keychain（未來萬一要加）。
- **Python 核心服務**：AI 生態完整；不 embed 進 Tauri，獨立 process 以便除錯、熱重載、共用 GPU。
- **Worker 獨立進程**：每個開源 repo 有自己的 venv / 依賴衝突。以 subprocess 或 Docker 啟動，透過 HTTP 溝通（ComfyUI 有 API、AudioCraft 自己包一層 FastAPI）。
- **Adapter Pattern**：Generation Orchestrator 定義統一 interface，每種 backend 寫一個 adapter。未來替換 backend（例如 SDXL → Flux）只改 adapter。

---

## 4. 顧問引擎 (Consultant Engine)

### 4.1 對話流程狀態機

```
[Intake]  → 使用者丟出需求
    ↓
[Clarify] → 顧問檢查 "領域 checklist" 哪些缺漏 → 一次問 3–5 題 → loop 直到 checklist 齊全
    ↓
[Summary] → 產生結構化 JSON 參數，列給使用者確認
    ↓
[Generate] → 呼叫對應 backend → 產出 asset → 存版本
    ↓
[Refine]  → 使用者評論 → 顧問判斷「改 prompt 重生」 vs 「後處理編輯」 vs 「參數微調重跑」 → loop
    ↓
[Accept]  → 標記為 final，寫回 RAG 長期記憶
```

### 4.2 領域 Checklist（硬編碼的必問項）

| 模態 | 必問項 |
|---|---|
| 音樂 BGM | 用途場景、情緒 tag、BPM 範圍、調性、主奏樂器、長度、loop 需求、stem 分離需求、取樣率、格式 |
| 音效 SFX | 觸發事件、持續時間、音調、材質感（金屬/有機/電子）、是否需多變體 |
| 語音 | 角色性別年齡、個性、情緒、語速、口音、參考聲、語言、是否需對嘴時間戳 |
| 圖像 | 用途（立繪/場景/UI/icon/tile）、解析度、透明背景、是否需 tileable、風格參考、色票、是否 SFW |
| 影片 | 用途（過場/預告/loop 動畫）、時長、fps、解析度、是否有音軌、攝影機運動 |

### 4.3 Prompt 範本（改寫自你原本的）

每個 consultant 使用相同骨架，只差領域段落：

```
# Role
你是資深的{{領域專家}}，專門為遊戲/獨立創作者產出{{模態}}素材。

# Objective
透過提問幫客戶把模糊需求轉成精確的生成參數。

# Rules
1. 一次最多問 5 題，依重要性排序（用途 → 情緒/風格 → 技術細節）。
2. 每題都提供 3–5 個候選選項 + 「自由描述」逃生門。
3. 偵測使用者熟練度：第一輪先問「你對 {{領域術語}} 熟悉嗎？」若不熟就用比喻（「類似《{{知名作品}}》的風格」）。
4. 不要問已經在 {{專案記憶}} 中有答案的問題。
5. Checklist 集齊後，產出 YAML 摘要讓使用者確認，不要擅自開始生成。
6. 修正階段：先判斷「重生 / 微調參數 / 後處理」三種修法哪個最快，解釋你的選擇。

# Project Context (由 RAG 自動填入)
{{project_summary}}
{{style_guide}}
{{recent_assets}}

# Domain Checklist
{{checklist_for_this_modality}}

# Output Format
對話時用自然語言。要送去生成時，輸出:
```json
{ "backend": "...", "params": { ... }, "rationale": "..." }
```
```

---

## 5. 專案管理與 RAG 記憶

### 5.1 專案結構

每個專案是一個**資料夾**，完全自包含（方便備份、分享、git 管理）：

```
projects/
└── my_adventure_game/
    ├── project.json             # 基本設定、風格指南引用
    ├── style_guide.md           # 使用者可讀可編輯的風格指南
    ├── memory.sqlite            # 對話、版本、metadata
    ├── vectors/                 # ChromaDB persist dir
    ├── assets/
    │   ├── images/
    │   │   └── hero_portrait/
    │   │       ├── v1.png
    │   │       ├── v1.json      # prompt, seed, model, params
    │   │       ├── v2.png
    │   │       └── ...
    │   ├── audio/
    │   ├── voice/
    │   └── video/
    ├── models/                  # 專案專屬訓練產出（LoRA, voice clone）
    │   ├── hero_voice.pth
    │   └── game_style_lora.safetensors
    └── exports/                 # 打包給遊戲引擎用的輸出
```

### 5.2 RAG 記憶策略

**寫入時機 (Ingest)**
- 每個 session 結束，LLM 自動產出 300 字以內摘要 → embed → 寫入向量庫
- 每個 accepted asset 的 prompt + 使用者評語 → embed → 寫入
- 使用者偏好（「我不喜歡太鮮豔的顏色」）顯式指令 → 寫入「偏好」namespace

**讀取時機 (Retrieval)**
- 顧問每次新對話啟動時，用當前 user message 檢索 top-5 相關記憶
- 注入到 system prompt 的 `{{project_summary}}` 區塊
- 同時保留 hard-coded 的「當前 style guide」作為必讀脈絡

**分 namespace**
- `conversations` — 過去對話摘要
- `assets` — 已產出素材的描述
- `preferences` — 使用者個人偏好
- `style_guide` — 專案風格規則（優先級最高）

### 5.3 風格一致性 (Consistency)

- **Style Guide** 是人類可讀的 Markdown，使用者可手動編輯：

  ```markdown
  # Adventure Game Style Guide
  ## Visual
  - Palette: warm earth tones, #D4A574, #8B6F47, #2C3E50
  - Line: 2px clean outline, no shading
  - Reference: Studio Ghibli backgrounds, Octopath Traveler UI
  ## Audio
  - Key signature: prefer minor, A minor / D minor
  - Instrumentation: acoustic guitar, fantasy strings, light percussion
  - Avoid: 808 drums, heavy synth leads
  ## Character Voice
  - Hero: young male, warm, confident, reference = recording_01.wav
  ```

- 生成前，**整份 Style Guide 都會塞進 system prompt**（不靠 RAG，必定注入）
- 圖像生成用**固定 seed pool**（每個專案隨機生成 4 個 seed，後續風格統一取其一）
- 角色立繪可上傳 1–2 張錨定圖，作為 IP-Adapter / reference image 輸入

---

## 6. 生成後端對應表

| 模態 | Local 首選 | Local 備選 | Cloud API 選項 |
|---|---|---|---|
| 圖像 (靜態) | **ComfyUI + SDXL / Flux** | AUTOMATIC1111 | DALL·E 3, Imagen 3, Midjourney (非官方) |
| 圖像 (LoRA 訓練) | **kohya_ss** | OneTrainer | — |
| BGM / 音樂 | **MusicGen (AudioCraft)** | Stable Audio Open, YuE | Suno, Udio |
| 音效 SFX | **AudioGen**, Stable Audio Open | — | ElevenLabs SFX |
| TTS 配音 | **GPT-SoVITS**, F5-TTS | XTTS v2, Fish-Speech | ElevenLabs, OpenAI TTS |
| 語音 clone 訓練 | GPT-SoVITS (內建訓練) | RVC | ElevenLabs Voice Clone |
| 影片 (短) | **Wan 2.1**, HunyuanVideo, CogVideoX | SVD | Veo, Runway, Kling, Sora |
| 影片 (動畫 loop) | **AnimateDiff (ComfyUI)** | — | — |
| LLM (顧問) | **Ollama (Llama 3.3, Qwen 2.5, Mistral)** | llama.cpp, LM Studio API | Anthropic, OpenAI, Gemini, OpenRouter |

> 選擇原則：local 首選 = 目前（2026-04）無審查、品質可接受、社群活躍、可 finetune。

---

## 7. 訓練整合 (Training)

### 7.1 圖像 LoRA

**對話流程：**
> 顧問：「我看到你這個專案已經有 20 張主角立繪了。要不要我幫你訓練一個專屬 LoRA，之後生圖自動套用？」
> 使用者：「好啊」
> 顧問：「我會用這 20 張自動打 tag，訓練約 30 分鐘。需要你確認 3 件事：1. 觸發詞 2. 要不要包含背景 3. 訓練輪次（預設 10）」

**後台：**
- 呼叫 kohya_ss CLI
- 自動 tagging 用 WD14 tagger
- 產出 `.safetensors` 存到 `projects/xxx/models/`
- 自動在 `style_guide.md` 登記觸發詞

### 7.2 TTS Voice Clone

**對話流程：**
> 顧問：「你傳的這段錄音是要複製聲音嗎？GPT-SoVITS 只需 3–10 秒樣本即可 zero-shot，或丟 1 小時語料做完整 fine-tune（品質更好）。」

**後台：**
- Zero-shot 模式：直接走 inference，不訓練
- Fine-tune 模式：自動切片（slicer-gui）→ 降噪 → ASR 打字幕 → 訓練
- 產出模型存到 `projects/xxx/models/voices/`

### 7.3 訓練狀態回報
- 進度條 + 預估剩餘時間（從 worker WebSocket 串流）
- 可中斷、可續訓、可試聽中間 checkpoint

---

## 8. 版本控制 (Asset Versioning)

### 8.1 資料模型

```sql
CREATE TABLE assets (
  id TEXT PRIMARY KEY,              -- uuid
  project_id TEXT,
  modality TEXT,                    -- image/music/sfx/voice/video
  name TEXT,                        -- 使用者命名，例如 "hero_portrait"
  created_at TIMESTAMP
);

CREATE TABLE versions (
  id TEXT PRIMARY KEY,              -- uuid
  asset_id TEXT,
  parent_version_id TEXT,           -- null = root
  file_path TEXT,                   -- 相對於 project 根目錄
  prompt TEXT,                      -- 完整展開後送給模型的 prompt
  backend TEXT,                     -- comfyui / musicgen / ...
  params JSON,                      -- seed, cfg, steps, sampler, ...
  user_note TEXT,                   -- 使用者在這版的評語
  created_at TIMESTAMP,
  is_favorite BOOLEAN
);
```

### 8.2 UI 行為
- 每個 asset 顯示為**樹狀**時間軸（不是線性），可看到分支（「從 v3 分出 v4a 和 v4b」）
- 點任何節點可還原為「當前編輯中」
- 兩節點可 side-by-side diff（圖像對比、音訊 AB 試聽）
- `is_favorite` 可釘選，避免誤刪

---

## 9. Local 模型管理 (Model Manager)

### 9.1 UI
獨立面板，分頁：
- **LLM**（GGUF / Ollama 拉取）
- **Image Checkpoints**（SDXL / Flux base models）
- **LoRA / Embeddings**
- **Audio Models**（MusicGen variants, Stable Audio）
- **TTS Models**
- **Video Models**

每個條目：名稱、大小、VRAM 需求、來源 URL、啟用/停用、刪除。

### 9.2 後端行為
- 模型存在共用路徑（不是每專案一份）：`~/.misakaAssetGene/models/`
- 下載走 HuggingFace Hub（hf_hub_download，支援續傳）
- Ollama 模型走 `ollama pull` 代理
- 偵測 VRAM，模型卡片上標紅「你的 GPU 可能跑不動」

---

## 10. 基礎編輯工具 (Editor)

對話修正很累，也有「改一個小地方不值得重生」的場景。介面提供：

| 模態 | 工具 |
|---|---|
| 音訊 | BPM 調整、音量 normalize、淡入淡出、裁切、loop point 設定、格式轉換 |
| 圖像 | 亮度/對比/飽和度、裁切、resize、背景去除（rembg）、upscale (Real-ESRGAN) |
| 影片 | 裁切、速度調整、加音軌、格式轉換、抽禎轉 GIF |
| 語音 | 靜音裁切、音量 normalize、降噪 (demucs / RNNoise) |

實作：薄包裝 FFmpeg / PIL / rembg / demucs。所有編輯產生新版本，保留原版。

---

## 11. 目錄結構 (Repo Layout)

```
misakaAssetGene/
├── README.md
├── spec.md                         # 本檔
├── LICENSE
├── .env.example                    # API 金鑰範本（.env 進 .gitignore）
├── .gitignore
├── pyproject.toml                  # Python 依賴（uv / poetry）
├── package.json                    # 前端
├── src-tauri/                      # Tauri 殼
│   ├── Cargo.toml
│   └── src/main.rs
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   │   ├── Chat.tsx
│   │   │   ├── Assets.tsx
│   │   │   ├── Projects.tsx
│   │   │   └── Models.tsx
│   │   ├── store/                  # zustand / redux
│   │   └── api/                    # 呼叫 core service
│   └── vite.config.ts
├── core/                           # Python 核心服務
│   ├── main.py                     # FastAPI entry
│   ├── consultant/
│   │   ├── engine.py
│   │   ├── prompts/
│   │   │   ├── music.md
│   │   │   ├── image.md
│   │   │   ├── voice.md
│   │   │   └── video.md
│   │   └── checklists.py
│   ├── llm/                        # LLM 路由層
│   │   ├── router.py
│   │   ├── providers/
│   │   │   ├── ollama.py
│   │   │   ├── anthropic.py
│   │   │   ├── openai.py
│   │   │   └── gemini.py
│   ├── generation/
│   │   ├── orchestrator.py
│   │   ├── adapters/
│   │   │   ├── comfyui.py
│   │   │   ├── audiocraft.py
│   │   │   ├── gpt_sovits.py
│   │   │   └── video_backend.py
│   ├── training/
│   │   ├── lora.py
│   │   └── voice_clone.py
│   ├── editor/
│   │   ├── audio.py
│   │   ├── image.py
│   │   └── video.py
│   ├── project/
│   │   ├── manager.py
│   │   ├── versioning.py
│   │   └── style_guide.py
│   ├── memory/                     # RAG
│   │   ├── ingest.py
│   │   ├── retrieve.py
│   │   └── store.py                # ChromaDB wrapper
│   └── models/                     # SQLAlchemy models
├── workers/                        # 第三方 repo 啟動腳本
│   ├── comfyui/
│   │   ├── start.sh
│   │   └── requirements.txt
│   ├── audiocraft/
│   └── gpt_sovits/
├── scripts/
│   ├── install.py                  # 一鍵安裝（偵測 GPU、拉 repo、裝依賴）
│   └── doctor.py                   # 健康檢查
└── projects/                       # 使用者專案（gitignore）
```

---

## 12. 關鍵 User Flow 範例

### 12.1 新專案「冒險遊戲」第一次產 BGM
1. 使用者開啟 app → 「建立新專案」→ 輸入名稱、描述（「日式 RPG，中世紀奇幻」）
2. 顧問自動產生初版 `style_guide.md`，讓使用者確認或修改
3. 使用者切到 Chat 面板，選 modality = 音樂，說「我需要城鎮 BGM」
4. 顧問載入該專案記憶（第一次 = 空），走音樂 checklist，問 4–5 題
5. 使用者回答 → 顧問產出 YAML 摘要 → 確認
6. 後台呼叫 MusicGen，Chat 顯示進度
7. 完成，UI 出現播放器，左邊版本樹有 v1
8. 使用者：「節奏再慢 10 BPM」→ 顧問判斷「這用後處理最快」→ 呼叫 FFmpeg → 產生 v2
9. v2 滿意 → 標記 favorite → 自動寫進 RAG

### 12.2 第二次產角色立繪（跨模態一致性）
1. 使用者：「主角立繪」→ 顧問載入 RAG，看到 style_guide 寫了 palette，也看到使用者上次喜歡「溫暖色調」
2. 顧問直接用 style guide 的 palette + 固定 seed pool，少問 2 題
3. 產生 4 張變體供挑選
4. 使用者挑 v2 → 標記 → 顧問：「要不要我之後都用這個 seed 和類似構圖？」
5. 使用者同意 → 寫回 style_guide

### 12.3 訓練角色專屬聲音
1. 使用者已有 5 張立繪，現在要配音
2. 上傳 30 秒聲優試音 wav
3. 顧問：「這個聲音品質 OK，可以 zero-shot；如果你有 10 分鐘以上樣本我建議 fine-tune」
4. 使用者只有 30 秒 → 走 zero-shot
5. 顧問產生第一句測試 → 使用者：「太年輕了，降低 2 歲的感覺」
6. 顧問調整 GPT-SoVITS 的 speaker embedding 參數 → 重生
7. 滿意後，把聲音綁定到專案的「主角」角色 → 之後所有主角台詞自動用這個聲音

---

## 13. 技術選型細節

| 層 | 選擇 | 理由 |
|---|---|---|
| 桌面殼 | Tauri 2 | 輕量、Rust 安全、跨平台 |
| 前端 | React + TypeScript + Vite + Tailwind + shadcn/ui | 生態熟、AI 編碼支援好 |
| 前後端通訊 | Tauri invoke + WebSocket (for streaming) | 串流 LLM token 與進度 |
| 核心服務 | Python 3.11 + FastAPI + uvicorn | AI 生態 |
| 依賴管理 | uv (Python) + pnpm (JS) | 快 |
| DB | SQLite (metadata) + ChromaDB (vector) | 無伺服器，跟專案走 |
| LLM 路由 | 自建薄層（不用 LiteLLM，省依賴；但 API 介面仿它） | 可控 |
| 嵌入模型 | BGE-M3（多語，支援中英日） | 好用 |
| Worker 啟動 | subprocess + 健康檢查 port poll | 簡單可靠 |
| 包裝 | Tauri bundler + Python 用 PyOxidizer 或 PyInstaller，可選 portable mode | 使用者雙擊即用 |

---

## 14. 開發里程碑 (Roadmap)

### M0 — 骨架 (2 週)
- Tauri + React 殼 + Python FastAPI 能溝通
- 建立 / 切換專案
- Ollama + Anthropic API 兩個 LLM provider 通

### M1 — 顧問 MVP（音樂） (3 週)
- 音樂 checklist + consultant prompt
- MusicGen adapter
- 版本控制（線性，先不做樹狀）
- 基礎播放器

### M2 — 圖像 + RAG (3 週)
- ComfyUI adapter
- ChromaDB RAG ingest / retrieve
- Style guide 基本版

### M3 — 其他模態 (4 週)
- SFX、TTS、影片 adapters
- 版本樹狀 UI
- 基礎編輯工具

### M4 — 訓練整合 (3 週)
- kohya_ss LoRA 整合
- GPT-SoVITS voice clone 整合

### M5 — 打磨 (持續)
- Model Manager UI
- Prompt 模板庫
- 遊戲引擎匯出
- 安裝體驗（doctor、一鍵安裝）

---

## 15. 風險與開放問題

| 風險 | 緩解 |
|---|---|
| 安裝體驗地獄（CUDA / Python 版本 / 依賴衝突） | `scripts/install.py` 自動偵測並用 uv 隔離 venv；每個 worker 獨立環境；提供 Docker 模式備用 |
| 各 repo API 穩定性差（ComfyUI 節點改版） | 鎖定 commit hash，提供「升級 worker」按鈕並測試 |
| Local 影片模型品質仍差 | 在顧問對話中如實告知使用者；預設 route 到 cloud API（若有 key） |
| 顧問 LLM 小模型 (<7B) 遵循不了複雜 prompt | 預設推薦 ≥14B 的 Qwen 2.5 / Llama 3.3；小模型走 cloud |
| 訓練時 GPU 被占滿，其他生成排隊 | 單 GPU 排程器；訓練建議晚上跑 |
| 無審查模型商用授權不明確 | README 標註每個模型的授權，使用者自負責任 |
| 跨模態風格一致性仍依賴使用者自己維護 style guide | MVP 階段先接受此限制；未來研究「從已接受素材自動更新 style guide」 |

### 待決策
1. 影片 backend 首選 Wan 2.1 vs HunyuanVideo？（看 2026 Q2 社群與 VRAM 需求）
2. TTS 首選 GPT-SoVITS vs F5-TTS？（GPT-SoVITS 有訓練、F5-TTS 推論快）
3. Style guide 應該完全由使用者手寫，還是由 LLM 從對話中自動 propose？（傾向後者 + 人工 approve）
4. Prompt 模板庫要不要做社群分享？（超出「純個人用」範圍，暫緩）

---

## 16. 下一步

建議順序：
1. **確認這份 spec** — 有哪段你想改／刪／加
2. **跑一輪技術 PoC** — Tauri + FastAPI + Ollama + ComfyUI 四者能否在你機器上串起來
3. **M0 骨架開工** — 建立 repo、目錄、空的但可跑的 app

要不要我先把 `README.md`、`.env.example`、目錄骨架、`scripts/install.py` 的初版也寫出來？
