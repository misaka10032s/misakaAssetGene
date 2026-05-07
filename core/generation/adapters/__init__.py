from __future__ import annotations

from collections.abc import Callable

from core.generation.adapters.common import AdapterContext, AdapterExecutionResult
from core.generation.adapters.ace_step import execute as execute_ace_step
from core.generation.adapters.comfyui import execute as execute_comfyui
from core.generation.adapters.gpt_sovits import execute as execute_gpt_sovits
from core.generation.adapters.stable_audio_tools import execute as execute_stable_audio_tools
from core.generation.adapters.ultimate_rvc import execute as execute_ultimate_rvc
from core.generation.adapters.voxcpm import execute as execute_voxcpm

AdapterExecutor = Callable[[AdapterContext], AdapterExecutionResult]

ADAPTERS: dict[str, AdapterExecutor] = {
    "comfyui": execute_comfyui,
    "ace-step": execute_ace_step,
    "gpt-sovits": execute_gpt_sovits,
    "gpt_sovits": execute_gpt_sovits,
    "stable-audio-tools": execute_stable_audio_tools,
    "ultimate-rvc": execute_ultimate_rvc,
    "voxcpm": execute_voxcpm,
}


def get_adapter(worker_name: str) -> AdapterExecutor | None:
    return ADAPTERS.get(worker_name)
