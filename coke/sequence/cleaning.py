"""炉门清扫顺序：松开闩锁 → 开炉门 → 清扫 → 复位闩锁 → 密封判定。

清扫完压紧不等于密封合格：每次压紧后都用闩锁行程回读与清扫前后压差做一次
逐门独立判定。判定不合格且该炉门策略要求再压紧时，重新压紧闩锁后再采一次
压、再判一次；再压紧次数有上限，到顶仍不通过就升级为报警。判不准与采样失
败（部分失败）一律不得放行。
"""

from __future__ import annotations

from ..door.latch import DoorLatch
from ..door.machine import DoorCleaner
from ..seal.policy import SealConfig, default_seal_config
from ..seal.verdict import (
    ACTION_ALARM,
    ACTION_PASS,
    ACTION_REPRESS,
    AUTO,
    SealEvidence,
    decide_action,
    judge_seal,
)

MAX_STEPS = 600


def _sample(slot: object, sampler) -> float | None:
    """解析一路采样值。

    显式给数值即按该值判定；显式给 ``None`` 表示该路采样失败（部分失败）；
    ``AUTO`` 时调用采样器取现成信号，没有采样器或采样器返回 None 都按采样
    失败处理。
    """

    if slot is None:
        return None
    if slot is AUTO:
        if sampler is None:
            return None
        sampled = sampler()
        return None if sampled is None else float(sampled)
    return float(slot)  # type: ignore[arg-type]


def clean_door(
    door: DoorCleaner,
    oven: str,
    dt: float = 1.0,
    seal: SealConfig | None = None,
    pressure_before: object = AUTO,
    pressure_after: object = AUTO,
    clamp_readback: object = AUTO,
    sampler=None,
) -> dict:
    """执行一炉炉门清扫并逐门判定密封，返回带步骤、违约项与判定的报告。

    ``pressure_before`` / ``pressure_after`` / ``clamp_readback`` 支持三种给法：
    缺省（AUTO）时走 ``sampler`` 自动采样（回读名义合格）；显式给数值即按该
    回读判定；显式给 None 表示该路信号采样失败（部分失败，不得判合格）。
    """

    seal = seal or default_seal_config()
    steps: list[dict] = []
    violations: list[str] = []

    door.latch.release()
    steps.append(
        {
            "component": "door",
            "action": "release_latch",
            "detail": {"engaged": door.latch.is_engaged(), "position_mm": door.latch.position_mm},
        }
    )

    door.open_door(oven)
    steps.append({"component": "door", "action": "open_door", "detail": {"oven": oven}})

    before_pa = _sample(pressure_before, sampler)
    steps.append(
        {
            "component": "door",
            "action": "sample_pressure_before",
            "detail": {"pressure_pa": None if before_pa is None else round(before_pa, 3)},
        }
    )

    for _ in range(MAX_STEPS):
        if door.progress() >= 1.0:
            break
        door.clean(dt)
    steps.append({"component": "door", "action": "clean", "detail": {"progress": round(door.progress(), 3)}})
    if door.progress() < 1.0:
        violations.append("clean_incomplete")
        return {"oven": oven, "ok": False, "steps": steps, "violations": violations, "seal_attempts": []}

    cycles = door.mark_cleaned()
    steps.append({"component": "door", "action": "mark_cleaned", "detail": {"cycles": cycles}})

    attempts: list[dict] = []
    action = ACTION_PASS
    final_verdict = "sealed"
    final_reasons: list[str] = []
    escalated = False

    for attempt_index in range(1, seal.max_repress + 2):
        if attempt_index == 1:
            readback_slot = DoorLatch.NOMINAL if clamp_readback is AUTO else clamp_readback
            door.close_door(readback_slot)
            steps.append(
                {
                    "component": "door",
                    "action": "close_door",
                    "detail": {
                        "latch_engaged": door.latch.is_engaged(),
                        "cleaned": door.is_cleaned(),
                        "readback_ok": door.latch.readback_ok,
                    },
                }
            )
        else:
            # 再压紧是一次新的机构动作：名义压紧、回读正常，压力重新采。
            door.latch.set_engaged(DoorLatch.NOMINAL)
            steps.append(
                {
                    "component": "door",
                    "action": "repress_latch",
                    "detail": {
                        "attempt": attempt_index,
                        "latch_cycles": door.latch.cycles(),
                        "readback_ok": door.latch.readback_ok,
                    },
                }
            )

        after_slot = pressure_after if attempt_index == 1 else AUTO
        after_pa = _sample(after_slot, sampler)
        steps.append(
            {
                "component": "door",
                "action": "sample_pressure_after",
                "detail": {
                    "attempt": attempt_index,
                    "pressure_pa": None if after_pa is None else round(after_pa, 3),
                },
            }
        )

        evidence = SealEvidence(
            oven=oven,
            attempt=attempt_index,
            latch_travel_mm=door.latch.travel_mm,
            latch_tolerance_mm=seal.latch_tolerance_mm,
            latch_readback_mm=door.latch.engaged_readback_mm(),
            pressure_before_pa=before_pa,
            pressure_after_pa=after_pa,
            pressure_band_pa=seal.pressure_band_pa,
            leak_delta_pa=seal.leak_delta_pa,
        )
        evidence_dict = evidence.as_dict()
        verdict, reasons = judge_seal(evidence)
        policy = seal.policy_for(oven)
        action, escalated = decide_action(
            verdict, policy.on_leak, policy.on_inconclusive, attempt_index, seal.max_repress
        )

        attempts.append(
            {
                "attempt": attempt_index,
                "verdict": verdict,
                "reasons": list(reasons),
                "action": action,
                "repress_escalated": escalated,
                "evidence": evidence_dict,
                "policy": policy.as_dict(),
            }
        )
        steps.append(
            {
                "component": "door",
                "action": "seal_check",
                "detail": {
                    "attempt": attempt_index,
                    "verdict": verdict,
                    "decision": action,
                    "reasons": ",".join(reasons),
                },
            }
        )

        final_verdict = verdict
        final_reasons = list(reasons)
        if action != ACTION_REPRESS:
            break

    if action == ACTION_ALARM:
        violations.append("seal_alarm")

    return {
        "oven": oven,
        "ok": not violations,
        "steps": steps,
        "violations": violations,
        "seal_verdict": final_verdict,
        "seal_action": action,
        "seal_reasons": final_reasons,
        "seal_attempts": attempts,
        "seal_repress_escalated": escalated,
    }
