from enum import Enum


class NetworkMode(str, Enum):
    AUTO = "auto"
    ALWAYS_OFFLINE = "always_offline"
    ALWAYS_ONLINE = "always_online"
