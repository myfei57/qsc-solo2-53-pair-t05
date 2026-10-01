"""炉门密封泄漏判定（纯判定域，不含硬件动作）。

判定粒度在模块里定死为逐门独立判（:data:`JUDGE_SCOPE`）：每条结论只属于一
个炉门，不允许按炉组合并出结论。判定只用两路现成信号，不加硬件：

1. 闩锁压紧行程回读：回读缺失属于**部分失败**，不能据此认定密封合格；回读
   值低于"额定行程−公差"是机械未压实的硬证据，判泄漏；
2. 清扫前／清扫后压差：任一侧采样失败同样属于部分失败，不得判合格；压差落
   在合格带内判合格，落在临界带判不准，越过泄漏阈值判泄漏。

硬泄漏证据（行程不足、压差越限）优先于部分失败：即使另一路信号采样失败，
只要一路给出了确定的泄漏证据，结论仍是泄漏而不是判不准。
"""

from __future__ import annotations

from dataclasses import dataclass

# 判定粒度定死：只支持逐门独立判定。
JUDGE_SCOPE = "door"

VERDICT_SEALED = "sealed"
VERDICT_LEAK = "leak"
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICTS = (VERDICT_SEALED, VERDICT_LEAK, VERDICT_INCONCLUSIVE)

ACTION_PASS = "pass"
ACTION_REPRESS = "repress"
ACTION_ALARM = "alarm"
ACTIONS = (ACTION_PASS, ACTION_REPRESS, ACTION_ALARM)

REASON_LATCH_READBACK_FAILED = "latch_readback_failed"
REASON_LATCH_TRAVEL_SHORT = "latch_travel_short"
REASON_PRESSURE_SAMPLE_FAILED = "pressure_sample_failed"
REASON_PRESSURE_DELTA_EXCEEDED = "pressure_delta_exceeded"
REASON_PRESSURE_DELTA_BORDERLINE = "pressure_delta_borderline"
REASON_REPRESS_LIMIT_REACHED = "repress_limit_reached"

# 采样值缺省标记：调用方未显式给值时由平台用现成信号自动采样；
# 显式传 None 表示该路采样失败（部分失败）。
AUTO = object()


@dataclass(frozen=True)
class SealEvidence:
    """一次密封判定所依据的全部原始量（判据快照）。"""

    oven: str
    attempt: int
    latch_travel_mm: float
    latch_tolerance_mm: float
    latch_readback_mm: float | None
    pressure_before_pa: float | None
    pressure_after_pa: float | None
    pressure_band_pa: float
    leak_delta_pa: float

    @property
    def latch_readback_ok(self) -> bool:
        """闩锁行程回读是否成功。"""

        return self.latch_readback_mm is not None

    @property
    def latch_travel_short(self) -> bool:
        """回读行程是否低于额定行程减公差（机械未压实）。"""

        if self.latch_readback_mm is None:
            return False
        return self.latch_readback_mm < self.latch_travel_mm - self.latch_tolerance_mm

    @property
    def pressure_ok(self) -> bool:
        """清扫前后两侧压差是否都采样成功。"""

        return self.pressure_before_pa is not None and self.pressure_after_pa is not None

    @property
    def pressure_delta_pa(self) -> float | None:
        """清扫后相对清扫前的压差绝对值；任一侧失败则为 None。"""

        if not self.pressure_ok:
            return None
        return abs(float(self.pressure_after_pa) - float(self.pressure_before_pa))

    def as_dict(self) -> dict:
        return {
            "oven": self.oven,
            "attempt": self.attempt,
            "latch_travel_mm": round(self.latch_travel_mm, 3),
            "latch_tolerance_mm": round(self.latch_tolerance_mm, 3),
            "latch_readback_mm": None if self.latch_readback_mm is None else round(self.latch_readback_mm, 3),
            "latch_readback_ok": self.latch_readback_ok,
            "pressure_before_pa": (
                None if self.pressure_before_pa is None else round(float(self.pressure_before_pa), 3)
            ),
            "pressure_after_pa": (
                None if self.pressure_after_pa is None else round(float(self.pressure_after_pa), 3)
            ),
            "pressure_delta_pa": None if self.pressure_delta_pa is None else round(self.pressure_delta_pa, 3),
            "pressure_ok": self.pressure_ok,
            "pressure_band_pa": round(self.pressure_band_pa, 3),
            "leak_delta_pa": round(self.leak_delta_pa, 3),
        }


def judge_seal(evidence: SealEvidence) -> tuple[str, list[str]]:
    """依据行程回读与清扫前后压差判密封，返回结论与理由码。

    结论只有三档：合格（sealed）、泄漏（leak）、判不准（inconclusive）。
    任何一路部分失败都排除合格档；硬泄漏证据优先，直接判泄漏。
    """

    reasons: list[str] = []

    if not evidence.latch_readback_ok:
        reasons.append(REASON_LATCH_READBACK_FAILED)
    elif evidence.latch_travel_short:
        reasons.append(REASON_LATCH_TRAVEL_SHORT)

    delta = evidence.pressure_delta_pa
    if not evidence.pressure_ok:
        reasons.append(REASON_PRESSURE_SAMPLE_FAILED)
    elif delta > evidence.leak_delta_pa:
        reasons.append(REASON_PRESSURE_DELTA_EXCEEDED)
    elif delta > evidence.pressure_band_pa:
        reasons.append(REASON_PRESSURE_DELTA_BORDERLINE)

    hard_leak = (
        REASON_LATCH_TRAVEL_SHORT in reasons or REASON_PRESSURE_DELTA_EXCEEDED in reasons
    )
    if hard_leak:
        verdict = VERDICT_LEAK
    elif reasons:
        verdict = VERDICT_INCONCLUSIVE
    else:
        verdict = VERDICT_SEALED
    return verdict, reasons


def decide_action(
    verdict: str, on_leak: str, on_inconclusive: str, attempt: int, max_repress: int
) -> tuple[str, bool]:
    """按炉门策略把结论翻译成系统动作。

    返回 ``(动作, 是否因再压紧次数到顶而升级报警)``。合格直接放行；泄漏与判
    不准的处置分别由该炉门的策略决定；策略要求再压紧但次数已到顶时，强制升
    级为报警。
    """

    if verdict == VERDICT_SEALED:
        return ACTION_PASS, False
    choice = on_leak if verdict == VERDICT_LEAK else on_inconclusive
    if choice == ACTION_REPRESS and attempt <= max_repress:
        return ACTION_REPRESS, False
    return ACTION_ALARM, choice == ACTION_REPRESS
