"""Unit tests for the ACE-Step adapter: job.params -> /release_task payload
mapping (lyrics/tags/duration/etc, spec BP-COMFY-3-style tunables) and the
HTTP execute path with the ACE-Step API fully mocked."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from core.generation.adapters import ace_step
from core.generation.adapters.common import AdapterContext
from core.models.schemas import GenerationJob, GenerationJobStatus, Modality


def _job(params: dict | None = None) -> GenerationJob:
    now = datetime.now(UTC)
    return GenerationJob(
        id="job1234abcd",
        project_id="proj1",
        title="Battle theme",
        modality=Modality.MUSIC,
        asset_type="music",
        status=GenerationJobStatus.RUNNING,
        prompt="an epic battle theme",
        summary="Boss fight track",
        worker="ace-step",
        params=params or {},
        created_at=now,
        updated_at=now,
    )


def _resp(url: str, method: str, **kwargs) -> httpx.Response:
    return httpx.Response(200, request=httpx.Request(method, url), **kwargs)


BASELINE_PAYLOAD = {
    "prompt": "an epic battle theme",
    "global_caption": "Boss fight track",
    "lyrics": "",
    "thinking": False,
    "sample_mode": False,
    "use_format": False,
    "inference_steps": 8,
    "guidance_scale": 7.0,
    "use_random_seed": True,
    "seed": -1,
    "task_type": "text2music",
    "audio_format": "wav",
}


def test_empty_params_reproduces_prior_hardcoded_payload() -> None:
    """Byte-for-byte equality with the payload this adapter sent before job
    params were consumed at all -- the core regression guard for this change."""
    payload = ace_step._build_payload(_job())
    assert payload == BASELINE_PAYLOAD


def test_lyrics_param_reaches_payload() -> None:
    payload = ace_step._build_payload(_job({"lyrics": "la la la, fight on"}))
    assert payload["lyrics"] == "la la la, fight on"
    # everything else stays at its prior default.
    assert payload["prompt"] == "an epic battle theme"


def test_tags_param_overrides_prompt_caption() -> None:
    payload = ace_step._build_payload(_job({"tags": "epic, orchestral, choir"}))
    assert payload["prompt"] == "epic, orchestral, choir"


def test_prompt_param_also_overrides_caption_when_tags_absent() -> None:
    payload = ace_step._build_payload(_job({"prompt": "synthwave, driving"}))
    assert payload["prompt"] == "synthwave, driving"


def test_duration_param_maps_to_audio_duration() -> None:
    payload = ace_step._build_payload(_job({"duration": 45}))
    assert payload["audio_duration"] == 45.0


def test_audio_duration_param_name_also_accepted() -> None:
    payload = ace_step._build_payload(_job({"audio_duration": 30.5}))
    assert payload["audio_duration"] == 30.5


def test_no_duration_param_means_no_audio_duration_key() -> None:
    payload = ace_step._build_payload(_job())
    assert "audio_duration" not in payload


def test_seed_param_disables_random_seed() -> None:
    payload = ace_step._build_payload(_job({"seed": 12345}))
    assert payload["seed"] == 12345
    assert payload["use_random_seed"] is False


def test_extra_whitelisted_params_pass_through() -> None:
    payload = ace_step._build_payload(
        _job(
            {
                "inference_steps": 20,
                "guidance_scale": 9.5,
                "task_type": "audio2audio",
                "audio_format": "flac",
                "model": "acestep-v15-turbo",
                "vocal_language": "ja",
                "bpm": 128,
                "key_scale": "C major",
                "time_signature": "3/4",
            }
        )
    )
    assert payload["inference_steps"] == 20
    assert payload["guidance_scale"] == 9.5
    assert payload["task_type"] == "audio2audio"
    assert payload["audio_format"] == "flac"
    assert payload["model"] == "acestep-v15-turbo"
    assert payload["vocal_language"] == "ja"
    assert payload["bpm"] == 128
    assert payload["key_scale"] == "C major"
    assert payload["time_signature"] == "3/4"


class _FakeClient:
    """Minimal httpx.Client stand-in for the ACE-Step release/query/download flow."""

    def __init__(self) -> None:
        self.submitted_payload: dict | None = None

    def post(self, url: str, **kwargs):
        if url.endswith("/release_task"):
            self.submitted_payload = kwargs["json"]
            return _resp(url, "POST", json={"data": {"task_id": "task-1"}})
        if url.endswith("/query_result"):
            return _resp(
                url,
                "POST",
                json={
                    "data": [
                        {
                            "status": 1,
                            "progress_text": "done",
                            "result": '[{"file": "/files/out.wav"}]',
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url: str, **kwargs):
        if url.endswith("/files/out.wav"):
            return _resp(url, "GET", content=b"WAVDATA")
        raise AssertionError(f"unexpected GET {url}")


def test_execute_end_to_end_with_mocked_api_threads_job_params(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient()

    class _CM:
        def __enter__(self):
            return fake

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(ace_step.httpx, "Client", lambda *a, **k: _CM())

    context = AdapterContext(
        project_dir=tmp_path / "proj",
        job=_job({"lyrics": "hello world", "duration": 20}),
        worker_path=tmp_path / "worker",
        health_check="http://127.0.0.1:8401/health",
    )
    result = ace_step.execute(context)
    assert len(result.artifacts) == 1
    assert result.artifacts[0].content == b"WAVDATA"
    assert result.metadata["task_id"] == "task-1"
    assert fake.submitted_payload is not None
    assert fake.submitted_payload["lyrics"] == "hello world"
    assert fake.submitted_payload["audio_duration"] == 20.0
