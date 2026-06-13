from __future__ import annotations

import re

from core.consultant.checklists import CHECKLISTS
from core.models.schemas import (
    ClarifyRequest,
    ClarifyResult,
    ConsultantAnalysis,
    ConsultantDeliverable,
    ConsultantPlanStep,
    Modality,
    TrainingSuggestionCard,
)

_QUOTED_TERM_RE = re.compile(r"[\"'「『](.+?)[\"'」』]")
_MATRIX_SPLIT_RE = re.compile(r"\s*[x×＊]\s*")
_SPACE_RE = re.compile(r"\s+")


def build_clarify_result(request: ClarifyRequest, template_loaded: bool) -> ClarifyResult:
    analysis = _analyze_request(request)
    primary_modality = request.modality or (analysis.inferred_modalities[0] if analysis.inferred_modalities else Modality.TEXT)
    return ClarifyResult(
        modality=primary_modality,
        summary=_build_summary(primary_modality, analysis),
        questions=_build_questions(primary_modality, analysis),
        template_loaded=template_loaded,
        next_step=_build_next_step(analysis),
        analysis=analysis,
    )


def _analyze_request(request: ClarifyRequest) -> ConsultantAnalysis:
    prompt = _normalize_prompt(request.prompt)
    project_context = _normalize_prompt(" ".join(filter(None, [request.project_name, request.project_type, request.project_synopsis])))
    inferred_modalities = _infer_modalities(prompt, request.modality)
    primary_modality = request.modality or (inferred_modalities[0] if inferred_modalities else Modality.TEXT)
    franchise = _extract_franchise(prompt, project_context)
    characters = _extract_characters(prompt)
    outfits = _extract_outfits(prompt)
    scenes = _extract_scenes(prompt)
    actions = _extract_actions(prompt)
    matrix_axes = _extract_matrix_axes(prompt)
    style_keywords = _extract_style_keywords(prompt)
    required_research = _build_required_research(prompt, franchise, characters, outfits)
    deliverables = _build_deliverables(inferred_modalities, prompt, outfits, matrix_axes)
    recommended_workers = _recommend_workers(inferred_modalities, prompt)
    search_queries = _build_search_queries(prompt, franchise, characters)
    blocking_reasons = list(required_research)
    execution_steps = _build_execution_steps(primary_modality, prompt, deliverables, required_research, recommended_workers)
    guidance_path = _build_guidance_path(primary_modality, required_research, deliverables)

    # Training-flow extensions (spec §7.1.1 / M4.b).
    is_training = primary_modality is Modality.TRAINING
    suggestion_cards: list[TrainingSuggestionCard] = []
    if is_training:
        suggestion_cards = _build_training_suggestion_cards(prompt, characters)
        training_steps = _build_training_execution_steps(prompt, characters)
        execution_steps = training_steps
        if not recommended_workers:
            recommended_workers = ["kohya_ss", "gpt-sovits"]
        guidance_path = _build_training_guidance_path()

    return ConsultantAnalysis(
        objective=prompt,
        inferred_modalities=inferred_modalities,
        franchise=franchise,
        characters=characters,
        outfits=outfits,
        scenes=scenes,
        actions=actions,
        style_keywords=style_keywords,
        matrix_axes=matrix_axes,
        required_research=required_research,
        search_queries=search_queries,
        recommended_workers=recommended_workers,
        deliverables=deliverables,
        execution_steps=execution_steps,
        blocking_reasons=blocking_reasons,
        guidance_path=guidance_path,
        is_training_flow=is_training,
        # Entity IDs are unknown at analysis time (session slots populate them);
        # leave None so the engine can fill them from accepted slots.
        training_character_sheet_id=None,
        training_dataset_pack_id=None,
        training_recipe_id=None,
        training_lora_preset_id=None,
        training_i2v_recipe_id=None,
        suggestion_cards=suggestion_cards,
    )


def _normalize_prompt(prompt: str) -> str:
    return _SPACE_RE.sub(" ", prompt.replace("\r", " ").replace("\n", " ")).strip()


def _extract_franchise(prompt: str, project_context: str = "") -> str | None:
    patterns = [
        r"關於(.+?)中",
        r"來自(.+?)的",
        r"from (.+?)[,， ]",
    ]
    for source in [prompt, project_context]:
        if not source:
            continue
        for pattern in patterns:
            match = re.search(pattern, source, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return None


def _extract_characters(prompt: str) -> list[str]:
    quoted_terms = [term.strip() for term in _QUOTED_TERM_RE.findall(prompt) if term.strip()]
    if quoted_terms:
        return quoted_terms
    patterns = [
        r"(?:先做|做|產生|生成|畫出)([一-龥A-Za-z0-9_\-·]+?)(?:所有官方服裝|官方服裝)",
        r"關於[^\"]*?([一-龥A-Za-z0-9_\-·]{1,12})(?:這個)?角色",
        r"角色(?:是|為)?([一-龥A-Za-z0-9_\-·]+?)(?:的|，|,|。|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt)
        if match:
            value = match.group(1).strip()
            if value:
                return [value]
    match = re.search(r"角色(?:是|為)?(.+?)(?:的|，|,|。|$)", prompt)
    if match:
        value = match.group(1).strip()
        return [value] if value else []
    return []


def _extract_outfits(prompt: str) -> list[str]:
    keywords = []
    if "所有官方服裝" in prompt or "全部官方服裝" in prompt:
        keywords.append("all official outfits")
    if "泳裝" in prompt:
        keywords.append("swimsuit")
    if "禮服" in prompt:
        keywords.append("dress")
    if "制服" in prompt:
        keywords.append("uniform")
    return keywords


def _extract_scenes(prompt: str) -> list[str]:
    scenes = []
    if "立繪" in prompt:
        scenes.append("character portrait")
    if "場景" in prompt:
        scenes.append("scene background")
    if "透明背景" in prompt:
        scenes.append("transparent background")
    return scenes


def _extract_actions(prompt: str) -> list[str]:
    actions = []
    if "所有" in prompt and "立繪" in prompt:
        actions.append("batch generation")
    if "動作" in prompt:
        actions.append("action variants")
    return actions


def _extract_matrix_axes(prompt: str) -> list[str]:
    axes: list[str] = []
    if re.search(r"[x×＊]", prompt):
        for chunk in _MATRIX_SPLIT_RE.split(prompt):
            cleaned = chunk.strip("()（） ").strip()
            if not cleaned:
                continue
            if any(marker in cleaned for marker in ("角色", "服裝", "場景", "動作")):
                axes.append(cleaned)
    if "所有官方服裝" in prompt and "服裝" not in " ".join(axes):
        axes.append("outfit")
    if "場景" in prompt and "scene" not in " ".join(axes).lower():
        axes.append("scene")
    if "動作" in prompt and "action" not in " ".join(axes).lower():
        axes.append("action")
    return axes


def _extract_style_keywords(prompt: str) -> list[str]:
    keywords = []
    if "官方" in prompt:
        keywords.append("official style fidelity")
    if "動漫" in prompt or "Anime" in prompt:
        keywords.append("anime illustration")
    if "立繪" in prompt:
        keywords.append("full-body or half-body portrait")
    return keywords


def _infer_modalities(prompt: str, explicit_modality: Modality | None) -> list[Modality]:
    if explicit_modality is not None:
        return [explicit_modality]
    # Training-intent detection takes priority when explicit markers are present
    # (spec §7.1.1 / M4.b).  Detection runs before the generic modality map so
    # that "訓練 LoRA" is not misclassified as IMAGE.
    if _is_training_intent(prompt):
        return [Modality.TRAINING]
    modality_map: list[tuple[Modality, tuple[str, ...]]] = [
        (Modality.IMAGE, ("圖", "圖片", "立繪", "插圖", "image", "portrait", "illustration")),
        (Modality.VOICE, ("語音", "配音", "聲音", "tts", "voice", "speaker")),
        (Modality.MUSIC, ("音樂", "bgm", "歌曲", "music", "sfx")),
        (Modality.VIDEO, ("影片", "動畫", "gif", "video", "mv")),
    ]
    prompt_lower = prompt.lower()
    detected = [modality for modality, keywords in modality_map if any(keyword in prompt_lower or keyword in prompt for keyword in keywords)]
    if detected:
        unique: list[Modality] = []
        for modality in detected:
            if modality not in unique:
                unique.append(modality)
        return unique
    return [Modality.TEXT]


# ---------------------------------------------------------------------------
# Training-intent detection (spec §7.1.1 / M4.b)
# ---------------------------------------------------------------------------

# Keywords that strongly indicate a training / character-factory workflow.
_TRAINING_KEYWORDS_ZH = (
    "訓練", "LoRA", "lora", "角色工廠", "資料集", "訓練配方", "voice clone",
    "語音複製", "聲線複製", "fine-tune", "fine tune", "finetune",
    "kohya", "gpt-sovits", "gpt sovits", "character sheet", "角色卡",
    "dataset", "trigger word", "觸發詞",
)

_TRAINING_PATTERN = re.compile(
    r"(" + "|".join(re.escape(kw) for kw in _TRAINING_KEYWORDS_ZH) + r")",
    flags=re.IGNORECASE,
)


def _is_training_intent(prompt: str) -> bool:
    """Return True when the prompt signals a LoRA / voice-clone training workflow."""
    return bool(_TRAINING_PATTERN.search(prompt))


def _build_required_research(prompt: str, franchise: str | None, characters: list[str], outfits: list[str]) -> list[str]:
    required: list[str] = []
    if franchise and characters and ("官方服裝" in prompt or "all official outfits" in " ".join(outfits)):
        required.append(
            f"Collect the authoritative outfit list and visual references for {characters[0]} from {franchise} before rendering."
        )
    if franchise and not characters:
        required.append("Confirm the exact target character before creating batch assets.")
    return required


def _build_deliverables(
    modalities: list[Modality],
    prompt: str,
    outfits: list[str],
    matrix_axes: list[str],
) -> list[ConsultantDeliverable]:
    deliverables: list[ConsultantDeliverable] = []
    for modality in modalities:
        if modality is Modality.IMAGE:
            variants = [*matrix_axes]
            if not variants and outfits:
                variants.extend(outfits)
            if "立繪" in prompt:
                asset_type = "portrait"
                title = "Character portrait batch"
            else:
                asset_type = "image"
                title = "Image batch"
            deliverables.append(
                ConsultantDeliverable(
                    modality=modality,
                    asset_type=asset_type,
                    title=title,
                    variants=variants,
                    worker="comfyui",
                )
            )
        elif modality is Modality.MUSIC:
            lower_prompt = prompt.lower()
            worker = "stable-audio-tools" if any(keyword in lower_prompt for keyword in ("sfx", "sound effect", "音效", "環境音", "foley")) else "ace-step"
            deliverables.append(
                ConsultantDeliverable(
                    modality=modality,
                    asset_type="music",
                    title="Music generation",
                    worker=worker,
                )
            )
        elif modality is Modality.VOICE:
            worker = "gpt-sovits" if any(keyword in prompt.lower() for keyword in ("clone", "聲線", "角色語音")) else "voxcpm"
            deliverables.append(
                ConsultantDeliverable(
                    modality=modality,
                    asset_type="voice",
                    title="Voice generation",
                    worker=worker,
                )
            )
        elif modality is Modality.VIDEO:
            deliverables.append(
                ConsultantDeliverable(
                    modality=modality,
                    asset_type="video",
                    title="Video generation",
                    worker="comfyui",
                )
            )
    if not deliverables:
        deliverables.append(
            ConsultantDeliverable(
                modality=Modality.TEXT,
                asset_type="text",
                title="Planning note",
                worker=None,
            )
        )
    return deliverables


def _recommend_workers(modalities: list[Modality], prompt: str) -> list[str]:
    workers: list[str] = []
    for modality in modalities:
        if modality is Modality.IMAGE or modality is Modality.VIDEO:
            workers.append("comfyui")
        elif modality is Modality.MUSIC:
            workers.extend(["ace-step", "stable-audio-tools"])
        elif modality is Modality.VOICE:
            workers.extend(["voxcpm", "gpt-sovits"])
            if any(keyword in prompt.lower() for keyword in ("convert", "vc", "轉聲")):
                workers.append("ultimate-rvc")
    unique: list[str] = []
    for worker in workers:
        if worker not in unique:
            unique.append(worker)
    return unique


def _build_search_queries(prompt: str, franchise: str | None, characters: list[str]) -> list[str]:
    queries: list[str] = []
    if franchise and characters:
        queries.extend(
            [
                f"{franchise} {characters[0]} official outfit list",
                f"{franchise} {characters[0]} 立繪 衣裝",
                f"{franchise} {characters[0]} 官方服裝",
            ]
        )
    elif characters:
        queries.append(f"{characters[0]} official outfit list")
    elif franchise:
        queries.append(f"{franchise} character outfit reference")
    else:
        queries.append(prompt)
    return queries


def _build_execution_steps(
    modality: Modality,
    prompt: str,
    deliverables: list[ConsultantDeliverable],
    required_research: list[str],
    recommended_workers: list[str],
) -> list[ConsultantPlanStep]:
    worker = recommended_workers[0] if recommended_workers else None
    steps: list[ConsultantPlanStep] = []
    if required_research:
        steps.append(
            ConsultantPlanStep(
                title="Reference collection",
                detail="Gather the authoritative outfit list and matching visual references before opening the batch generation stage.",
                worker=None,
            )
        )
    steps.append(
        ConsultantPlanStep(
            title="Variant matrix planning",
            detail="Convert the request into a production matrix such as character x outfit x scene x action so each output can be tracked explicitly.",
            worker=None,
        )
    )
    if modality is Modality.IMAGE:
        steps.extend(
            [
                ConsultantPlanStep(
                    title="Preview render",
                    detail="Generate low-resolution previews first to validate composition, costume fidelity, and pose coverage.",
                    worker=worker,
                ),
                ConsultantPlanStep(
                    title="Detail refinement",
                    detail="Use img2img or inpaint passes to fix costume details, accessories, and facial consistency.",
                    worker=worker,
                ),
                ConsultantPlanStep(
                    title="Final upscale",
                    detail="Upscale only accepted images to the final export size after composition and costume fidelity are approved.",
                    worker=worker,
                ),
            ]
        )
    elif modality is Modality.VOICE:
        steps.append(
            ConsultantPlanStep(
                title="Voice render",
                detail="Generate preview lines first, confirm tone and age impression, then batch the remaining voice lines.",
                worker=worker,
            )
        )
    elif modality is Modality.MUSIC:
        steps.append(
            ConsultantPlanStep(
                title="Music render",
                detail="Create short motif previews first, then expand the approved direction into full-length tracks or loops.",
                worker=worker,
            )
        )
    elif modality is Modality.VIDEO:
        steps.append(
            ConsultantPlanStep(
                title="Video render",
                detail="Use approved image assets as inputs for starter video workflows, then polish timing, camera motion, and loop quality.",
                worker=worker,
            )
        )
    if not deliverables:
        steps.append(
            ConsultantPlanStep(
                title="Planning note",
                detail="No executable deliverable was detected, so the result is kept as a planning artifact for follow-up.",
                worker=None,
            )
        )
    return steps


def _build_guidance_path(
    modality: Modality,
    required_research: list[str],
    deliverables: list[ConsultantDeliverable],
) -> list[str]:
    path: list[str] = []
    if required_research:
        path.append("Confirm the missing reference data first.")
    path.append("Approve the production matrix and output scope.")
    if any(deliverable.modality is Modality.IMAGE for deliverable in deliverables):
        path.append("Run preview image generation before committing to final upscale.")
    if any(deliverable.modality is Modality.VOICE for deliverable in deliverables):
        path.append("Generate short voice previews and adjust tone before batching all lines.")
    if any(deliverable.modality is Modality.MUSIC for deliverable in deliverables):
        path.append("Generate short motif previews before rendering full tracks or loops.")
    if any(deliverable.modality is Modality.VIDEO for deliverable in deliverables):
        path.append("Reuse approved image assets as the source for video workflows.")
    return path


def _build_summary(modality: Modality, analysis: ConsultantAnalysis) -> str:
    if analysis.is_training_flow:
        char_hint = f" for {analysis.characters[0]}" if analysis.characters else ""
        return (
            f"Training-flow request detected{char_hint}: the consultant will guide you through "
            "the §7.1 sequence — CharacterSheet → DatasetPack → TrainingRecipe → LoraPreset "
            "(→ optional ImageToVideoRecipe). Each missing entity surfaces a suggestion card "
            "for inline creation. Training execution (kohya_ss / GPT-SoVITS) is offered only "
            "after all required entities are confirmed (spec §4.4)."
        )
    if len(analysis.inferred_modalities) > 1:
        deliverable_modes = ", ".join(modality.value for modality in analysis.inferred_modalities)
        return f"Multimodal request detected: plan coordinated {deliverable_modes} outputs with shared references, staged previews, and follow-up clarification on missing inputs."
    if modality is Modality.IMAGE and analysis.franchise and analysis.characters:
        return (
            f"Image batch request detected: generate portrait-oriented assets for {analysis.characters[0]} "
            f"from {analysis.franchise}, then expand the output set by outfit, scene, or action variants."
        )
    if modality is Modality.VOICE and analysis.characters:
        return f"Voice request detected: prepare voice assets for {analysis.characters[0]} with preview-first validation."
    if modality is Modality.MUSIC:
        return "Music request detected: define mood, instrumentation, and loop structure before batching the final outputs."
    if modality is Modality.VIDEO:
        return "Video request detected: confirm source images, shot rhythm, and motion strategy before rendering."
    return "The request has been converted into a structured production plan with required references, execution steps, and worker suggestions."


def _build_questions(modality: Modality, analysis: ConsultantAnalysis) -> list[str]:
    questions: list[str] = []
    for inferred_modality in analysis.inferred_modalities or [modality]:
        questions.extend(CHECKLISTS.get(inferred_modality.value, []))
    if analysis.required_research:
        questions.insert(0, "要以哪個官方資料來源作為服裝清單與參考圖的基準？")
    elif "官方" in analysis.objective and not analysis.franchise:
        questions.insert(0, "這個角色屬於哪個作品 / IP？請提供官方名稱，這樣我才能正確比對官方服裝與立繪。")
    if Modality.IMAGE in (analysis.inferred_modalities or [modality]) and "character portrait" in analysis.scenes:
        questions.append("每套服裝要做幾個姿勢、表情或鏡位版本？")
    if Modality.IMAGE in (analysis.inferred_modalities or [modality]):
        questions.append("是否要維持官方立繪比例、透明背景與統一尺寸規格？")
    deduped: list[str] = []
    for question in questions:
        if question not in deduped:
            deduped.append(question)
    return deduped[:8]


def _build_next_step(analysis: ConsultantAnalysis) -> str:
    if analysis.is_training_flow:
        return (
            "Complete the training-flow checklist: confirm the CharacterSheet, DatasetPack, "
            "TrainingRecipe, and LoraPreset. Each missing entity surfaces a suggestion card "
            "for inline creation (spec §4.4). Training execution will be offered once all "
            "required entities are selected."
        )
    if analysis.required_research:
        return "Confirm the reference source and outfit list first, then the batch generation jobs can move from blocked to ready."
    if "官方" in analysis.objective and not analysis.franchise:
        return "Confirm the target franchise or IP first, then the reference-matching and batch generation steps can proceed with the correct costume set."
    return "Confirm the variant matrix and output spec, then the planned generation jobs can be executed."


# ---------------------------------------------------------------------------
# Training-flow builders (spec §7.1.1 / M4.b)
# ---------------------------------------------------------------------------

def _build_training_suggestion_cards(
    prompt: str,
    characters: list[str],
) -> list[TrainingSuggestionCard]:
    """Build suggestion cards for each missing §7.1.1 entity.

    Cards follow spec §4.4: they describe a proposed create action with
    pre-filled fields; the frontend renders a clickable button — the
    consultant never auto-executes creation.
    """
    cards: list[TrainingSuggestionCard] = []
    character_name = characters[0] if characters else ""

    # (a) CharacterSheet
    char_prefilled: dict[str, object] = {}
    if character_name:
        char_prefilled["name"] = character_name
    if "觸發詞" in prompt or "trigger" in prompt.lower():
        # Surface a reminder to fill trigger_words; leave value empty for the user.
        char_prefilled["trigger_words"] = []
    cards.append(
        TrainingSuggestionCard(
            entity_kind="character_sheet",
            action="create",
            prefilled=char_prefilled,
            reason="A CharacterSheet anchors the visual identity (trigger words, visual anchors, forbidden features) required before LoRA training can begin.",
        )
    )

    # (b) DatasetPack
    dataset_prefilled: dict[str, object] = {}
    if "danbooru" in prompt.lower():
        dataset_prefilled["source"] = "danbooru"
    elif "pixiv" in prompt.lower():
        dataset_prefilled["source"] = "pixiv"
    elif "手繪" in prompt or "manual" in prompt.lower():
        dataset_prefilled["source"] = "manual"
    cards.append(
        TrainingSuggestionCard(
            entity_kind="dataset_pack",
            action="create",
            prefilled=dataset_prefilled,
            reason="A DatasetPack records the training image collection: source, cleaning status, license, and train/val split strategy.",
        )
    )

    # (c) TrainingRecipe
    recipe_prefilled: dict[str, object] = {}
    if "sdxl" in prompt.lower() or "flux" in prompt.lower():
        recipe_prefilled["base_model"] = "sd-xl-base-1.0" if "sdxl" in prompt.lower() else "flux-dev"
    # Sensible defaults to reduce friction.
    recipe_prefilled.setdefault("rank", 32)
    recipe_prefilled.setdefault("epochs", 10)
    recipe_prefilled.setdefault("optimizer", "AdamW8bit")
    recipe_prefilled.setdefault("caption_strategy", "wd14")
    cards.append(
        TrainingSuggestionCard(
            entity_kind="training_recipe",
            action="create",
            prefilled=recipe_prefilled,
            reason="A TrainingRecipe defines the hyperparameter configuration (base model, LoRA rank, epochs, optimizer, caption strategy) for kohya_ss.",
        )
    )

    # (d) LoraPreset
    lora_prefilled: dict[str, object] = {"name": f"{character_name} Stack" if character_name else "Character Stack"}
    cards.append(
        TrainingSuggestionCard(
            entity_kind="lora_preset",
            action="create",
            prefilled=lora_prefilled,
            reason="A LoraPreset names the stack of LoRA layers (character / costume / style) and their blending weights for batch generation.",
        )
    )

    # (e) ImageToVideoRecipe — optional, only surface if prompt signals video interest.
    if any(kw in prompt for kw in ("影音", "動畫", "i2v", "animatediff", "svd", "video", "影片")):
        i2v_prefilled: dict[str, object] = {
            "workflow_kind": "animatediff",
            "frames": 24,
            "fps": 8,
            "motion_strength": 0.85,
        }
        cards.append(
            TrainingSuggestionCard(
                entity_kind="i2v_recipe",
                action="create",
                prefilled=i2v_prefilled,
                reason="An ImageToVideoRecipe extends accepted character images into animated clips via AnimateDiff / SVD (spec §7.1.1 step e).",
            )
        )

    return cards


def _build_training_execution_steps(
    prompt: str,
    characters: list[str],
) -> list[ConsultantPlanStep]:
    """Build the §7.1 training-sequence execution steps.

    Sequence: prepare dataset → train LoRA (kohya_ss) → optional voice clone
    (GPT-SoVITS) → bulk generate → optional image-to-video.
    Training execution is a later phase (M4.c); steps are surfaced as planned
    / suggestion-card driven, NOT auto-executed.
    """
    steps: list[ConsultantPlanStep] = [
        ConsultantPlanStep(
            title="(a) Confirm or create CharacterSheet",
            detail=(
                "Pick an existing CharacterSheet or create one via the suggestion card. "
                "Supply the character name, visual anchors (e.g. hair colour, eye shape), "
                "trigger words, and any forbidden features."
            ),
            worker=None,
        ),
        ConsultantPlanStep(
            title="(b) Confirm or create DatasetPack",
            detail=(
                "Select an existing DatasetPack or create one. Record the image source, "
                "cleaning status (raw / cleaned / tagged), license, and train/val split strategy."
            ),
            worker=None,
        ),
        ConsultantPlanStep(
            title="(c) Confirm or create TrainingRecipe",
            detail=(
                "Select an existing TrainingRecipe or create one. Key hyperparameters: "
                "base model (SDXL / Flux), LoRA rank (e.g. 32), epochs (e.g. 10), "
                "optimizer (AdamW8bit), caption strategy (wd14 / blip / manual)."
            ),
            worker="kohya_ss",
        ),
        ConsultantPlanStep(
            title="(d) Confirm or create LoraPreset",
            detail=(
                "Select an existing LoraPreset or build one by stacking character LoRA, "
                "costume LoRA, and style LoRA layers with their respective weights."
            ),
            worker=None,
        ),
        ConsultantPlanStep(
            title="Train LoRA via kohya_ss",
            detail=(
                "Once all entities are confirmed, the UI will offer a [Start Training] button "
                "(spec §4.4 — consultant never auto-triggers training). "
                "kohya_ss runs with the selected recipe; output .safetensors is saved to "
                "projects/<id>/models/. Training execution is M4.c."
            ),
            worker="kohya_ss",
        ),
        ConsultantPlanStep(
            title="Bulk generate with trained LoRA",
            detail=(
                "Apply the trained LoRA preset in ComfyUI to generate the variant matrix "
                "(character × outfit × scene × action). "
                "Review and accept outputs before promoting to the final asset library."
            ),
            worker="comfyui",
        ),
    ]

    # Optional voice-clone step.
    if any(kw in prompt for kw in ("聲音", "語音", "voice clone", "語音複製", "gpt-sovits", "GPT-SoVITS")):
        steps.insert(4, ConsultantPlanStep(
            title="Optional: voice clone via GPT-SoVITS",
            detail=(
                "If a character voice clone is required, provide 3–10 s reference audio for "
                "zero-shot inference, or ≥1 h corpus for full fine-tune. "
                "Output model is stored in projects/<id>/models/voices/."
            ),
            worker="gpt-sovits",
        ))

    # Optional i2v step.
    if any(kw in prompt for kw in ("影音", "動畫", "i2v", "animatediff", "svd", "video", "影片")):
        steps.append(ConsultantPlanStep(
            title="Optional: image-to-video via ImageToVideoRecipe",
            detail=(
                "Apply the selected ImageToVideoRecipe to accepted character images using "
                "ComfyUI AnimateDiff or SVD. The source image is supplied at apply-time; "
                "the recipe is a reusable template."
            ),
            worker="comfyui",
        ))

    return steps


def _build_training_guidance_path() -> list[str]:
    """Ordered guidance steps for the training-flow dialog."""
    return [
        "Step (a): Confirm which character to train — pick or create a CharacterSheet.",
        "Step (b): Confirm the dataset — pick or create a DatasetPack with source, cleaning status, and license.",
        "Step (c): Confirm the training hyperparameters — pick or create a TrainingRecipe.",
        "Step (d): Confirm the LoRA stack — pick or create a LoraPreset.",
        "Once all four entities are confirmed, approve the plan to unlock the [Start Training] button.",
        "(Optional) Set an ImageToVideoRecipe to animate accepted outputs after training.",
    ]
