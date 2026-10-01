"""炉门密封泄漏判定（纯软件判据，不改硬件）。

判据只有两条，都来自既有信号：

1. **闩锁行程回读**：压紧后闩锁实际到位的行程；
2. **清扫前后压差**：清扫前与压紧后集气管压力之差，下降越大越可疑。

判定粒度（定死）
----------------
**逐门独立判定**。泄漏是单门密封的物理属性，闩锁回读与压差采样都是单门信
号，部分失败也是单门传感器故障；按炉组统一判定会让一门故障连坐整组，或
把单门泄漏摊薄成群组"正常"。因此本模块只提供单门判定入口，炉组维度仅允
许控制台按门罗列展示，不合成组级结论。

部分失败
--------
压差采样失败或闩锁行程回读失败都属于部分失败：结论为 ``inconclusive``，
动作一律报警，绝不能当作密封合格——数据不可信时盲压无法验证，反而可能
掩盖泄漏。

历史不可变
----------
每次判定把依据（行程回读、压差、阈值与策略快照）与结论一起追加进台账与
审计流水；已出判定不回改，后续数据只能追加新记录，不能改写历史结论。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ..config import DoorLeakPolicy

#: 判定粒度：逐门独立（定死，不提供炉组统一判定）。
GRANULARITY = "per_door"

SEALED = "sealed"
LEAK = "leak"
UNCERTAIN = "uncertain"
INCONCLUSIVE = "inconclusive"

ACTION_PASS = "pass"
ACTION_RE_TIGHTEN = "re_tighten"
ACTION_ALARM = "alarm"

#: 判定结论的中文说明，审计与控制台共用。
VERDICT_MESSAGES = {
    SEALED: "炉门密封合格",
    LEAK: "判定炉门泄漏",
    UNCERTAIN: "炉门密封判不准",
    INCONCLUSIVE: "炉门密封证据缺失（部分失败）",
}

#: 处置动作对应的审计严重度：报警档才会进控制台告警页。
ACTION_SEVERITIES = {
    ACTION_PASS: "notice",
    ACTION_RE_TIGHTEN: "warning",
    ACTION_ALARM: "alarm",
}


@dataclass(frozen=True)
class LeakEvidence:
    """一次判定的输入证据；``None`` 表示该路采样／回读失败（部分失败）。"""

    latch_readback_mm: float | None
    pressure_before_pa: float | None
    pressure_after_pa: float | None

    def as_dict(self) -> dict:
        """转成可落审计的字典。"""

        return {
            "latch_readback_mm": self.latch_readback_mm,
            "pressure_before_pa": self.pressure_before_pa,
            "pressure_after_pa": self.pressure_after_pa,
        }


@dataclass(frozen=True)
class LeakJudgment:
    """一次判定结论：判定、处置动作、依据码与压差。"""

    verdict: str
    action: str
    reasons: tuple[str, ...]
    dp_drop_pa: float | None


def _retighten_or_alarm(policy: DoorLeakPolicy, retightens_used: int, reasons: list[str]) -> str:
    """预算内重新压紧，预算耗尽报警。"""

    if retightens_used < policy.max_retighten:
        return ACTION_RE_TIGHTEN
    reasons.append("retighten_budget_exhausted")
    return ACTION_ALARM


def judge_leak(evidence: LeakEvidence, policy: DoorLeakPolicy, retightens_used: int) -> LeakJudgment:
    """对单门做一次泄漏判定。

    ``retightens_used`` 是本轮清扫已执行的重新压紧次数，用于压紧预算控制。
    判定是纯函数：同样的证据与策略必然得到同样的结论，结论可复算、可复查。
    """

    failures: list[str] = []
    if evidence.latch_readback_mm is None:
        failures.append("latch_readback_failed")
    if evidence.pressure_before_pa is None or evidence.pressure_after_pa is None:
        failures.append("pressure_sample_failed")
    if failures:
        # 部分失败：不得当作密封合格；数据不可信，直接报警。
        return LeakJudgment(INCONCLUSIVE, ACTION_ALARM, tuple(failures), None)

    dp_drop = float(evidence.pressure_before_pa) - float(evidence.pressure_after_pa)
    readback = float(evidence.latch_readback_mm)

    hard: list[str] = []
    gray: list[str] = []
    if readback < policy.travel_fail_mm:
        hard.append("latch_travel_below_fail")
    elif readback < policy.travel_pass_mm:
        gray.append("latch_travel_marginal")
    if dp_drop > policy.dp_drop_fail_pa:
        hard.append("dp_drop_above_fail")
    elif dp_drop > policy.dp_drop_pass_pa:
        gray.append("dp_drop_marginal")

    if hard:
        return LeakJudgment(LEAK, _retighten_or_alarm(policy, retightens_used, hard), tuple(hard), dp_drop)
    if gray:
        reasons = list(gray)
        action = ACTION_ALARM
        if policy.on_uncertain == "re_tighten_once":
            # “再压紧一次”：本轮尚未压过且预算允许时才压，否则报警。
            if retightens_used < 1 and retightens_used < policy.max_retighten:
                action = ACTION_RE_TIGHTEN
            else:
                reasons.append("retighten_budget_exhausted")
        return LeakJudgment(UNCERTAIN, action, tuple(reasons), dp_drop)
    return LeakJudgment(SEALED, ACTION_PASS, ("seal_ok",), dp_drop)


class LeakRegister:
    """逐门泄漏判定台账：只追加，不改写；最新结论由历史派生。"""

    def __init__(self) -> None:
        self._by_oven: dict[str, list[dict]] = {}
        self._lock = threading.RLock()

    def append(self, oven: str, record: dict) -> None:
        """追加一条判定记录；台账不提供任何改写入口。"""

        with self._lock:
            self._by_oven.setdefault(oven, []).append(dict(record))

    def history(self, oven: str) -> list[dict]:
        """返回单门全部历史判定（按时间升序），供复查。"""

        with self._lock:
            return [dict(item) for item in self._by_oven.get(oven, [])]

    def latest(self, oven: str) -> dict | None:
        """返回单门最新一条判定；没有判过返回 ``None``。"""

        with self._lock:
            entries = self._by_oven.get(oven)
            return dict(entries[-1]) if entries else None

    def latest_all(self) -> list[dict]:
        """返回每门最新一条判定（按炉号排序），供控制台展示。"""

        with self._lock:
            return [dict(items[-1]) for _oven, items in sorted(self._by_oven.items()) if items]
