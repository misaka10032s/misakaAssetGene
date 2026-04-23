from dataclasses import dataclass


@dataclass
class GenerationRequest:
    modality: str
    backend: str
    prompt: str


def orchestrate(request: GenerationRequest) -> dict[str, str]:
    return {
        "status": "queued",
        "modality": request.modality,
        "backend": request.backend,
    }
