from typing import Protocol


class ProjectDraft(Protocol):
    name: str
    type: str
    synopsis: str


def build_initial_style_guide(request: ProjectDraft) -> str:
    synopsis = request.synopsis.strip() or "Add a short synopsis to improve consultant suggestions."
    return (
        "# Style Guide\n\n"
        f"- Project: {request.name}\n"
        f"- Type: {request.type}\n"
        f"- Synopsis: {synopsis}\n"
    )
