from dataclasses import dataclass


@dataclass
class BundleMember:
    name: str
    modality: str
    depends_on: list[str]


def draft_bundle(bundle_type: str) -> list[BundleMember]:
    if bundle_type == "promo":
        return [
            BundleMember(name="key_visual", modality="image", depends_on=[]),
            BundleMember(name="copy", modality="text", depends_on=[]),
            BundleMember(name="voiceover", modality="voice", depends_on=["copy"]),
            BundleMember(name="teaser", modality="video", depends_on=["key_visual", "voiceover"]),
        ]
    return []
