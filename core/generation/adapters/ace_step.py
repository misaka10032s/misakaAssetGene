from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from core.generation.adapters.common import AdapterContext, AdapterExecutionResult, GeneratedArtifact
from core.models.schemas import GenerationJob, Modality


def adapter_name() -> str:
    return "ace-step"


# Explicit job.params -> ACE-Step /release_task field whitelist (GenerateMusicRequest,
# workers/ace-step-1.5/acestep/api/http/release_task_models.py). No **params passthrough
# of arbitrary keys -- only these are threaded through, mirroring comfyui.py's style.
_STRING_PARAM_FIELDS: tuple[tuple[str, str], ...] = (
    ("model", "model"),
    ("vocal_language", "vocal_language"),
    ("key_scale", "key_scale"),
    ("time_signature", "time_signature"),
    # BP-ADAPTER-1 260905: 5Hz LM planning/thinking-mode knobs (see
    # docs/blueprint/entries/BP-ADAPTER-1.md).
    ("lm_model_path", "lm_model_path"),
    ("lm_backend", "lm_backend"),
    ("lm_negative_prompt", "lm_negative_prompt"),
    ("sample_query", "sample_query"),
)

# BP-ADAPTER-1 260905: bool-typed LM/planning knobs. Strict bool only (no
# "true"/"false" string coercion) -- a non-bool value is a caller bug, so it
# fails loud (ValueError) rather than being silently coerced or dropped,
# mirroring this module's existing fail-loud int()/float() casts above.
_BOOL_PARAM_FIELDS: tuple[tuple[str, str], ...] = (
    ("thinking", "thinking"),
    ("use_cot_caption", "use_cot_caption"),
    ("use_cot_language", "use_cot_language"),
)

# BP-ADAPTER-1 260905: float-typed LM sampling knobs.
_FLOAT_PARAM_FIELDS: tuple[tuple[str, str], ...] = (
    ("lm_temperature", "lm_temperature"),
    ("lm_cfg_scale", "lm_cfg_scale"),
    ("lm_top_p", "lm_top_p"),
    ("lm_repetition_penalty", "lm_repetition_penalty"),
)

# BP-ADAPTER-1 260905: int-typed LM sampling knobs.
_INT_PARAM_FIELDS: tuple[tuple[str, str], ...] = (("lm_top_k", "lm_top_k"),)


def _build_payload(job: GenerationJob) -> dict[str, object]:
    """Build the ACE-Step ``/release_task`` payload, threading job.params (spec
    BP-COMFY-3-style tunables carried via ``_build_job`` seeding / the
    ``JobExecutionPatch.params`` PATCH) on top of the prior hardcoded defaults.
    An empty/absent ``params`` reproduces the previous payload byte-for-byte."""
    params = job.params or {}
    tags = params.get("tags")
    if tags is None:
        tags = params.get("prompt")
    if tags is None:
        tags = job.prompt
    lyrics = params.get("lyrics", "")
    seed = params.get("seed")
    payload: dict[str, object] = {
        "prompt": str(tags),
        "global_caption": job.summary,
        "lyrics": str(lyrics),
        "thinking": False,
        "sample_mode": False,
        "use_format": False,
        "inference_steps": int(params.get("inference_steps", 8)),
        "guidance_scale": float(params.get("guidance_scale", 7.0)),
        "use_random_seed": seed is None,
        "seed": int(seed) if seed is not None else -1,
        "task_type": str(params.get("task_type", "text2music")),
        "audio_format": str(params.get("audio_format", "wav")),
    }
    duration = params.get("duration")
    if duration is None:
        duration = params.get("audio_duration")
    if duration is not None:
        payload["audio_duration"] = float(duration)
    if "bpm" in params and params.get("bpm") is not None:
        payload["bpm"] = int(params["bpm"])
    for job_key, worker_key in _STRING_PARAM_FIELDS:
        value = params.get(job_key)
        if value is not None:
            payload[worker_key] = str(value)
    for job_key, worker_key in _BOOL_PARAM_FIELDS:
        if job_key not in params:
            continue
        value = params[job_key]
        if value is None:
            continue
        if not isinstance(value, bool):
            raise ValueError(
                f"job.params[{job_key!r}] must be a bool, got {type(value).__name__}."
            )
        payload[worker_key] = value
    for job_key, worker_key in _FLOAT_PARAM_FIELDS:
        value = params.get(job_key)
        if value is not None:
            payload[worker_key] = float(value)
    for job_key, worker_key in _INT_PARAM_FIELDS:
        value = params.get(job_key)
        if value is not None:
            payload[worker_key] = int(value)
    return payload


def execute(context: AdapterContext) -> AdapterExecutionResult:
    if not context.health_check:
        raise RuntimeError("ACE-Step health check URL is missing.")
    base_url = context.health_check.removesuffix("/health").rstrip("/")
    payload = _build_payload(context.job)

    with httpx.Client(timeout=120.0) as client:
        if context.report_progress:
            context.report_progress(15, "Submitting ACE-Step task")
        response = client.post(f"{base_url}/release_task", json=payload)
        response.raise_for_status()
        body = response.json()
        task_id = str((body.get("data") or {}).get("task_id") or "")
        if not task_id:
            raise RuntimeError("ACE-Step did not return a task id.")

        result_item = _poll_result(client, base_url, task_id, context)
        file_url = str(result_item.get("file") or "")
        if not file_url:
            raise RuntimeError("ACE-Step completed without an audio file.")
        if file_url.startswith("/"):
            file_url = f"{base_url}{file_url}"
        file_response = client.get(file_url)
        file_response.raise_for_status()
        suffix = Path(urlparse(file_url).path).suffix or ".wav"

    artifact = GeneratedArtifact(
        modality=Modality.MUSIC,
        asset_type="music",
        title=context.job.title,
        filename=f"{context.job.project_id}-{context.job.id[:8]}{suffix}",
        description="Generated by ACE-Step.",
        content=file_response.content,
    )
    return AdapterExecutionResult(artifacts=[artifact], metadata={"backend": "ace-step", "task_id": task_id})


def _poll_result(client: httpx.Client, base_url: str, task_id: str, context: AdapterContext) -> dict:
    for attempt in range(180):
        response = client.post(f"{base_url}/query_result", json={"task_id_list": [task_id]})
        response.raise_for_status()
        payload = response.json()
        items = (payload.get("data") or [])
        if not items:
            time.sleep(1)
            continue
        item = items[0]
        status = int(item.get("status") or 0)
        progress_text = str(item.get("progress_text") or "").strip()
        if context.report_progress:
            context.report_progress(min(90, 20 + attempt // 2), progress_text or "Waiting for ACE-Step")
        result_raw = item.get("result") or "[]"
        try:
            result_items = json.loads(result_raw)
        except json.JSONDecodeError:
            result_items = []
        first_result = result_items[0] if result_items and isinstance(result_items[0], dict) else {}
        if status == 1:
            return first_result
        if status == 2:
            raise RuntimeError(str(first_result.get("error") or progress_text or "ACE-Step generation failed."))
        time.sleep(2)
    raise RuntimeError("ACE-Step did not finish within the expected timeout.")
