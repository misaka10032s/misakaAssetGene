---
id: BP-ADAPTER-1
title: 六大核心多模態生成 Adapter
system: adapter
tags: [adapter, generation, multimodal, comfyui, gpt-sovits, ace-step]
status: 已完成
request_verbatim: "多模態生成｜圖像、BGM、SFX、角色配音、影片（spec.md §2 核心功能總覽 Feature Matrix，P0）"
decided_date: 2026-05-07
exec_links:
  - core/generation/adapters/comfyui.py
  - core/generation/adapters/gpt_sovits.py
  - core/generation/adapters/voxcpm.py
  - core/generation/adapters/ultimate_rvc.py
  - core/generation/adapters/ace_step.py
  - core/generation/adapters/audiocraft.py
  - core/generation/adapters/stable_audio_tools.py
  - core/generation/adapters/video_backend.py
  - core/generation/adapters/common.py
done_date: 2026-05-07
origin: "`core/generation/adapters/` 首次入庫於 commit ca99f10（2026-05-07，『M1~4 XD』，@PM 登記為 M1）"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §2 核心功能總覽 / §6 生成後端對應表
---

## 設計說明

`core/generation/adapters/` 底下每個檔案對應 CLAUDE.md「Multimodal by default」原則所要求的一種輸出模態，統一透過共用介面（`common.py`）與後端 CLI/HTTP 溝通：

| 模態 | 後端 | Adapter 檔案 |
|---|---|---|
| 圖像（含 inpaint/img2img） | ComfyUI | `comfyui.py` |
| 角色配音（TTS/Voice Clone） | GPT-SoVITS | `gpt_sovits.py` |
| 角色配音（另一路徑） | VoxCPM | `voxcpm.py` |
| 變聲/RVC | ultimate-rvc | `ultimate_rvc.py` |
| 歌曲/BGM | ACE-Step | `ace_step.py` |
| 音效/BGM（另一路徑） | AudioCraft | `audiocraft.py` |
| 音效/BGM（另一路徑） | stable-audio-tools | `stable_audio_tools.py` |
| 影片 | video backend | `video_backend.py` |

對應 spec §6「生成後端對應表」列出的 M1~M2 必做 repo 清單；每個 adapter 是實際呼叫外部 worker（`workers/<name>/`）的薄封裝層，由 `core/generation/service.py` 統一編排排程與 job 生命週期。

### 現況核對（2026-07-23 盤點）

9 個 adapter 檔案皆存在且非空樁（`comfyui.py` 支援 txt2img/img2img/inpaint，見 `BP-COMFY-1`）。M2（928dd77）追加 inpaint/img2img e2e 與多階段精修；M3（7af6066/2e1aa19）補齊 offline gating（見 `BP-OFFLINE-1`）與 VRAM 熱切換（見 `BP-VRAM-1`）。@PM 登記 M2 有 70 tests green + Chrome UI e2e 驗證（真實 ComfyUI 生成 + img2img refine lineage）。「影片」模態（`video_backend.py`）與「角色台詞文字」不經 adapter（走 LLM router 直接生文字），故 spec §2 表格中的「圖文、台詞、角色語音、歌曲、影片」在此以「6 大 adapter 覆蓋圖像/語音/歌曲/影片四種二進位輸出」理解；台詞文字由顧問引擎（`BP-CONSULT-1`）直接以 LLM 產生。

### `ace_step.py` job.params 消費（2026-09-05）

`BP-COMFY-3` 已讓一般（非 refine）job 攜帶 `params`（`_build_job` 種預設值 + `PATCH .../jobs/{id}` `JobExecutionPatch.params`），但當時「範圍外」明列僅涵蓋 `comfyui.py`，`ace_step.execute()` 仍把 `/release_task` payload 的 `lyrics` 寫死為 `""`、`prompt`/`global_caption` 只讀 `context.job.prompt`/`summary`，job 完全無法要求帶歌詞的人聲、覆寫 tags/caption 或指定時長。本次比照 `comfyui.py` 的作法（helper + explicit key whitelist，不做 `**params` 任意透傳）新增 `ace_step._build_payload(job)`，消費 `job.params`（欄位名對照 `workers/ace-step-1.5/acestep/api/http/release_task_models.py` 的 `GenerateMusicRequest`，非猜測）：

| job.params 鍵 | 對應 worker 欄位 | 說明 |
|---|---|---|
| `lyrics` | `lyrics` | 預設 `""`（純伴奏），非空即人聲歌詞 |
| `tags` 或 `prompt` | `prompt`（ACE-Step 的 tags/caption 欄位） | 不存在時沿用既有行為，回退到 `job.prompt` |
| `duration` 或 `audio_duration` | `audio_duration` | 未提供時完全不帶這個 key（與修改前行為一致） |
| `seed` | `seed`（連動 `use_random_seed=False`） | 未提供時維持原本 `seed=-1`／`use_random_seed=True` |
| `inference_steps` / `guidance_scale` / `task_type` / `audio_format` | 同名欄位 | 未提供時維持原硬編碼預設值 |
| `model` / `vocal_language` / `bpm` / `key_scale` / `time_signature` | 同名欄位 | 僅在 `params` 存在該鍵時才加入 payload |

`params` 為空（或缺少上述鍵）時，`_build_payload` 產出的 payload 與修改前逐位元組相同（`tests/test_ace_step_adapter.py::test_empty_params_reproduces_prior_hardcoded_payload` 保證）。前端仍無 UI 可編輯這些 job params（同 `BP-COMFY-3` 的範圍外備註），只能透過 API 直接 PATCH。

### `ace_step.py` thinking / LM 規劃參數開放（2026-09-05）

研究（`@PM/state/runs/misakaAssetGene-gen-test-260904/song-sop.md` §1 第 5 條）指出 `thinking`（ACE-Step 5Hz LM 規劃模式，本機已裝 `acestep-5Hz-lm-1.7B` checkpoint）被 `_build_payload` 寫死 `False`，且不在任何白名單內，job 無法打開它來提升人聲連貫度/清晰度。本次在既有白名單機制上，新增 12 個 LM/規劃相關欄位（同樣對照 `GenerateMusicRequest` 的確切欄位名，非猜測；`constrained_decoding*`／`allow_lm_batch`／`track_name`／`track_classes`／`is_format_caption` 屬多軌合成/除錯內部機制，非規劃品質旋鈕，本次不開放）：

| job.params 鍵 | 對應 worker 欄位 | 型別 |
|---|---|---|
| `thinking` | `thinking` | bool |
| `use_cot_caption` | `use_cot_caption` | bool |
| `use_cot_language` | `use_cot_language` | bool |
| `lm_model_path` | `lm_model_path` | str |
| `lm_backend` | `lm_backend` | str |
| `lm_negative_prompt` | `lm_negative_prompt` | str |
| `sample_query` | `sample_query` | str |
| `lm_temperature` | `lm_temperature` | float |
| `lm_cfg_scale` | `lm_cfg_scale` | float |
| `lm_top_p` | `lm_top_p` | float |
| `lm_repetition_penalty` | `lm_repetition_penalty` | float |
| `lm_top_k` | `lm_top_k` | int |

bool 欄位維持嚴格型別（不接受 `"true"`/`"false"` 字串）：非 bool 值會拋 `ValueError`，呼應本檔案既有數值欄位（`int()`/`float()`）遇到型別錯誤即失敗、不靜默吞掉的一貫風格。除 `thinking`（既有硬編碼預設 `False` 的 key，`params` 提供時才覆蓋）外，其餘 11 個欄位在 `params` 未提供時完全不加入 payload（byte-for-byte 與修改前相同，含既有 `BASELINE_PAYLOAD` 測試維持不動）——這一點對 worker 有影響：這些欄位在 worker 端本就有自己的 Pydantic 預設值（如 `use_cot_caption`/`use_cot_language` 預設 `True`），未指定時由 worker 沿用其預設，adapter 不代為決定。`sample_query` 僅在 `sample_mode=True`（目前仍硬編碼 `False`，本次未變動）時才對 worker 生效，暫為待用欄位。測試：`tests/test_ace_step_adapter.py`（`test_thinking_*`、`test_lm_*` 共 8 條新測試）。
