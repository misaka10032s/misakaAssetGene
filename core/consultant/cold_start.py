from core.project.manager import ProjectSummary


def generate_cold_start(project: ProjectSummary | None, modality: str) -> list[str]:
    if not project:
        return ["Describe the asset you need and I will help narrow it down."]

    base = f"{project.type} project"
    if modality == "music":
        return [f"{base} opening theme", f"{base} town bgm", f"{base} battle cue"]
    if modality == "image":
        return [f"{base} hero portrait", f"{base} key visual", f"{base} town scene"]
    if modality == "voice":
        return [f"{base} protagonist voice line", f"{base} narrator intro"]
    return [f"{base} teaser clip", f"{base} loop animation"]
