"""炉门密封判定策略与阈值。

判不准时"再压紧一次还是直接报警"按炉门定：平台有一份缺省策略，可按炉门号
覆盖；判定粒度同样在这里定死，只接受逐门独立判，配置成炉组判在装配阶段就
被拒绝，避免运行期两套口径并存。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..errors import ConfigError
from .verdict import ACTION_ALARM, ACTION_PASS, ACTION_REPRESS, ACTIONS, JUDGE_SCOPE


@dataclass(frozen=True)
class DoorSealPolicy:
    """单个炉门的泄漏处置策略。"""

    on_leak: str = ACTION_REPRESS
    on_inconclusive: str = ACTION_ALARM

    def as_dict(self) -> dict:
        return {"on_leak": self.on_leak, "on_inconclusive": self.on_inconclusive}


@dataclass(frozen=True)
class SealConfig:
    """密封判定阈值、缺省策略与逐门覆盖。"""

    scope: str
    latch_tolerance_mm: float
    pressure_band_pa: float
    leak_delta_pa: float
    max_repress: int
    default_policy: DoorSealPolicy
    oven_policies: Mapping[str, DoorSealPolicy]

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "SealConfig":
        scope = str(section.get("scope", JUDGE_SCOPE))
        if scope != JUDGE_SCOPE:
            raise ConfigError(
                f"密封判定粒度只支持逐门独立判 {JUDGE_SCOPE!r}，不接受 {scope!r}",
                component="config",
            )
        tolerance = _number(section, "latch_tolerance_mm", minimum=0.0)
        band = _number(section, "pressure_band_pa", minimum=0.0)
        leak_delta = _number(section, "leak_delta_pa", minimum=0.0)
        if leak_delta < band:
            raise ConfigError("密封泄漏压差阈值不得小于合格压差带", component="config")
        max_repress = int(_number(section, "max_repress", minimum=0.0))
        default_policy = _policy(section, "default_policy")
        overrides = section.get("oven_policies", {})
        if not isinstance(overrides, Mapping):
            raise ConfigError("seal.oven_policies 必须是映射", component="config")
        oven_policies = {str(oven): _policy(overrides, oven) for oven in overrides}
        return cls(
            scope=scope,
            latch_tolerance_mm=tolerance,
            pressure_band_pa=band,
            leak_delta_pa=leak_delta,
            max_repress=max_repress,
            default_policy=default_policy,
            oven_policies=dict(oven_policies),
        )

    def policy_for(self, oven: str) -> DoorSealPolicy:
        """取某炉门的处置策略，未单列时回落缺省策略。"""

        return self.oven_policies.get(oven, self.default_policy)

    def as_dict(self) -> dict:
        return {
            "scope": self.scope,
            "latch_tolerance_mm": self.latch_tolerance_mm,
            "pressure_band_pa": self.pressure_band_pa,
            "leak_delta_pa": self.leak_delta_pa,
            "max_repress": self.max_repress,
            "default_policy": self.default_policy.as_dict(),
            "oven_policies": {oven: policy.as_dict() for oven, policy in self.oven_policies.items()},
        }


def default_seal_config() -> SealConfig:
    """内置缺省密封判定配置。"""

    return SealConfig(
        scope=JUDGE_SCOPE,
        latch_tolerance_mm=2.0,
        pressure_band_pa=5.0,
        leak_delta_pa=15.0,
        max_repress=1,
        default_policy=DoorSealPolicy(on_leak=ACTION_REPRESS, on_inconclusive=ACTION_ALARM),
        oven_policies={},
    )


def _number(section: Mapping[str, Any], key: str, *, minimum: float | None = None) -> float:
    value = section.get(key)
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"配置字段 {key!r} 必须是数值", component="config")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ConfigError(f"配置字段 {key!r} 不得小于 {minimum}", component="config")
    return number


def _policy(section: Mapping[str, Any], key: str) -> DoorSealPolicy:
    raw = section.get(key)
    if raw is None:
        return DoorSealPolicy()
    if not isinstance(raw, Mapping):
        raise ConfigError(f"策略 {key!r} 必须是映射", component="config")
    on_leak = str(raw.get("on_leak", ACTION_REPRESS))
    on_inconclusive = str(raw.get("on_inconclusive", ACTION_ALARM))
    for field_name, choice in (("on_leak", on_leak), ("on_inconclusive", on_inconclusive)):
        if choice not in ACTIONS or choice == ACTION_PASS:
            raise ConfigError(f"策略 {key!r}.{field_name} 只允许 repress 或 alarm", component="config")
    return DoorSealPolicy(on_leak=on_leak, on_inconclusive=on_inconclusive)
