"""炉门闩锁。

闩锁有压紧与松开两态：压紧时炉门密封、清扫头不能进入；松开时炉门可以开
清扫。闩锁行程按配置核对，超差即认为没有真正松开。

压紧后的行程回读用来判断是否真的压实：回读成功时记录压紧行程；回读失败
（现场总线上不来读数，``readback_mm=None``）属于部分失败——机构仍按压紧处
置，但密封判定不能把"读不到行程"当作压紧合格。
"""

from __future__ import annotations

from ..errors import InterlockError


class DoorLatch:
    """带行程核对与压紧回读的炉门闩锁。"""

    # 未显式提供回读时的标记：表示按额定行程名义压紧（回读正常）。
    NOMINAL = object()

    def __init__(self, travel_mm: float) -> None:
        self._travel_mm = float(travel_mm)
        self._engaged = True
        self._position_mm = float(travel_mm)
        self._readback_ok = True
        self._cycles = 0

    @property
    def travel_mm(self) -> float:
        return self._travel_mm

    @property
    def position_mm(self) -> float:
        return self._position_mm

    @property
    def readback_ok(self) -> bool:
        """最近一次压紧的行程回读是否成功。"""

        return self._readback_ok

    def engaged_readback_mm(self) -> float | None:
        """压紧行程回读值；未压紧或回读失败时返回 None。"""

        if not self._engaged or not self._readback_ok:
            return None
        return self._position_mm

    def set_engaged(self, readback_mm: float | object | None = NOMINAL) -> bool:
        """压紧闩锁，返回压紧状态。

        ``readback_mm`` 给数值时按回读行程记录；显式传 ``None`` 表示行程回读
        失败（部分失败）：机构进入压紧态，但回读状态标记为不可用。
        """

        self._engaged = True
        self._cycles += 1
        if readback_mm is None:
            self._readback_ok = False
            self._position_mm = self._travel_mm
        elif readback_mm is self.NOMINAL:
            self._readback_ok = True
            self._position_mm = self._travel_mm
        else:
            self._readback_ok = True
            self._position_mm = float(readback_mm)  # type: ignore[arg-type]
        return self._engaged

    def release(self, position_mm: float | None = None) -> bool:
        """松开闩锁，返回松开状态；行程不足即拒绝。"""

        measured = self._travel_mm if position_mm is None else float(position_mm)
        if measured < self._travel_mm:
            raise InterlockError(
                f"闩锁行程 {measured} 不足 {self._travel_mm}，未真正松开", component="door"
            )
        self._engaged = False
        self._readback_ok = False
        self._position_mm = 0.0
        return self._engaged

    def is_engaged(self) -> bool:
        """闩锁是否处于压紧状态。"""

        return self._engaged

    def cycles(self) -> int:
        """返回闩锁压紧次数。"""

        return self._cycles
