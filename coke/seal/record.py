"""密封判定的不可变结论记录。

一旦结论落档就是历史事实：再压紧不会改写旧结论，只会追加一条新结论并通过
``parent_id`` 链接到上一条。记录是冻结数据类，字段口径与审计流水一致，可
直接 JSONL 落盘并按字段回放。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .verdict import ACTIONS, JUDGE_SCOPE, VERDICTS


@dataclass(frozen=True)
class SealRecord:
    """一次炉门密封判定的历史结论。"""

    ts: float
    verdict_id: str
    op_id: str
    oven: str
    scope: str
    attempt: int
    verdict: str
    action: str
    reasons: tuple[str, ...]
    repress_escalated: bool
    latch_readback_mm: float | None
    pressure_before_pa: float | None
    pressure_after_pa: float | None
    pressure_delta_pa: float | None
    latch_travel_mm: float
    latch_tolerance_mm: float
    pressure_band_pa: float
    leak_delta_pa: float
    parent_id: str | None
    policy: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        ts: float,
        verdict_id: str,
        op_id: str,
        oven: str,
        attempt: int,
        verdict: str,
        action: str,
        reasons: tuple[str, ...] | list[str],
        repress_escalated: bool,
        evidence: dict,
        parent_id: str | None,
        policy: dict,
        scope: str = JUDGE_SCOPE,
    ) -> "SealRecord":
        if verdict not in VERDICTS:
            raise ValueError(f"未知密封结论 {verdict!r}")
        if action not in ACTIONS:
            raise ValueError(f"未知处置动作 {action!r}")
        return cls(
            ts=float(ts),
            verdict_id=verdict_id,
            op_id=op_id,
            oven=str(oven),
            scope=scope,
            attempt=int(attempt),
            verdict=verdict,
            action=action,
            reasons=tuple(reasons),
            repress_escalated=bool(repress_escalated),
            latch_readback_mm=evidence.get("latch_readback_mm"),
            pressure_before_pa=evidence.get("pressure_before_pa"),
            pressure_after_pa=evidence.get("pressure_after_pa"),
            pressure_delta_pa=evidence.get("pressure_delta_pa"),
            latch_travel_mm=float(evidence["latch_travel_mm"]),
            latch_tolerance_mm=float(evidence["latch_tolerance_mm"]),
            pressure_band_pa=float(evidence["pressure_band_pa"]),
            leak_delta_pa=float(evidence["leak_delta_pa"]),
            parent_id=parent_id,
            policy=dict(policy),
        )

    @classmethod
    def from_dict(cls, raw: dict) -> "SealRecord":
        """从落盘字段回放一条历史结论。"""

        return cls(
            ts=float(raw.get("ts", 0.0)),
            verdict_id=str(raw.get("verdict_id", "")),
            op_id=str(raw.get("op_id", "")),
            oven=str(raw.get("oven", "")),
            scope=str(raw.get("scope", JUDGE_SCOPE)),
            attempt=int(raw.get("attempt", 1)),
            verdict=str(raw.get("verdict", "")),
            action=str(raw.get("action", "")),
            reasons=tuple(raw.get("reasons", ())),
            repress_escalated=bool(raw.get("repress_escalated", False)),
            latch_readback_mm=raw.get("latch_readback_mm"),
            pressure_before_pa=raw.get("pressure_before_pa"),
            pressure_after_pa=raw.get("pressure_after_pa"),
            pressure_delta_pa=raw.get("pressure_delta_pa"),
            latch_travel_mm=float(raw.get("latch_travel_mm", 0.0)),
            latch_tolerance_mm=float(raw.get("latch_tolerance_mm", 0.0)),
            pressure_band_pa=float(raw.get("pressure_band_pa", 0.0)),
            leak_delta_pa=float(raw.get("leak_delta_pa", 0.0)),
            parent_id=raw.get("parent_id"),
            policy=dict(raw.get("policy", {})),
        )

    def as_dict(self) -> dict:
        return {
            "ts": round(self.ts, 3),
            "verdict_id": self.verdict_id,
            "op_id": self.op_id,
            "oven": self.oven,
            "scope": self.scope,
            "attempt": self.attempt,
            "verdict": self.verdict,
            "action": self.action,
            "reasons": list(self.reasons),
            "repress_escalated": self.repress_escalated,
            "latch_readback_mm": self.latch_readback_mm,
            "pressure_before_pa": self.pressure_before_pa,
            "pressure_after_pa": self.pressure_after_pa,
            "pressure_delta_pa": self.pressure_delta_pa,
            "latch_travel_mm": round(self.latch_travel_mm, 3),
            "latch_tolerance_mm": round(self.latch_tolerance_mm, 3),
            "pressure_band_pa": round(self.pressure_band_pa, 3),
            "leak_delta_pa": round(self.leak_delta_pa, 3),
            "parent_id": self.parent_id,
            "policy": self.policy,
        }
