"""炉门密封泄漏判定。

清扫完压紧就算通过无法发现冒烟前的密封缺陷。本子包在不动硬件的前提下，用
闩锁压紧行程回读与清扫前后压差逐门独立判定：合格放行，泄漏或判不准时按该
炉门配置决定再压紧还是直接报警；任何一路采样失败只构成部分失败，不得判合
格。每次判定的判据与结论落不可变档案并同步审计，历史结论只追加、不改写。
"""

from __future__ import annotations

from .history import SealHistory
from .policy import DoorSealPolicy, SealConfig, default_seal_config
from .record import SealRecord
from .verdict import (
    ACTION_ALARM,
    ACTION_PASS,
    ACTION_REPRESS,
    AUTO,
    JUDGE_SCOPE,
    VERDICT_INCONCLUSIVE,
    VERDICT_LEAK,
    VERDICT_SEALED,
    SealEvidence,
    decide_action,
    judge_seal,
)

__all__ = [
    "SealHistory",
    "SealRecord",
    "SealConfig",
    "DoorSealPolicy",
    "default_seal_config",
    "SealEvidence",
    "judge_seal",
    "decide_action",
    "JUDGE_SCOPE",
    "VERDICT_SEALED",
    "VERDICT_LEAK",
    "VERDICT_INCONCLUSIVE",
    "ACTION_PASS",
    "ACTION_REPRESS",
    "ACTION_ALARM",
    "AUTO",
]
