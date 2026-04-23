from pathlib import Path

from core.consultant.checklists import CHECKLISTS
from core.consultant.few_shot import load_prompt_template
from core.models.schemas import ClarifyRequest


class ConsultantEngine:
    def __init__(self) -> None:
        self.prompt_dir = Path(__file__).resolve().parent / "prompts"

    def clarify(self, request: ClarifyRequest) -> dict[str, object]:
        modality = request.modality.value
        checklist = CHECKLISTS.get(modality, [])
        template = load_prompt_template(self.prompt_dir, modality)
        return {
            "modality": modality,
            "summary": "M0 consultant stub: collect enough detail before generation.",
            "questions": checklist[:5],
            "template_loaded": bool(template),
            "next_step": "Confirm a structured summary before generation.",
        }
