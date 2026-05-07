from pathlib import Path

from core.consultant.few_shot import load_prompt_template
from core.consultant.planner import build_clarify_result
from core.models.schemas import ClarifyRequest, ClarifyResult


class ConsultantEngine:
    def __init__(self) -> None:
        self.prompt_dir = Path(__file__).resolve().parent / "prompts"

    def clarify(self, request: ClarifyRequest) -> ClarifyResult:
        modality = request.modality.value if request.modality else "text"
        template = load_prompt_template(self.prompt_dir, modality)
        return build_clarify_result(request, bool(template))
