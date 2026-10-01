"""炉门清扫状态快照。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DoorSnapshot:
    """炉门清扫某一时刻的状态。"""

    oven: str
    door_open: bool
    latch_engaged: bool
    latch_position_mm: float
    latch_readback_ok: bool
    latch_readback_mm: float | None
    cleaned: bool
    progress: float
    cycles: int

    def as_dict(self) -> dict:
        return {
            "oven": self.oven,
            "door_open": self.door_open,
            "latch_engaged": self.latch_engaged,
            "latch_position_mm": round(self.latch_position_mm, 2),
            "latch_readback_ok": self.latch_readback_ok,
            "latch_readback_mm": (
                None if self.latch_readback_mm is None else round(self.latch_readback_mm, 2)
            ),
            "cleaned": self.cleaned,
            "progress": round(self.progress, 3),
            "cycles": self.cycles,
        }
