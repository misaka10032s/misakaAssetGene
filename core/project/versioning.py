from dataclasses import dataclass


@dataclass
class VersionRecord:
    version_id: str
    asset_type: str
    prompt_hash: str
