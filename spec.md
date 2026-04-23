# MisakaAssetGene — 專案規格書 (Spec)

> **Version:** v0.4  
> **Last Updated:** 2026-04-23  
> 對話式 AI 創作顧問：整合圖像、文字台詞、聲音、影片的**生成 + 訓練**工作台，以專案為單位維持記憶與風格一致性，為不懂技術的個人創作者（遊戲開發、內容創作）而設計。

---

## 1. 專案定位 (Positioning)

### 1.1 是什麼
- **個人用桌面軟體**，下載壓縮檔解壓即用，**不是 SaaS 平台**。
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
- **不懂 Python、不會裝 CUDA、不想讀 ComfyUI 節點圖**，但願意解壓縮後雙擊一個 setup 檔。
- 可能有一張消費級 GPU（8–24GB VRAM），或願意付費用 cloud API。

### 1.4 分發模式

| 模式 | 對象 | 取得方式 | 階段 |
|---|---|---|---|
| **Dev Mode** | 開發者、貢獻者 | `git clone` + `./scripts/setup.sh`（或 `.ps1`） | M0–M3 唯一模式 |
| **Portable Release** | 一般使用者 | 下載壓縮檔 → 解壓 → 雙擊 `setup.ps1/sh` | M4 起推出 |

**共用原則**：
- 使用者**不需安裝系統級 Python、CUDA Toolkit、uv、ffmpeg**
- 附帶的 `uv` 單一 binary 負責下載 embedded Python、建 venv、安裝依賴
- 重依賴（PyTorch + CUDA wheel、ComfyUI 等 worker）**延後到首次使用該模態時才下載**

---

## 2. 核心功能總覽 (Feature Matrix)

> v0.3: 新增 Cross-Project References、Project Portability、Cold Start Examples、VRAM Hot Swap、Offline Mode。

| 模組 | 功能 | 優先級 |
|---|---|---|
| **顧問引擎** | 分輪漸進提問、候選選項、摘要確認、對話式修正 | P0 |
| **Cold Start 範例** | 根據專案 type/synopsis/既有資產動態生成範例 prompt | P0 |
| **專案管理** | 建立/切換/匯出專案、專案層級設定 | P0 |
| **專案可攜性** | 所有路徑相對化、匯出 zip 含完整性 manifest | P0 |
| **跨專案引用** | `@project/path#version` 語法、`_external/` 複本、解析器 | P1 |
| **專案記憶 (RAG)** | 向量化歷史對話、素材描述、使用者偏好，生成前自動注入 | P0 |
| **多模態生成** | 圖像、BGM、SFX、角色配音、影片 | P0 |
| **複合素材包** | 圖文、台詞、角色語音、歌曲、影片、靜態動圖等可組合交付 | P1 |
| **版本控制** | 線性歷史 + favorite + note + tags（schema 預留 parent_version_id） | P0 |
| **Metadata 嵌入** | 產出檔案自描述（`version_id` + `prompt_hash` 寫入 PNG/WAV/MP4 元資料） | P0 |
| **VRAM Scheduler (Active/Cold)** | 模態切換時 VRAM 排程（exclusive/shared） | P0 |
| **VRAM Hot Swap (Warm)** | 加入 RAM 快取中間態，切換更快 | P1 |
| **整合管理 (Tools+Workers+Models)** | 統一選單腳本 + WebUI：版本檢查、更新、回滾、smoke test | P0 |
| **LLM 路由** | 統一介面切換 Ollama / llama.cpp / Anthropic / OpenAI / Gemini / OpenRouter | P0 |
| **Setup 友善錯誤處理** | 階段化進度、已知錯誤白名單 + 友善訊息、未知錯誤可 AI 解釋 | P0 |
| **離線模式** | 三態（Auto/Always Offline/Always Online）、自動偵測、UI 區分 | P1 |
| **訓練整合** | 圖像 LoRA、TTS voice clone、（未來）音樂風格微調 | P1 |
| **基礎編輯** | BPM、音量 normalize、亮度/對比、影片裁切、格式轉換 | P1 |
| **風格指南** | 專案級 style guide（色票、關鍵字、IP-Adapter 錨定圖、自訓 LoRA） | P1 |
| **自動 Style Guide Propose** | 雙重門檻（啟發式 + LLM 置信度）+ 自適應調整 | P1 |
| **批次變體** | 一次產 N 個候選讓使用者挑 | P1 |
| **License Report** | Export 時自動產授權報告（商用/署名/NSFW 狀態） | P1 |
| **版本樹狀 UI** | 從線性升級為 parent-child 樹 + diff | P1 |
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
│  │ Chat    │ │ Asset   │ │ Project  │ │ Integration  │    │
│  │ Panel   │ │ Browser │ │ Manager  │ │ Manager      │    │
│  └─────────┘ └─────────┘ └──────────┘ └──────────────┘    │
│  網路狀態 badge · 離線模式切換 · 跨專案引用警示             │
└──────────────────────────┬────────────────────────────────┘
                           │ IPC (Tauri invoke) / WebSocket
┌──────────────────────────▼────────────────────────────────┐
│  Core Service — Python (FastAPI, local 127.0.0.1)         │
│  ┌──────────────────┐  ┌─────────────────────────────┐    │
│  │ Consultant       │  │ Project / Memory (RAG)      │    │
│  │ Engine (router)  │──│ SQLite + ChromaDB           │    │
│  └────────┬─────────┘  └──────┬──────────────────────┘    │
│           │                    │                          │
│  ┌────────▼──────────┐  ┌──────▼────────────┐             │
│  │ VRAM Scheduler    │  │ Cross-Project     │             │
│  │ Active/Warm/Cold  │  │ Resolver          │             │
│  └────────┬──────────┘  └───────────────────┘             │
│           │                                               │
│  ┌────────▼─────────────────────────────────────────┐     │
│  │ Generation Orchestrator (adapter pattern)        │     │
│  │  Image │ Music │ SFX │ Voice │ Video │ Composite │     │
│  └────────┬─────────────────────────────────────────┘     │
│           │                                               │
│  ┌────────▼──────────┐ ┌────────────┐ ┌────────────┐      │
│  │ Training Engine   │ │ Editor     │ │ Network    │      │
│  │ (LoRA, TTS clone) │ │ Tools      │ │ State Mgr  │      │
│  └────────┬──────────┘ └────────────┘ └────────────┘      │
└───────────┼───────────────────────────────────────────────┘
            │ HTTP / subprocess
┌───────────▼───────────────────────────────────────────────┐
│  Backend Workers (各自獨立 process，可 on-demand 啟停)      │
│  ComfyUI │ AudioCraft │ GPT-SoVITS │ Ollama │ ...         │
└───────────────────────────────────────────────────────────┘
```

### 3.2 為什麼這樣分

- **Tauri 而非 Electron**：體積小 10×，冷啟動快，Rust 原生整合 OS keychain。
- **Python 核心服務**：AI 生態完整；獨立 process 以便除錯、熱重載、共用 GPU。
- **Worker 獨立進程**：每個開源 repo 有自己的 venv / 依賴衝突，隔離避免互撞。
- **Adapter Pattern**：Generation Orchestrator 定義統一 interface，每種 backend 寫一個 adapter。
- **VRAM Scheduler**：單卡使用者 LLM + 生成模型同時駐留會 OOM，必須有中央排程。

### 3.3 Tools & Workers 整合管理架構

#### 三類被管理物件

| 類別 | 性質 | 範例 | 更新機制 |
|---|---|---|---|
| **Tools (Binary)** | 單一可執行檔 | `uv`, `ffmpeg` | 下載官方 release zip → SHA256 驗證 → 原子替換 |
| **Workers (Git Repo)** | 第三方開源專案 | ComfyUI, AudioCraft, GPT-SoVITS, kohya_ss | `git clone` + `git checkout <commit>` |
| **Models (File/HF)** | 模型權重檔 | Qwen3.6-14B, Flux.1, MusicGen | HuggingFace Hub 下載 + 續傳 |

#### 本機實際儲存結構

```
repo/
├── tools/                           # ✗ gitignore（除 manifest 與 .gitignore）
│   ├── .gitignore                   ✓ tracked
│   ├── manifest.json                ✓ tracked
│   ├── uv.exe / uv                  ✗ 下載產物
│   └── ffmpeg.exe / ffmpeg          ✗ 下載產物
│
├── workers/                         # ✗ gitignore（除 manifest 與 .gitignore）
│   ├── .gitignore                   ✓ tracked
│   ├── manifest.json                ✓ tracked
│   ├── comfyui/                     ✗ 獨立 git clone
│   ├── audiocraft/                  ✗ 獨立 git clone
│   └── gpt-sovits/                  ✗ 獨立 git clone
│
└── ~/.misakaAssetGene/models/       # ← 跨專案共用的模型權重（不在 repo 內）
```

`tools/.gitignore`：
```
*
!.gitignore
!manifest.json
```

`workers/.gitignore`：
```
*/
!.gitignore
!manifest.json
```

> **本 main repo 絕不追蹤其他 repo**（不使用 git submodule / subtree）。workers 是使用者機器上獨立的 git clone，與本 repo 無拓撲關係。

### 3.4 VRAM Scheduler — 三態熱切換
> v0.3: 新增章節，明確 Active/Warm/Cold 狀態轉移。

**三態**：

| 狀態 | 位置 | 切回耗時 | 觸發條件 |
|---|---|---|---|
| 🟢 **Active** | VRAM | 0s | 正在使用 |
| 🟡 **Warm** | System RAM | 3–10s | 閒置超過 `idle_offload_sec`，權重仍保留 |
| ⚪ **Cold** | Disk | 30–60s | 超過 `cold_offload_sec`，或 RAM 壓力大 |

**預設策略**：
- LLM `idle_offload_sec = 300`（5 分鐘閒置 VRAM → RAM）
- 生成模型 `idle_offload_sec = 180`
- 模態切換時：優先載回 Warm 的，比 Cold 快 10×
- 系統 RAM < 16GB 時：自動跳過 Warm，直接 VRAM ↔ Disk

**技術實作**：
- Ollama 有 `keep_alive` 參數控制常駐
- ComfyUI 有 `/free` endpoint
- AudioCraft 需要手動 `del model + torch.cuda.empty_cache()`
- RAM offload 用 `model.to('cpu')`

**UI**：模態切換按鈕旁顯示 🟢🟡⚪ 狀態 icon 與預估切換時間。設定頁可調 timeout。

**Roadmap**：MVP 只做 Active/Cold（P0, M1），Warm 狀態 P1 (M3) 加入。

---

## 4. 顧問引擎 (Consultant Engine)

### 4.1 對話流程狀態機

```
[Intake]  → 使用者丟出需求 (Chat 底部提供 Cold Start 範例 chip)
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
| 圖像 | 用途（立繪/場景/UI/icon/tile）、解析度、透明背景、是否需 tileable、風格參考、色票 |
| 影片 | 用途（過場/預告/loop 動畫）、時長、fps、解析度、是否有音軌、攝影機運動 |

### 4.3 Prompt 範本骨架

```
# Role
你是資深的{{領域專家}}，專門為遊戲/獨立創作者產出{{模態}}素材。

# Objective
透過提問幫客戶把模糊需求轉成精確的生成參數。

# Rules
1. 一次最多問 5 題，依重要性排序（用途 → 情緒/風格 → 技術細節）。
2. 每題都提供 3–5 個候選選項 + 「自由描述」逃生門。
3. 偵測使用者熟練度：第一輪先問「你對 {{領域術語}} 熟悉嗎？」若不熟就用比喻。
4. 不要問已經在 {{專案記憶}} 中有答案的問題。
5. Checklist 集齊後，產出 YAML 摘要讓使用者確認，不要擅自開始生成。
6. 修正階段：先判斷「重生 / 微調參數 / 後處理」三種修法哪個最快，解釋你的選擇。
7. 任何「執行破壞性動作」（回滾 worker、刪模型、訓練 LoRA 等）只能以建議卡片形式輸出，不自行執行。

# Project Context (由 RAG 自動填入)
{{project_summary}}
{{style_guide}}
{{recent_assets}}

# Static Examples
{{domain_specific_few_shot_examples}}

# Project Examples (若該專案已累積 accepted assets)
{{dynamic_project_examples}}

# Domain Checklist
{{checklist_for_this_modality}}

# Output Format
對話時用自然語言。要送去生成時，輸出:
```json
{ "backend": "...", "params": { ... }, "rationale": "..." }
```
```

### 4.4 破壞性動作 UI 原則

**顧問永遠不自己執行**以下操作，一律產出「建議卡片」，由使用者在 UI 上**明確點擊按鈕**才執行：

- Worker 版本切換 / 回滾（`git checkout`）
- 刪除模型檔案
- 訓練新模型（長時間、高 GPU、高電費）
- 覆蓋 style_guide.md
- 清除 RAG 記憶

**為什麼不靠「對話回覆是／確定」**：
- 自然語言匹配「確定」等詞容易誤觸（使用者可能在回應別的問題）
- 按鈕點擊語意無歧義
- 稽核（「為什麼回滾了？」）有明確 UI event 記錄

**建議卡片 UI 範例**：
```
┌─ 顧問建議 ──────────────────────────┐
│ 🔄 回滾 ComfyUI 到 abc123           │
│ 原因: 最近 commit xyz789 引入 LoRA │
│      載入 bug (見 manifest 標註)   │
│ 影響: 約 30 秒 git checkout 時間    │
│                                     │
│ [ 取消 ]  [ 查看詳情 ]  [ 執行 ]    │
└────────────────────────────────────┘
```

### 4.5 Cold Start — 情境化範例
> v0.3: 新增章節。

每個模態的 Chat 面板底部顯示 3–5 個「試試這些」chip 按鈕，點擊填入輸入框（不自動送出，可編輯）。

**範例生成邏輯**：

| 情境 | 範例來源 |
|---|---|
| 新專案、有 `type` + `synopsis` | **LLM 動態生成**（吃 type + synopsis + current modality） |
| 新專案、只有 `type` | 按 type 分類的 hand-crafted fallback |
| 已有 ≥3 accepted assets | **LLM 動態生成**，吃 existing assets + modality，第一個 chip 一定是「類似〈既有 asset 名〉」 |
| 離線模式 + 無 local LLM | 純 hand-crafted fallback |

**範例 Prompt（音樂模態、冒險 RPG）**：
```
# Context
Project type: Japanese RPG
Synopsis: 異世界冒險，主角少年劍士，中世紀奇幻
Existing assets:
  - 5 張角色立繪 (tags: warm, painterly, hero)
  - 2 首 BGM (tags: town, calm, A-minor)
Current modality: 音樂

# Task
生成 4–5 個使用者「現在最可能下一步想做」的 BGM 需求短句，
每個 20 字內，JSON array of strings。
```

**輸出範例**：
```json
[
  "登入畫面的史詩感開場 BGM",
  "第一章村莊的寧靜夜晚",
  "主角與劍擦身相遇的主題",
  "第一場 boss 戰高強度戰鬥曲",
  "類似〈城鎮 BGM v3〉的風格"
]
```

**快取**：專案狀態「顯著變化」（新增 3+ asset、synopsis 更新、type 改變）才重生，平時讀 cache。使用者可點「🔄 換一組」強制重生。

**技術成本**：一次 LLM 呼叫（<1k token），local 模型 1–2 秒。

### 4.6 複合素材任務 (Composite Deliverables)
> v0.4: 新增章節。

系統不得把任務侷限成「只輸出一個檔案」。對於角色包、宣傳包、過場包等需求，顧問必須能把任務拆成**多個可協同生成的素材成員**。

**典型組合**：

| 套件類型 | 可能成員 |
|---|---|
| 角色包 | 立繪、角色設定文字、台詞、角色語音、待機動圖 |
| 宣傳包 | 主視覺、文案、旁白、BGM、短影片、GIF |
| 劇情包 | 分鏡圖、對白稿、配音、音效、過場影片 |

**顧問工作流**：
1. 先確認最終交付單位是單檔還是素材包
2. 若是素材包，先列出成員、依賴順序與共用風格錨點
3. 決定哪些成員可並行生成，哪些要等待前一步結果
4. 輸出 bundle manifest，記錄每個成員的 prompt、版本、依賴與檔案格式

**資料要求**：
- 每個 bundle 有自己的 `bundle_id`
- 成員之間可共享角色設定、style guide、參考音色與世界觀摘要
- 匯出時可選擇單檔匯出或整包匯出
- 後續 refinements 可只重跑其中一個成員，不必整包重做

---

## 5. 專案管理與 RAG 記憶

### 5.1 專案結構

每個專案是一個**資料夾**，完全自包含（方便備份、移動、分享）：

```
projects/                         # ← 整個目錄 gitignore
└── my_adventure_game/
    ├── project.json              # 基本設定 + type + synopsis
    ├── style_guide.md
    ├── memory.sqlite             # 對話、版本、metadata
    ├── vectors/                  # ChromaDB persist dir
    ├── assets/
    │   ├── images/
    │   │   └── hero_portrait/
    │   │       ├── v1.png        (已嵌入 metadata: version_id, prompt_hash)
    │   │       ├── v1.json
    │   │       └── v2.png
    │   ├── audio/
    │   ├── voice/
    │   └── video/
    ├── models/                   # 專案專屬訓練產出
    │   ├── hero_voice.pth
    │   └── game_style_lora.safetensors
    ├── _external/                # 跨專案引用複本（見 §5.6）
    │   ├── origins.json
    │   └── <source_project>/...
    ├── .cache/                   # 縮圖、衍生物（gitignore）
    └── exports/                  # 打包給遊戲引擎用的輸出
```

### 5.2 RAG 記憶策略

**寫入時機 (Ingest)**
- 每個 session 結束，LLM 自動產出 300 字以內摘要 → embed → 寫入向量庫
- 每個 accepted asset 的 prompt + 使用者評語 + 自動 tag → embed → 寫入
- 使用者偏好（「我不喜歡太鮮豔的顏色」）顯式指令 → 寫入「偏好」namespace

**讀取時機 (Retrieval)**
- 顧問每次新對話啟動時，用當前 user message 檢索 top-5 相關記憶
- 注入到 system prompt 的 `{{project_summary}}` 區塊
- 同時保留 hard-coded 的「當前 style guide」作為必讀脈絡

**分 namespace**
- `conversations` — 過去對話摘要
- `assets` — 已產出素材的描述 + tags
- `preferences` — 使用者個人偏好
- `style_guide` — 專案風格規則（優先級最高）

### 5.3 風格一致性 (Consistency)

**鎖風格的三層強度**（由弱到強）：

| 層級 | 手段 | 適用時機 |
|---|---|---|
| L1 弱 | 文字描述（style_guide 的 palette、關鍵字） | 專案剛建立，無參考素材 |
| L2 中 | **IP-Adapter 參考圖**（1–3 張風格錨定圖） | 使用者有明確風格參考圖 |
| L3 強 | **自訓 LoRA** | 累積 ≥15 張 accepted，顧問主動提議訓練 |

- Style Guide 是人類可讀的 Markdown，使用者可手動編輯
- 生成前，**整份 Style Guide 都會塞進 system prompt**（不靠 RAG，必定注入）
- Seed pool 降級：**只用來控制「同風格下的變體多樣性」**，不再當主要鎖定機制

Style Guide 範例：
```markdown
# Adventure Game Style Guide
## Visual
- Palette: warm earth tones, #D4A574, #8B6F47, #2C3E50
- Line: 2px clean outline, no shading
- Reference: Studio Ghibli backgrounds, Octopath Traveler UI
- Visual Anchors: ./references/sample_1.png, ./references/sample_2.png
- LoRA: ./models/game_style_lora.safetensors (trigger: "advgame_style")
## Audio
- Key signature: prefer minor, A minor / D minor
- Instrumentation: acoustic guitar, fantasy strings, light percussion
- Avoid: 808 drums, heavy synth leads
## Character Voice
- Hero: young male, warm, confident, reference = recording_01.wav
```

### 5.4 自動 Style Guide Propose

**雙重門檻**：
1. **啟發式（硬條件）**：≥3 accepted + ≥2 tag 重疊 + 距上次提議 ≥1 天
2. **LLM 自評置信度（軟條件）**：LLM 必須附 `confidence: 0–100`，預設 ≥75 才顯示卡片

**Prompt 要求 LLM 自評**：
```
請分析這 N 個 accepted assets 有沒有共通風格模式。
輸出 JSON:
{
  "has_pattern": true/false,
  "confidence": 0-100,
  "pattern_summary": "...",
  "proposed_diff": [...],
  "counterexamples": ["這個 asset 不完全符合"]
}
```

**自適應門檻**：
- 使用者 accept → 門檻 −2
- 使用者 reject → 門檻 +2
- 單次 accept/reject 後**交錯計數歸零**（連續才累積同方向）
- 範圍限制 60–90，初始 75
- 設定頁可手動覆蓋、reset

**Trigger 後的 UX**：
1. LLM 產出 diff 形式建議（新增/修改哪幾行）
2. Chat 出現「建議更新 style_guide.md」卡片
3. 使用者可：
   - **[Accept]** → 寫入 style_guide.md，顯示 `✓ 已更新（+3 行）`
   - **[Edit]** → 開 editor 讓使用者微調後再寫入
   - **[Reject]** → 記住此次拒絕
4. 任何 Accept 皆可一鍵 undo（style_guide.md 有自動歷史）

**預設開啟**，設定頁可關閉。第一次觸發時彈一次性教學 toast。

### 5.5 專案可攜性 (Portability)
> v0.3: 新增章節。

**相對路徑規則**（強制）：
1. `project.json`、`memory.sqlite`、`style_guide.md`、版本 JSON **所有路徑相對於專案根目錄**
2. 外部參照（共用模型庫）用**識別碼**（hash / name），不用絕對路徑；載入時由 Core Service 解析到本機位置
3. `.cache/`、縮圖、衍生物 → 可重建，`.gitignore`
4. 絕對路徑會被 Core Service 拒絕寫入（寫入前 normalize）

**匯出 / 匯入**：
- UI「匯出專案」→ 產生 `xxx.misaka.zip`，含：
  - 完整專案資料夾
  - `_external/` 所有複本
  - `manifest.json` 附所有檔案 SHA256 與版本資訊
  - `requirements.json` 列出用到的模型 / worker（對方缺什麼可提示下載）
- UI「匯入專案」→ 拖檔到主視窗：
  - 驗證 SHA256
  - 解壓到 `projects/`
  - 比對 `requirements.json` 與本機模型庫，缺什麼跳下載提示
  - 解析 `_external/` 引用（見 §5.6）

**匯出對話**：
```
匯出 my_adventure_game
  本體大小: 120 MB
  跨專案引用: 3 個 (見 §5.6)
  [ ] 包含大檔模型 (+ 850 MB)     ← 預設不勾
  [x] 完整性 manifest              ← 強制
  [x] requirements.json            ← 預設

  [ 取消 ]  [ 確認匯出 ]
```

### 5.6 跨專案引用 (Cross-Project References)
> v0.3: 新增章節。

**語法**：
```
@<project_name>/<asset_path>[#<version>]
```
- `@adventure_rpg/char/kyuoka` — 跟隨預設（見下方 fallback）
- `@adventure_rpg/char/kyuoka#v3` — 釘選特定版本
- `@adventure_rpg/char/kyuoka#favorite` — 跟隨 favorite
- `@adventure_rpg/char/kyuoka#latest` — 顯式跟隨最新

**預設 fallback（不指定版本時）**：
```
favorite  →  latest accepted  →  latest version
```

**可出現的欄位**（只在這些地方解析 `@`，避免 prompt 誤觸發）：
- `style_guide.md` 的 visual_anchors / voice references
- 版本 JSON 的 `dependencies` 欄位
- Chat 輸入框（GitHub 式 typeahead，`@` 打出來後彈專案 picker，resolved 後變 UI chip）
- 其餘自由文字欄位不解析（literal string）

**不允許跨專案引用的東西**：
- `style_guide.md` 整份（維持專案內部資產）
- RAG 記憶
- 專案設定

#### 5.6.1 `_external/` 結構

```
my_project/_external/
├── origins.json              # 索引：本地路徑 ↔ 原始出處 ↔ hash
└── adventure_rpg/
    └── char/
        └── kyuoka/
            ├── v3.png
            └── v3.meta.json
```

**`origins.json`**：
```json
{
  "schema_version": 1,
  "entries": [
    {
      "local_path": "_external/adventure_rpg/char/kyuoka/v3.png",
      "origin": {
        "project": "adventure_rpg",
        "asset_path": "char/kyuoka",
        "version": "v3",
        "version_id": "uuid-...",
        "sha256": "abc123..."
      },
      "copied_at": "2026-04-22T10:30:00Z"
    }
  ]
}
```

檔案本身 metadata（§8.3）也寫入 `misaka_origin_project` / `misaka_origin_path` — **雙軌保險**。

#### 5.6.2 解析順序

```
Resolve @adventure_rpg/char/kyuoka#v3
│
├─ Step 1: 本機 projects/ 是否有 "adventure_rpg"？
│    ├─ 有 → 該 asset v3 存在？
│    │    ├─ 是，hash 符合 → ✓ 使用 live
│    │    └─ 是，hash 不符 → ⚠ live 與複本不一致，問使用者
│    └─ 沒有 → 下一步
│
└─ Step 2: 本專案 _external/ 是否有對應複本？
     ├─ 有，hash 符合 origins.json → ✓ 使用複本
     ├─ 有，hash 不符 → ✗ 複本損毀，警告
     └─ 沒有 → ✗ 完全 broken
```

**Hash 不符處理**（live 版本已修改）：
```
⚠ @adventure_rpg/char/kyuoka#v3 在當前環境與匯入時不一致

[ 使用本機 live ] (更新 origins.json hash)
[ 使用複本 ]    (從 _external/ 讀，忽略 live)
[ 查看 diff ]   (並排顯示差異再決定)
```

#### 5.6.3 引用狀態與警告

**四種狀態**：
- ✓ OK — 引用有效
- ⚠ Outdated — pinned version 已被來源淘汰（非 favorite / 非 latest）
- ⚠ Hash Mismatch — live 與複本不一致
- ✗ Broken — 來源專案或 asset 不存在

**UI**：
- 專案側欄 badge：broken → 紅；outdated / mismatch → 黃
- 開啟受影響 asset 時顯示橫幅
- **生成前若引用 broken，阻擋生成**並跳建議卡片

#### 5.6.4 匯出時的邏輯

**每次匯出都重新解析**所有外部引用，把 resolved 結果複製進新 zip 的 `_external/`：

**例子**：
```
Env A 匯出 proj_alpha
  引用: @x/foo, @y/bar#v1, @z/baz
  Env A 解析: x@live=x1, y@_external=y1（已是 pinned v1）, z@live=z1
  zip 的 _external/: x1, y1, z1

移到 Env B（有 x 已更新到 x2，沒 y、z）
Env B 匯入 proj_alpha
  解析:
    @x/foo → live exists (x2), hash 與 origins 內 x1 不符
             → UI 問使用者：[用 live x2] / [用 _external 內 x1]
    @y/bar#v1 → live 沒有 → 走 _external/y1 ✓
    @z/baz → live 沒有 → 走 _external/z1 ✓

Env B 再匯出 proj_alpha
  重新解析:
    @x/foo → 使用者選了 live → 複製 x2 到新 zip
    @y/bar#v1 → 複製 y1 (釘選版本保留)
    @z/baz → 複製 z1 (from _external)
  新 zip 的 _external/: x2, y1, z1
```

**釘選版本的例外**：`@x/foo#v1` 即使 live 是 x2，仍用 `_external/` 內的 x1。

**匯出對話**：
```
匯出 proj_alpha
  跨專案引用：3
    @x/foo          → live (x2)          [切換用本機複本]
    @y/bar#v1       → _external (y1)     (釘選版本)
    @z/baz          → _external (z1)     (live 不存在)

  匯出大小: 本體 120 MB + 外部 340 MB = 460 MB

  [ 調整解析 ]  [ 取消 ]  [ 確認匯出 ]
```

#### 5.6.5 循環依賴

`A` 引用 `@B/x`，`B` 引用 `@A/y` → **允許**（不是程式依賴，只是資源連結），但 UI 顯示循環警告讓使用者知情。

### 5.7 專案建立：Type & Synopsis
> v0.3: 新增章節。

**建立新專案時收集**：

| 欄位 | 必填 | 用途 |
|---|---|---|
| 名稱 | ✓ | 專案資料夾名稱、跨專案引用前綴 |
| type | ✓ | RPG / FPS / puzzle / VN / anime / platformer / other | 冷啟動範例分類、顧問預設風格 |
| synopsis | 選填 | 一段 1–5 行故事/設定描述 | 冷啟動範例動態生成、RAG 初始 context |

**UI 建立流程**：
```
Step 1: 名稱
> my_adventure_game

Step 2: 類型 (用於初始化預設)
[○ RPG]  [○ FPS]  [○ Puzzle]  [● VN]  [○ Anime]  [○ Platformer]  [○ Other]

Step 3: 故事設定 (選填但強烈建議 — 顧問會根據此提供更貼切的建議)
┌─────────────────────────────────────┐
│ 異世界冒險 RPG，主角為少年劍士，     │
│ 中世紀奇幻背景，故事圍繞失落的神劍。 │
│                                     │
│ [skip]                              │
└─────────────────────────────────────┘

[ 建立 ]
```

顧問首次啟動時會讀 synopsis，若空會建議「填幾行有助於生成更貼切的素材，要現在補嗎？」

---

## 6. 生成後端對應表（當前推薦 · 2026-04）

| 模態 | Local 首選 | Local 備選 | Cloud API 選項 |
|---|---|---|---|
| 圖像 (靜態) | ComfyUI + SDXL / Flux | AUTOMATIC1111 | DALL·E 3, Imagen 3 |
| 圖像 (LoRA 訓練) | kohya_ss | OneTrainer | — |
| BGM / 音樂 | MusicGen (AudioCraft) | Stable Audio Open, YuE | Suno, Udio |
| 音效 SFX | AudioGen, Stable Audio Open | — | ElevenLabs SFX |
| TTS 配音 | GPT-SoVITS, F5-TTS | XTTS v2, Fish-Speech | ElevenLabs, OpenAI TTS |
| 語音 clone 訓練 | GPT-SoVITS (內建訓練) | RVC | ElevenLabs Voice Clone |
| 影片 (短) | Wan 2.1（首選）, HunyuanVideo, CogVideoX | SVD | Veo, Runway, Kling |
| 影片 (動畫 loop) | AnimateDiff (ComfyUI) | — | — |
| LLM (顧問) | Qwen 3.6 / Gemma 4 / Llama 系 (視 registry) | — | Anthropic, OpenAI, Gemini, OpenRouter |

> **此表快速過時**。實際清單維護於 `core/models/registry.json`，並可由遠端更新（見 §9.3）。spec 只列「編寫當下的判斷」，不作為實作權威。

---

## 7. 訓練整合 (Training)

### 7.1 圖像 LoRA

**對話流程：**
> 顧問：「我看到你這個專案已經有 20 張主角立繪了。要不要我幫你訓練一個專屬 LoRA，之後生圖自動套用？」

→ 使用者點 **[開始訓練]** 按鈕，進入參數確認頁（觸發詞、是否包含背景、訓練輪次預設 10）→ 點 **[確認]** 後才實際跑。

**後台：**
- 呼叫 kohya_ss CLI
- 自動 tagging 用 WD14 tagger
- 產出 `.safetensors` 存到 `projects/xxx/models/`
- 自動在 `style_guide.md` 登記觸發詞

### 7.2 TTS Voice Clone

**對話流程：**
> 顧問：「你傳的這段錄音是要複製聲音嗎？GPT-SoVITS 只需 3–10 秒樣本即可 zero-shot，或丟 1 小時語料做完整 fine-tune（品質更好）。」

**後台：**
- Zero-shot：直接走 inference，不訓練
- Fine-tune：自動切片 → 降噪 → ASR 打字幕 → 訓練
- 產出模型存到 `projects/xxx/models/voices/`

### 7.3 訓練狀態回報
- 進度條 + 預估剩餘時間（從 worker WebSocket 串流）
- 可中斷、可續訓、可試聽中間 checkpoint
- 訓練觸發 VRAM Scheduler 進 exclusive 模式，其他生成排隊

---

## 8. 版本控制 (Asset Versioning)

### 8.1 資料模型

```sql
CREATE TABLE assets (
  id TEXT PRIMARY KEY,              -- uuid
  project_id TEXT,
  modality TEXT,                    -- image/music/sfx/voice/video
  name TEXT,
  created_at TIMESTAMP
);

CREATE TABLE versions (
  id TEXT PRIMARY KEY,              -- uuid
  asset_id TEXT,
  parent_version_id TEXT,           -- null = root (P1 樹狀時才會真正用到)
  file_path TEXT,                   -- 相對路徑
  prompt TEXT,
  prompt_hash TEXT,                 -- sha256，寫入檔案 metadata
  backend TEXT,                     -- comfyui / musicgen / ...
  backend_version TEXT,             -- worker 的 git commit（可追溯不穩定版本）
  params JSON,
  tags JSON,                        -- ["warm", "combat", "D-minor", ...]
  dependencies JSON,                -- ["@adventure_rpg/char/kyuoka#v3"]
  user_note TEXT,
  created_at TIMESTAMP,
  is_favorite BOOLEAN
);
```

### 8.2 UI 行為

**P0（MVP）**：
- 線性時間軸（新→舊或舊→新可切）
- 每列顯示縮圖/波形、prompt 摘要、tags、favorite 星號、note 欄
- Tag filter + search（`#warm`、`#combat`、或自由文字）
- 點任一版本：預覽 + 「以此為基礎繼續修改」按鈕
- `is_favorite` 可釘選，提示使用者「釘選 = 保證不會被清理」

**P1（升級樹狀）**：
- git-log 風格節點圖
- 分支顯示
- 兩節點 side-by-side diff

### 8.3 檔案自描述 (Self-documenting Assets)

產出檔案**本身帶身分證**，即使被移出專案資料夾也能溯源。

**寫入位置**：

| 格式 | 位置 | 標準 |
|---|---|---|
| PNG | `tEXt` / `iTXt` chunk | ComfyUI 業界慣例 |
| JPEG | EXIF UserComment | — |
| WAV | `LIST-INFO` chunk | RIFF 標準 |
| MP3 | ID3v2 TXXX frame | — |
| MP4 / WebM | `-metadata` (ffmpeg) | — |
| OGG | Vorbis comment | — |

**寫什麼**（只寫識別碼，不寫明文 prompt）：
```
misaka_version_id      = {uuid}
misaka_project_id      = {uuid}
misaka_prompt_hash     = {sha256}
misaka_created_at      = {iso8601}
misaka_backend         = comfyui@abc123
misaka_origin_project  = {optional, 若為跨專案複本}
misaka_origin_path     = {optional}
```

**用途**：
1. 檔案被移出專案資料夾仍可識別
2. 拖回 app → 讀 metadata → 查本地 DB → 還原完整版本與參數
3. `_external/` 複本即使 `origins.json` 遺失也能從檔案本身回推來源

---

## 9. 整合管理 (Integration Manager)

### 9.1 統一選單腳本

單一入口，不同 OS 對應不同副檔名但 UI 一致：

| 檔案 | 對象 | 啟動方式 |
|---|---|---|
| `scripts/manager.ps1` | Windows | 雙擊 or `.\manager.ps1` |
| `scripts/manager.sh` | macOS / Linux | `./manager.sh` |
| `scripts/lib/manager.py` | 共用核心邏輯 | WebUI 也透過 API import 它 |

**主選單**：
```
╔════════════════════════════════════════════════════╗
║  misakaAssetGene — Integration Manager             ║
╠════════════════════════════════════════════════════╣
║                                                    ║
║ ── Binary Tools ──                                 ║
║ [1] uv          0.5.12  →  0.5.18   ⚠ 有更新      ║
║ [2] ffmpeg      7.1.0   →  7.1.0    ✓ 最新        ║
║                                                    ║
║ ── AI Workers (Git) ──                             ║
║ [3] ComfyUI     v0.3.15 @ abc123    ✓ 官方推薦    ║
║ [4] AudioCraft  v1.3.0  @ def456    ⚠ 上游有新版   ║
║ [5] GPT-SoVITS  v2.1    @ ghi789    ✓              ║
║ [6] kohya_ss    (未安裝)                            ║
║                                                    ║
║ ── Models ──                                       ║
║ [7] LLM 模型     3 個已安裝                         ║
║ [8] 圖像模型     5 個已安裝                         ║
║ [9] 音訊模型     2 個已安裝                         ║
║                                                    ║
║ [a] 全部檢查更新   [r] 修復損毀   [0] 離開         ║
╚════════════════════════════════════════════════════╝
>
```

### 9.2 Manifest schemas

**`tools/manifest.json`**：
```json
{
  "schema_version": 1,
  "tools": {
    "uv": {
      "version": "0.5.18",
      "platforms": {
        "windows-x64": { "url": "...", "sha256": "...", "extract": "uv.exe" },
        "macos-arm64": { "url": "...", "sha256": "..." },
        "linux-x64":   { "url": "...", "sha256": "..." }
      },
      "changelog_url": "https://github.com/astral-sh/uv/releases/tag/0.5.18",
      "self_update_cmd": "uv self update"
    },
    "ffmpeg": { ... }
  }
}
```

**`workers/manifest.json`**：
```json
{
  "schema_version": 1,
  "workers": {
    "comfyui": {
      "repo": "https://github.com/comfyanonymous/ComfyUI.git",
      "recommended": {
        "commit": "abc123...",
        "tag": "v0.3.15",
        "verified_at": "2026-04-10",
        "note": "穩定，支援 Flux 全系列"
      },
      "known_good": [
        { "commit": "abc123", "tag": "v0.3.15", "date": "2026-03-20", "note": "穩定" },
        { "commit": "bad000", "tag": "v0.3.12", "date": "2026-02-15", "note": "⚠ LoRA 載入 bug，避開" }
      ],
      "python": "3.11",
      "install_cmd": "uv pip install -r requirements.txt",
      "start_cmd":   "python main.py --port 8188 --listen 127.0.0.1",
      "health_check": "http://127.0.0.1:8188/system_stats",
      "smoke_test": "scripts/smoke/comfyui.py",
      "vram_requirement_mb": 4000
    }
  }
}
```

### 9.3 Model Registry

**`core/models/registry.json`**（git 追蹤，隨 app release 更新，也支援遠端拉取）：
```json
{
  "schema_version": 1,
  "categories": {
    "consultant_llm": [
      {
        "name": "Qwen3.6-14B-Instruct",
        "version": "3.6.0",
        "supersedes": ["Qwen3-14B-Instruct", "Qwen2.5-14B-Instruct"],
        "released": "2026-03-15",
        "changelog": "長 context 256k；指令遵循改善",
        "size_gb": 8.5,
        "vram_min_gb": 12,
        "license": "Apache-2.0",
        "commercial": true,
        "scores": { "chinese": 5, "instruction": 5, "speed_tps": 25 },
        "strengths": ["長 context", "中日文強"],
        "weaknesses": ["VRAM 需求高"],
        "download_url": "hf://Qwen/Qwen3.6-14B-Instruct-GGUF"
      }
    ],
    "image_checkpoint": [...],
    "music": [...],
    "tts": [...],
    "video": [...]
  }
}
```

### 9.4 WebUI 整合

- 跟 CLI 選單顯示相同資訊（讀同一份 manifest / registry）
- 每行一個「更新」或「管理」按鈕
- 有更新可用時側邊欄顯示紅點 badge（不阻斷主流程）
- 模型比較面板：選 2+ 模型 → 側邊欄顯示對比表（檔案大小、VRAM、授權、中文能力、速度等）

### 9.5 更新與回滾策略

| 項目 | 遠端檢查來源 | 觸發時機 | 自動動作 |
|---|---|---|---|
| Tools | `tools/manifest.json` from repo main | app 啟動時一次 | 僅提示 |
| Workers 官方推薦 | `workers/manifest.json` from repo main | app 啟動時一次 | 僅提示 |
| Workers 上游 | 各 worker 的 `git fetch origin` | 選單中 [c] 或設定頁手動 | 不自動 fetch |
| Models | `core/models/registry.json` from repo main | app 啟動時一次 | 僅提示 |

**離線模式下全部停用**，見 §11.5。

**安全機制**：
- 所有變更前自動記錄當前狀態（`tools/.backup/`、`workers/<name>/.misaka_prev_commit`）
- 更新失敗自動 rollback
- 顧問可在對話中**建議**回滾，但實際執行必須使用者點 UI 按鈕（見 §4.4）

### 9.6 Smoke Test

每個 worker 附一個最小生成腳本（由維護者寫、版本控制、隨 main repo 升級）：

```
scripts/smoke/
├── comfyui.py       # 跑一張 512×512 SDXL，驗證是否產出有效 PNG
├── audiocraft.py    # 跑 3 秒 musicgen-small，驗證 WAV
├── gpt_sovits.py    # 跑 zero-shot 合成「測試」二字
└── ...
```

**觸發點**：升/降級 worker 後自動跑；使用者在 Integration Manager 點 `[t]`；生成失敗時顧問可建議跑。

---

## 10. 基礎編輯工具 (Editor)

| 模態 | 工具 |
|---|---|
| 音訊 | BPM 調整、音量 normalize、淡入淡出、裁切、loop point 設定、格式轉換 |
| 圖像 | 亮度/對比/飽和度、裁切、resize、背景去除（rembg）、upscale (Real-ESRGAN) |
| 影片 | 裁切、速度調整、加音軌、格式轉換、抽禎轉 GIF |
| 語音 | 靜音裁切、音量 normalize、降噪 (demucs / RNNoise) |

實作：薄包裝 FFmpeg / PIL / rembg / demucs。所有編輯產生新版本，保留原版。

---

## 11. Setup 體驗與離線模式

### 11.1 入口

| 平台 | 入口腳本 | 使用者動作 |
|---|---|---|
| Windows | `scripts/setup.ps1` | 雙擊（自動提權）或 PowerShell 跑 |
| macOS / Linux | `scripts/setup.sh` | Terminal 跑 `./setup.sh` |

**Setup 內部流程**：
1. 呼叫 `manager.ps1/sh` 的「全部檢查並安裝」模式（tools + 必要 workers）
2. 跑 `doctor.py`（GPU / VRAM / 磁碟 / 網路檢查）
3. 初始化 `core/` Python venv
4. 啟動 Core Service + Tauri UI

### 11.2 階段化進度 UI

```
[1/7] 偵測作業系統與硬體...           ✓ Windows 11, RTX 3060 12GB
[2/7] 下載 uv (18 MB)...             ✓
[3/7] 下載 Python runtime...         ✓ CPython 3.11.8 (embed)
[4/7] 建立虛擬環境...                 ✓
[5/7] 安裝核心依賴...                 ⚠ 網路不穩，重試中 (2/3)
[6/7] 下載 ffmpeg (50 MB)...         待處理
[7/7] 初始化資料夾結構...             待處理
```

### 11.3 友善錯誤處理

**白名單已知錯誤**：寫好中文說明 + 修復指令
- 網路逾時 → 「網路不穩，按 Enter 重試或設定 proxy」
- 磁碟不足 → 「需 15GB，目前 C 槽剩 8GB」
- CUDA 驅動缺失 → 「未偵測到 NVIDIA 驅動。連結：...」
- PowerShell 執行策略 → 「請以管理員執行：`Set-ExecutionPolicy RemoteSigned`」
- SHA256 不符 → 「下載檔損毀，自動重試；連續失敗請檢查網路」

**未知錯誤**：完整 traceback **只寫入 `setup.log`**，console 顯示：
```
⚠ 步驟 [5/7] 發生未知錯誤
   摘要: ImportError: libcuda.so.1 not found
   完整記錄已寫入 setup.log

   要讓 AI 解釋可能原因嗎？
   [y] 是（需設定 API key 或 Ollama）
   [n] 否
   [s] 跳過此步驟繼續
```

AI 解釋路徑：讀 `.env` 有沒有 API key；沒有就提示「先取得一組免費 key」+ 常見 provider 連結；丟 `setup.log` 最後 50 行 + 系統資訊給 LLM。

### 11.4 原則
- console 不印 Python traceback（只寫 log）
- 每一步都給進度百分比與預估時間
- 任何需要使用者輸入的地方都提供預設選項（Enter 接受）
- 失敗可 resume，不必整個重跑

### 11.5 離線模式
> v0.3: 新增章節。

#### 核心定位

> 離線模式主要服務 **(1) 資安要求高的使用者**（政府、研究、企業內網）、**(2) 網路突然故障的救急**。非一般使用者常態模式。

**設計原則**：離線優先，線上只做「必要下載」與「cloud API」，其餘所有功能都要能離線運作。

#### 三種狀態

| 狀態 | 行為 | UI Badge |
|---|---|---|
| 🟢 **Auto** (預設) | 跟隨網路狀態，切換時提示使用者 | 綠點（線上）/ 灰點（離線暫時） |
| 🔒 **Always Offline** | 鎖死離線，忽略網路；啟動時自動載入 | 鎖圖示，灰底 |
| 🌐 **Always Online** | 視為永遠有網路，不跳離線提示 | 連網圖示，藍底 |

**設定持久化**：寫進 `~/.misakaAssetGene/settings.json`，啟動時讀取，下次開啟延續。

#### 功能離線矩陣

| 功能 | 離線可用？ | 備註 |
|---|---|---|
| 所有 local LLM 對話 | ✓ | Ollama / llama.cpp |
| 所有 local 生成（圖/聲/影） | ✓ | worker + model 已下載 |
| 版本控制 / tags / favorite | ✓ | 純 local DB |
| RAG 記憶 | ✓ | ChromaDB 本地 |
| 跨專案引用解析 | ✓ | 純 local |
| Style Guide 自動 propose | ✓ | 走 local LLM |
| Metadata 嵌入 | ✓ | 純 local |
| 編輯工具 | ✓ | FFmpeg / PIL / rembg |
| 版本樹、匯出、冷啟動範例 | ✓ | 純 local（冷啟動可退回 hand-crafted） |
| 訓練（LoRA / voice clone） | ✓ | 純 local |
| Cloud API 對話 | ✗ | 自動 disable |
| 下載 tools / workers / models | ✗ | 需網路 |
| 檢查 manifest 更新 | ✗ | 需網路 |
| AI 解釋 setup 錯誤 | ⚠ | 有 local LLM 就 ✓，否則 ✗ |

#### Auto 模式的網路偵測

- OS 層訊號（`navigator.onLine` / 系統 API）+ 每 30 秒 ping neutral endpoint（可關閉）
- 連續 ≥10 秒失聯才視為斷線（避免閃斷誤判）

#### 斷線時的提示流程（Auto 模式）

```
⚠ 網路中斷（持續 10 秒）

切換到離線模式？
  ▸ 目前使用的 Cloud LLM (Claude Opus 4.7) 將不可用
  ▸ 要改用本機 Qwen3-7B
     - 首次載入約 25 秒
     - 需 7 GB VRAM（你有 12 GB ✓）

  [ 切換到離線 ]  [ 等待網路恢復 ]

（5 分鐘後自動採取預設動作：等待）
```

**重連時的提示**：
```
✓ 網路恢復

要切回線上模式嗎？
  ▸ 可重新使用 Cloud LLM（對話品質更高）
  ▸ 本機 Qwen3-7B 會從 VRAM 卸載

  [ 切回線上 ]  [ 保持離線 ]
```

#### 模型切換的特殊處理

- 當前 session 正在用 cloud 模型（對話中斷）→ 斷線提示預設「等待」（避免對話中途打斷思路）
- Idle（>2 分鐘沒對話）→ 預設顯示「切換」
- 使用者可在設定「斷線時預設行為」：切換 / 等待 / 每次問

#### 資源不足警告

切換到離線模式時偵測本機資源：
```
⚠ 切換到離線模式

偵測到你的裝置：
  • VRAM: 8 GB
  • RAM: 16 GB
  • Disk: 120 GB free

可離線運作的功能：
  ✓ 顧問（Qwen3-3B，品質中）
  ✓ 圖像生成（SDXL）
  ✓ 基礎編輯
  ✗ 影片生成（Wan 2.1 需 16 GB VRAM）
  ✗ 語音訓練（需 12 GB VRAM）

確認切換？
```

#### UI 視覺區別

- **標題列**永駐 badge，三態不同顏色
- **Chat 模型選單**：離線時 cloud 選項灰掉 + 鎖圖示 tooltip
- **Integration Manager**：離線時所有下載/檢查按鈕灰掉
- **設定頁「網路」分頁**：三選一切換 + 目前網路狀態 + 最近切換記錄

#### Always Offline 的啟動行為

- 略過所有網路檢查（manifest 更新、doctor 的 ping 測試）
- 立即載入使用者預設的 local LLM
- 任何對 cloud 的嘗試都直接拒絕（不跳提示）
- 啟動速度比 Auto 快

---

## 12. 目錄結構 (Repo Layout)

```
misakaAssetGene/
├── CLAUDE.md                        # Claude 專案總則
├── CONTRIBUTING.md                  # 開源貢獻指南
├── README.md
├── spec.md                          # 本檔
├── LICENSE
├── .env.example                     # API 金鑰範本（.env 進 .gitignore）
├── .gitignore
├── .plan/
│   ├── DEVELOPMENT_PLAN.md
│   └── RESEARCH_LOG.md
├── .claude/
│   ├── commands/
│   │   ├── spec-discuss.md
│   │   ├── update-spec.md
│   │   └── review-plan.md
│   ├── rules/
│   │   ├── spec-workflow.md
│   │   ├── multimodal-assets.md
│   │   ├── repo-hygiene.md
│   │   └── community-workflow.md
│   └── agents/
│       ├── architect.md
│       ├── backend.md
│       ├── ai-ml.md
│       ├── frontend.md
│       ├── ui-ux.md
│       ├── devops.md
│       ├── qa-sdet.md
│       └── security.md
├── pyproject.toml
├── uv.lock
├── package.json
├── pnpm-lock.yaml
│
├── tools/                           # ✗ gitignore
│   ├── .gitignore
│   ├── manifest.json
│   ├── uv.exe / uv                  (執行後產生)
│   └── ffmpeg.exe / ffmpeg          (執行後產生)
│
├── workers/                         # ✗ gitignore
│   ├── .gitignore
│   ├── manifest.json
│   ├── comfyui/                     (獨立 git clone)
│   ├── audiocraft/                  (獨立 git clone)
│   └── gpt-sovits/                  (獨立 git clone)
│
├── scripts/
│   ├── setup.ps1                    # Windows 主入口
│   ├── setup.sh                     # macOS / Linux 主入口
│   ├── manager.ps1                  # Tools/Workers/Models 統一選單
│   ├── manager.sh
│   ├── doctor.py                    # 系統健康檢查
│   ├── lib/
│   │   ├── manager.py               # 選單共用邏輯
│   │   ├── download.py              # 下載 + SHA256 + 原子替換
│   │   └── git_ops.py               # worker git 操作包裝
│   └── smoke/
│       ├── comfyui.py
│       ├── audiocraft.py
│       └── gpt_sovits.py
│
├── src-tauri/
│   ├── Cargo.toml
│   └── src/main.rs
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   │   ├── Chat.vue
│   │   │   ├── Assets.vue
│   │   │   ├── Projects.vue
│   │   │   └── IntegrationManager.vue
│   │   ├── stores/                  # Pinia
│   │   └── api/
│   └── vite.config.ts
│
├── core/                            # Python 核心服務
│   ├── main.py                      # FastAPI entry
│   ├── consultant/
│   │   ├── engine.py
│   │   ├── prompts/
│   │   │   ├── music.md
│   │   │   ├── image.md
│   │   │   ├── voice.md
│   │   │   └── video.md
│   │   ├── checklists.py
│   │   ├── few_shot.py
│   │   └── cold_start.py            # 冷啟動範例生成
│   ├── llm/
│   │   ├── router.py
│   │   └── providers/
│   │       ├── ollama.py
│   │       ├── anthropic.py
│   │       ├── openai.py
│   │       └── gemini.py
│   ├── scheduler/
│   │   └── vram.py                  # VRAM Scheduler (Active/Warm/Cold)
│   ├── network/
│   │   └── state.py                 # Network State Manager (離線模式)
│   ├── generation/
│   │   ├── orchestrator.py
│   │   ├── composition.py           # 複合素材任務編排
│   │   └── adapters/
│   │       ├── comfyui.py
│   │       ├── audiocraft.py
│   │       ├── gpt_sovits.py
│   │       └── video_backend.py
│   ├── training/
│   │   ├── lora.py
│   │   └── voice_clone.py
│   ├── editor/
│   │   ├── audio.py
│   │   ├── image.py
│   │   ├── video.py
│   │   └── metadata.py              # self-documenting 寫入
│   ├── project/
│   │   ├── manager.py
│   │   ├── versioning.py
│   │   ├── style_guide.py
│   │   ├── portability.py           # 匯出/匯入、相對路徑 normalize
│   │   └── cross_project.py         # @proj/path#ver 解析器
│   ├── memory/                      # RAG
│   │   ├── ingest.py
│   │   ├── retrieve.py
│   │   └── store.py
│   ├── models/
│   │   ├── registry.json
│   │   └── schemas.py               # SQLAlchemy models
│   └── integration/
│       ├── tools.py
│       ├── workers.py
│       └── model_registry.py
│
└── projects/                        # ✗ gitignore (使用者專案)
```

---

## 13. 關鍵 User Flow 範例

### 13.1 新專案「冒險遊戲」第一次產 BGM
1. 使用者解壓縮 → 雙擊 `setup.ps1` → 跑完 7 步 → 啟動 app
2. 「建立新專案」→ 名稱 + `type: RPG` + synopsis「異世界冒險 RPG，主角少年劍士」
3. 顧問讀 synopsis，生成初版 `style_guide.md` 草稿讓使用者確認
4. Chat 面板，選 modality = 音樂
5. Chat 底部出現 chip：`[登入 BGM]` `[村莊夜晚]` `[戰鬥曲]` `[主角主題]`
6. 點 `[村莊夜晚]` 填入輸入框 → 送出
7. 顧問走音樂 checklist，問 4–5 題
8. 使用者回答 → 顧問產出 YAML 摘要 → 確認
9. 後台呼叫 MusicGen，Chat 顯示進度
10. 完成，UI 出現播放器，版本列表有 v1（tags: `bgm, town, calm, A-minor`）
11. v2 滿意 → 點 favorite 星號 → 寫回 RAG

### 13.2 跨專案借用主角立繪

已有 `adventure_rpg` 專案，裡面有 `char/kyuoka#v3`。現在開新專案 `side_story`：
1. 使用者在 Chat：「主角造型請用 @」→ typeahead 彈出 picker → 選 `adventure_rpg/char/kyuoka`
2. Chat 輸入框變成：「主角造型請用 [@adventure_rpg/char/kyuoka]（chip）」
3. 顧問提示「已連結 kyuoka v3（favorite），生圖會用它當 IP-Adapter 錨定」
4. 生成完成，版本 JSON 的 `dependencies` 自動寫入 `@adventure_rpg/char/kyuoka#v3`
5. 若之後 `adventure_rpg` 把 kyuoka 改版，`side_story` 自動跟隨（因為沒釘版本）

### 13.3 ComfyUI 出錯 → 顧問建議回滾
1. 使用者跑生圖 → 失敗（LoRA 載入 error）
2. Core Service 抓 log + worker `git log` → 送顧問
3. Chat 出現建議卡片：
   ```
   🔄 偵測到 ComfyUI 可能有 bug
      目前版本: xyz789 (上游最新，未驗證)
      manifest 標註此 commit 為 ⚠ LoRA 載入 bug
      建議回滾: abc123 (官方推薦 2026-04-10 驗證)

      [ 忽略 ]  [ 查看 log ]  [ 執行回滾 ]
   ```
4. 使用者點 **[執行回滾]** → `git checkout abc123` → 跑 smoke test → 通過 → 重試原生成

### 13.4 網路斷線自動切換離線模式（Auto 模式）
1. 使用者正在用 Cloud Claude Opus 4.7 對話
2. 網路突然斷線，10 秒後 toast 跳出
3. 詢問切換：預設選「等待」（因為 idle < 2 分鐘）
4. 但使用者急著繼續 → 點 **[切換到離線]**
5. Qwen3-7B 載入 25 秒（UI 顯示進度）
6. 顧問從上一句話繼續（context 保留）
7. 2 分鐘後網路恢復 → toast 詢問切回；使用者點 **[保持離線]**（目前對話順利，不想再切）

### 13.5 訓練角色專屬聲音
1. 已有 5 張立繪，現在要配音
2. 上傳 30 秒聲優試音 wav
3. 顧問：「這段品質 OK，可 zero-shot」→ 產生第一句測試
4. 使用者：「太年輕了」→ 顧問微調 speaker embedding → v2
5. 滿意 → 綁定到「主角」角色 → 之後所有主角台詞自動用這個聲音

### 13.6 角色宣傳包（複合素材）
1. 使用者說：「幫我做主角宣傳包，要有立繪、兩句角色台詞、角色語音、15 秒角色介紹短片和一個可貼社群的 GIF」
2. 顧問先判定這是 **Composite Deliverable**
3. 顧問列出成員：立繪、文案、配音、短片、GIF，並說明依賴順序
4. 使用者確認後，系統先產立繪與文案，再用文案產語音與短片，最後由短片抽 GIF
5. Asset Browser 顯示同一個 bundle，下方可展開各成員版本與依賴
6. 使用者只想改語氣 → 只重跑台詞與配音，不重做立繪與 GIF

---

## 14. 技術選型細節

| 層 | 選擇 | 理由 |
|---|---|---|
| 桌面殼 | Tauri 2 | 輕量、Rust 安全、跨平台 |
| 前端 | Vue 3 + TypeScript + Vite + Tailwind + shadcn-vue | 使用者選擇 |
| 狀態管理 | Pinia | Vue 官方推薦 |
| 前後端通訊 | Tauri invoke + WebSocket（串流 token 與進度） | — |
| 核心服務 | Python 3.11 + FastAPI + uvicorn | AI 生態 |
| 依賴管理 | **uv (bundled)** + pnpm | 使用者零安裝 |
| DB | SQLite (metadata) + ChromaDB (vector) | 無伺服器 |
| LLM 路由 | 自建薄層（介面仿 LiteLLM 但不依賴它） | 可控、少依賴 |
| 嵌入模型 | BGE-M3（多語） | 中英日強 |
| Worker 啟動 | subprocess + 健康檢查 port poll | 簡單可靠 |
| Metadata 嵌入 | PIL (PNG)、mutagen (audio)、ffmpeg (video) | — |
| 網路偵測 | Tauri 網路事件 + Python aiohttp 輔助 probe | — |
| 打包 | Tauri bundler + uv 自帶 embedded Python | 使用者解壓即用 |

---

## 15. 開發里程碑 (Roadmap)
> v0.3: 加入跨專案引用 (M2)、專案可攜性 (M2)、冷啟動範例 (M1)、離線模式 (M3)、VRAM Warm (M3)。

### M0 — 骨架 (2 週)
- Tauri + Vue3 殼 + Python FastAPI 能溝通
- 建立 / 切換專案 + `project.json` schema（含 type、synopsis）
- Ollama + Anthropic API 兩個 LLM provider 通
- `tools/manifest.json` + `manager.ps1/sh` 骨架（可下載 uv、ffmpeg）

### M1 — 顧問 MVP（音樂） (3 週)
- 音樂 checklist + consultant prompt
- **Cold start 範例（靜態 + 動態）**
- MusicGen adapter + AudioCraft worker（用 manifest 管理）
- 線性版本控制 + tags + favorite + note
- 檔案 metadata 嵌入（WAV ID3）
- VRAM Scheduler Active/Cold 模式
- 基礎播放器

### M2 — 圖像 + RAG + 可攜性 (4 週)
- ComfyUI adapter + worker
- ChromaDB RAG ingest / retrieve
- Style guide 基本版（含 IP-Adapter）
- ComfyUI smoke test + 顧問建議回滾流程
- 圖像 metadata 嵌入（PNG tEXt）
- **專案可攜性：相對路徑 normalize、匯出/匯入 zip、requirements.json**
- **跨專案引用：`@proj/path#ver` 語法、resolver、`_external/` 結構、UI chip**

### M3 — 其他模態 + 離線 + 熱切換 (4 週)
- SFX、TTS、影片 adapters
- 所有 worker 的 smoke test
- 基礎編輯工具
- **複合素材工作流（圖文/台詞/配音/影片/GIF）**
- **離線模式（三態、自動偵測、UI 區分）**
- **VRAM Warm 狀態**
- Cross-project 匯出邏輯（重新解析 + 複本更新）

### M4 — 訓練整合 + 打包 (4 週)
- kohya_ss LoRA 整合
- GPT-SoVITS voice clone 整合
- **自動 Style Guide propose（雙重門檻 + 自適應）**
- Portable Release：Tauri bundle + embedded Python + 附帶 uv/ffmpeg
- 友善 Setup 錯誤處理完整版
- License Report

### M5 — 打磨 (持續)
- 版本樹狀 UI（升級）
- Prompt 模板庫
- 遊戲引擎匯出
- Model 比較面板進階
- Air-gapped bundle（若有需求）

---

## 16. 風險與開放問題

| 風險 | 緩解 |
|---|---|
| 安裝體驗地獄（CUDA / Python 版本 / 依賴衝突） | uv bundled + embedded Python + 延後重依賴下載；doctor 預檢；錯誤白名單 + AI 解釋 |
| Worker 上游 regression | manifest 鎖 commit + known_good 清單 + smoke test + 顧問建議回滾 |
| 模型快速過時（spec 寫死很快不準） | Model Registry 動態維護，spec 只標當前推薦 |
| Local 影片模型品質仍差 | 在對話中告知使用者；預設 route 到 cloud API（若有 key） |
| 顧問 LLM 小模型遵循不了複雜 prompt | 預設推薦較大模型；小 VRAM 機器顧問走 cloud，生成走 local |
| VRAM 單卡爭奪 | VRAM Scheduler Active/Warm/Cold；顧問 LLM 可 route cloud |
| 訓練時 GPU 被占滿 | Scheduler 切 exclusive；訓練建議晚上跑 |
| 無審查模型商用授權不明確 | Model Registry 標授權；License Report 自動掃描 |
| 跨專案引用循環 / dangling | UI 警告、匯出時強制重新解析、hash 雙軌保險 |
| 網路狀態誤判（閃斷 / 中間人劫持） | 連續 10 秒失聯才切換；使用者可強制 Always Online/Offline |
| 離線模式下使用者看不到 cloud 服務更新 | 設定頁有「最近狀態切換記錄」，切回線上時提示「離線期間 X 個模型有更新」 |
| 專案 zip 太大（含 `_external/` 複本） | 匯出對話顯示預估大小；提供「不包含大檔模型」選項 |

### 待決策
1. Air-gapped bundle（跨機器傳遞完整離線包）放 M5 或更後？
2. Model Registry 遠端來源要不要獨立 repo？（分離 app 更新節奏）
3. 是否支援多 GPU（未來研究）？
4. 跨專案引用是否提供「廢棄遷移」工具（把所有引用實體化成本地複本）？

---

## 17. 協作與社群流程

### 17.1 新需求處理流程

1. 使用者提出新需求
2. 先以 `spec.md` 與 `architect` 角色討論可行性、架構影響與實作細節
3. 確認方向後，才更新 `spec.md`
4. 若有研究結論或規劃決策，同步寫入 `.plan/RESEARCH_LOG.md`

### 17.2 外部 repo 追蹤政策

- **本需求已寫入 spec**：見 §3.3「本 main repo 絕不追蹤其他 repo」
- 第三方 repo 必須維持為使用者機器上的獨立 clone
- 禁止使用 git submodule / subtree 把第三方 repo 納入主 repo 歷史
- `workers/`、`tools/` 只追蹤控制檔，其餘內容由 `.gitignore` 排除

### 17.3 Claude 專案結構

- `CLAUDE.md`：專案總則
- `.claude/commands/`：固定工作入口
- `.claude/rules/`：模組化規範
- `.claude/agents/`：對應 Dream Team 的角色 persona

### 17.4 開源協作規範

- repo 必須附 `CONTRIBUTING.md` 與 `LICENSE`
- 任何重大實作變更都要可被外部貢獻者追溯到 `spec.md`
- PR 應附問題描述、方案、影響範圍與驗證方式

---

## 18. 下一步

1. **Review spec v0.4** — 最後檢視
2. **PoC** — Tauri + Vue3 + FastAPI + Ollama + ComfyUI 五者串起來
3. **M0 骨架** — repo 初始化、`tools/manifest.json`、`setup.ps1/sh` 骨架、專案建立流程（type/synopsis）

---

## 附錄 A: Manifest & Registry Schemas

完整 JSON schema 定義見：
- `tools/manifest.schema.json`
- `workers/manifest.schema.json`
- `core/models/registry.schema.json`
- `projects/_external/origins.schema.json`

（實作階段補上）

---

## Changelog

### v0.4 (2026-04-23)
- **新增**
  - `CLAUDE.md` 與 `.claude/` 協作結構（commands / rules / agents）
  - §4.6 複合素材任務（圖文、台詞、角色語音、歌曲、影片、靜態動圖等組合交付）
  - §13.6 角色宣傳包複合素材 user flow
  - §17 協作與社群流程（spec-first、架構師討論、外部 repo 邊界、開源協作）
- **修改**
  - 版本提升到 v0.4，更新專案描述加入文字台詞
  - §2 Feature Matrix 加入「複合素材包」
  - §3.1 架構圖加入 Composite 任務編排
  - §12 目錄結構加入 `CLAUDE.md`、`.claude/`、`.plan/`、`CONTRIBUTING.md`
  - §15 M3 里程碑加入複合素材工作流

### v0.3 (2026-04-22)
- **新增**
  - §3.4 VRAM Scheduler 三態詳細（Active/Warm/Cold），Warm 為 P1 / M3
  - §4.5 Cold Start 情境化範例（靜態 + 動態）
  - §5.5 專案可攜性（相對路徑規則、匯出/匯入、完整性 manifest）
  - §5.6 跨專案引用（`@proj/path#ver` 語法、`_external/` 結構、resolver、hash 保險、匯出重新解析）
  - §5.7 專案建立 type 必填、synopsis 選填
  - §11.5 離線模式（三態 Auto/Always Offline/Always Online、自動偵測、UI 區分、資源警告）
  - §16 新增 5 條風險條目（循環引用、網路誤判、離線期間漏更新、zip 大小、實體化遷移）
- **修改**
  - §2 Feature Matrix 加入新功能與優先級重排
  - §4.4 自動 Style Guide 置信度自適應 ±2（原未明確）
  - §5.1 專案結構加入 `_external/`、`.cache/`
  - §8.1 `versions` 表加 `dependencies` 欄位
  - §8.3 metadata 加 `misaka_origin_*` 欄位（跨專案複本追溯）
  - §12 目錄結構加 `network/`、`cross_project.py`、`portability.py`、`cold_start.py`
  - §15 Roadmap：M1 加冷啟動、M2 加可攜性與跨專案引用、M3 加離線與 Warm、M4 加自動 propose

### v0.2 (2026-04-22)
- 新增：§1.4 分發模式、§3.3 Tools & Workers 管理、§4.4 破壞性動作 UI 原則、§5.4 自動 Style Guide propose、§8.3 檔案自描述、§9 Integration Manager、§11 Setup 體驗
- 修改：§2 優先級重排、§5.3 風格一致性三層策略、§6 指向 Model Registry、§8 版本控制 P0 降級線性、§14 React → Vue3

### v0.1 (2026-04-21)
- 初版
