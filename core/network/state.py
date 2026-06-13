from enum import Enum


class NetworkMode(str, Enum):
    """User-selected network policy (spec §11.5 three modes)."""

    AUTO = "auto"
    ALWAYS_OFFLINE = "always_offline"
    ALWAYS_ONLINE = "always_online"


class NetworkState(str, Enum):
    """Effective network state derived from the mode plus live probes.

    Spec §11.5 defines three *modes*; the effective runtime behaviour resolves
    to one of three *states* that gate provider availability:

    * ``ONLINE``   — external/cloud network reachable; cloud LLM providers allowed.
    * ``DEGRADED`` — cloud unreachable but a local LLM backend (Ollama) is up:
      local-only operation, cloud providers disabled. (Interpretation amended
      into spec §11.5; see RESEARCH_LOG.)
    * ``OFFLINE``  — no external network and no local LLM backend reachable:
      cloud providers disabled, AI features fall back to hand-crafted paths.
    """

    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
