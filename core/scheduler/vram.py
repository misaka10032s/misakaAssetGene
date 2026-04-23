from enum import Enum


class RuntimeState(str, Enum):
    ACTIVE = "active"
    WARM = "warm"
    COLD = "cold"
