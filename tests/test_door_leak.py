"""炉门密封泄漏判定：判据、部分失败、逐门策略、审计与历史不可变。"""

from __future__ import annotations

import dataclasses
import json

import pytest

from coke.app import build_platform
from coke.clock import ManualClock
from coke.config import (
    DoorLeakPolicy,
    LeakLimits,
    PlatformConfig,
    config_to_mapping,
    default_config,
    load_config,
)
from coke.console.api import Api
from coke.door.leak import (
    ACTION_ALARM,
    ACTION_PASS,
    ACTION_RE_TIGHTEN,
    INCONCLUSIVE,
    LEAK,
    SEALED,
    UNCERTAIN,
    LeakEvidence,
    judge_leak,
)
from coke.errors import ConfigError


def _policy(**overrides) -> DoorLeakPolicy:
    base = {
        "travel_pass_mm": 58.0,
        "travel_fail_mm": 54.0,
        "dp_drop_pass_pa": 5.0,
        "dp_drop_fail_pa": 15.0,
        "on_uncertain": "re_tighten_once",
        "max_retighten": 1,
    }
    base.update(overrides)
    return DoorLeakPolicy(**base)


def _platform_with_leak(tmp_path, clock, leak: LeakLimits):
    config = dataclasses.replace(default_config(), leak=leak)
    return build_platform(config, clock=clock, storage_root=str(tmp_path / "state"))


# ---------- 纯判定：判据与处置 ----------


def test_sealed_when_both_signals_pass():
    judgment = judge_leak(LeakEvidence(60.0, 90.0, 89.0), _policy(), 0)
    assert judgment.verdict == SEALED
    assert judgment.action == ACTION_PASS
    assert judgment.dp_drop_pa == pytest.approx(1.0)


def test_hard_leak_retightens_within_budget_then_alarms():
    evidence = LeakEvidence(40.0, 90.0, 89.0)  # 行程远低于硬失败阈
    first = judge_leak(evidence, _policy(max_retighten=1), 0)
    assert (first.verdict, first.action) == (LEAK, ACTION_RE_TIGHTEN)
    second = judge_leak(evidence, _policy(max_retighten=1), 1)
    assert (second.verdict, second.action) == (LEAK, ACTION_ALARM)
    assert "retighten_budget_exhausted" in second.reasons


def test_pressure_drop_above_fail_is_leak():
    judgment = judge_leak(LeakEvidence(60.0, 90.0, 70.0), _policy(), 0)
    assert judgment.verdict == LEAK
    assert "dp_drop_above_fail" in judgment.reasons


def test_uncertain_follows_per_door_policy():
    evidence = LeakEvidence(56.0, 90.0, 89.0)  # 行程落在灰区
    retighten = judge_leak(evidence, _policy(on_uncertain="re_tighten_once"), 0)
    assert (retighten.verdict, retighten.action) == (UNCERTAIN, ACTION_RE_TIGHTEN)
    alarm = judge_leak(evidence, _policy(on_uncertain="alarm"), 0)
    assert (alarm.verdict, alarm.action) == (UNCERTAIN, ACTION_ALARM)
    # “再压紧一次”只给一次：本轮已压过就直接报警
    again = judge_leak(evidence, _policy(on_uncertain="re_tighten_once"), 1)
    assert again.action == ACTION_ALARM
    assert "retighten_budget_exhausted" in again.reasons


def test_latch_readback_failure_is_partial_failure_never_sealed():
    judgment = judge_leak(LeakEvidence(None, 90.0, 89.0), _policy(), 0)
    assert judgment.verdict == INCONCLUSIVE
    assert judgment.action == ACTION_ALARM
    assert judgment.verdict != SEALED
    assert "latch_readback_failed" in judgment.reasons


def test_pressure_sample_failure_is_partial_failure_never_sealed():
    for evidence in (LeakEvidence(60.0, None, 89.0), LeakEvidence(60.0, 90.0, None)):
        judgment = judge_leak(evidence, _policy(), 0)
        assert judgment.verdict == INCONCLUSIVE
        assert judgment.action == ACTION_ALARM
        assert judgment.verdict != SEALED
        assert "pressure_sample_failed" in judgment.reasons


# ---------- 平台编排：判定落审计、系统按结论处置 ----------


def test_clean_judges_sealed_and_records_evidence(platform):
    platform.start()
    report = platform.clean(oven="oven01")
    assert report["ok"] is True
    assert report["leak"]["verdict"] == "sealed"
    assert report["leak"]["decision"] == "pass"
    verdicts = [entry for entry in platform.audit.entries(limit=50) if entry["action"] == "leak_verdict"]
    assert verdicts
    payload = verdicts[0]["payload"]
    assert payload["verdict"] == "sealed"
    assert payload["granularity"] == "per_door"
    assert payload["evidence"]["latch_readback_mm"] == platform.door.latch.travel_mm
    assert payload["evidence"]["pressure_before_pa"] is not None
    assert payload["policy"]["on_uncertain"] == "re_tighten_once"


def test_leak_triggers_retighten_then_sealed(platform):
    platform.start()
    report = platform.clean(oven="oven01", latch_readback_mm=40.0)
    leak = report["leak"]
    assert leak["verdict"] == "sealed"  # 系统重新压紧后复判合格
    assert [attempt["decision"] for attempt in leak["attempts"]] == ["re_tighten", "pass"]
    assert leak["attempts"][0]["verdict"] == "leak"
    history = platform.door_leak_history("oven01")["history"]
    assert [item["verdict"] for item in history] == ["leak", "sealed"]


def test_leak_alarms_immediately_when_retighten_budget_zero(tmp_path, clock):
    leak = LeakLimits(58.0, 54.0, 5.0, 15.0, "re_tighten_once", 0, {})
    platform = _platform_with_leak(tmp_path, clock, leak)
    platform.start()
    report = platform.clean(oven="oven01", latch_readback_mm=40.0)
    assert report["leak"]["verdict"] == "leak"
    assert report["leak"]["decision"] == "alarm"
    alarms = platform.audit.alarms()
    assert any(item["action"] == "leak_verdict" for item in alarms)


def test_uncertain_action_is_decided_per_door(tmp_path, clock):
    leak = LeakLimits(58.0, 54.0, 5.0, 15.0, "re_tighten_once", 1, {"oven02": {"on_uncertain": "alarm"}})
    platform = _platform_with_leak(tmp_path, clock, leak)
    platform.start()
    oven01 = platform.clean(oven="oven01", latch_readback_mm=56.0)  # 灰区：默认策略再压一次
    assert [attempt["decision"] for attempt in oven01["leak"]["attempts"]] == ["re_tighten", "pass"]
    oven02 = platform.clean(oven="oven02", latch_readback_mm=56.0)  # 同一证据，按门直接报警
    assert oven02["leak"]["verdict"] == "uncertain"
    assert oven02["leak"]["decision"] == "alarm"


def test_latch_readback_failure_is_not_treated_as_sealed(platform):
    platform.start()
    report = platform.clean(oven="oven01", latch_readback_ok=False)
    assert report["leak"]["verdict"] == "inconclusive"
    assert report["leak"]["decision"] == "alarm"
    assert report["leak"]["verdict"] != "sealed"


def test_pressure_sample_failure_is_not_treated_as_sealed(platform):
    platform.start()
    report = platform.clean(oven="oven01", pressure_sample_ok=False)
    assert report["leak"]["verdict"] == "inconclusive"
    assert report["leak"]["decision"] == "alarm"
    assert report["leak"]["verdict"] != "sealed"


# ---------- 历史不可变与复查 ----------


def test_history_is_append_only_and_not_rewritten(platform):
    platform.start()
    platform.clean(oven="oven01", latch_readback_ok=False)  # 第一次：部分失败 → 报警
    platform.clean(oven="oven01")  # 第二次：合格
    history = platform.door_leak_history("oven01")["history"]
    assert [item["verdict"] for item in history] == ["inconclusive", "sealed"]
    # 已出判定属于历史不可变：后续合格数据不改写第一次的报警结论
    assert history[0]["decision"] == "alarm"
    assert platform.door_leak_history("oven01")["latest"]["verdict"] == "sealed"


def test_verdicts_survive_restart_for_review(tmp_path):
    clock = ManualClock()
    storage = str(tmp_path / "state")
    first = build_platform(default_config(), clock=clock, storage_root=storage)
    first.start()
    first.clean(oven="oven01", latch_readback_ok=False)
    second = build_platform(default_config(), clock=clock, storage_root=storage)
    history = second.door_leak_history("oven01")["history"]
    assert [item["verdict"] for item in history] == ["inconclusive"]
    doors = second.leak_status()["doors"]
    assert doors[0]["decision"] == "alarm"


# ---------- 控制台展示（与审计同源） ----------


def test_console_exposes_latest_and_history(platform):
    platform.start()
    platform.clean(oven="oven01", latch_readback_ok=False)
    api = Api(platform)
    status, payload = api.handle("GET", "/api/leak", {}, {})
    assert status == 200
    assert payload["granularity"] == "per_door"
    assert payload["doors"][0]["verdict"] == "inconclusive"
    status, history = api.handle("GET", "/api/leak/history", {"oven": "oven01"}, {})
    assert status == 200
    assert len(history["history"]) == 1
    assert payload["doors"][0]["op_id"] == history["latest"]["op_id"]


def test_console_clean_passes_field_readings_through(platform):
    api = Api(platform)
    platform.start()
    status, payload = api.handle("POST", "/api/clean", {}, {"oven": "oven01", "latch_readback_mm": 40.0})
    assert status == 200
    assert payload["leak"]["attempts"][0]["verdict"] == "leak"
    status, payload = api.handle("POST", "/api/clean", {}, {"oven": "oven01", "pressure_sample_ok": False})
    assert status == 200
    assert payload["leak"]["verdict"] == "inconclusive"


def test_status_includes_leak_section(platform):
    platform.start()
    platform.clean(oven="oven01")
    status = platform.status()
    assert status["leak"]["granularity"] == "per_door"
    assert status["leak"]["doors"][0]["verdict"] == "sealed"


# ---------- 配置：按炉门定、装配期校验 ----------


def test_leak_config_round_trips(tmp_path):
    payload = config_to_mapping(default_config())
    path = tmp_path / "coke.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_config(str(path)).leak == default_config().leak


def test_policy_for_applies_door_override():
    leak = LeakLimits(58.0, 54.0, 5.0, 15.0, "re_tighten_once", 1, {"oven09": {"on_uncertain": "alarm"}})
    assert leak.policy_for("oven09").on_uncertain == "alarm"
    assert leak.policy_for("oven01").on_uncertain == "re_tighten_once"
    assert leak.policy_for("oven01").max_retighten == 1


def test_leak_config_rejects_bad_uncertain_policy():
    payload = config_to_mapping(default_config())
    payload["limits"]["leak"]["on_uncertain"] = "guess"
    with pytest.raises(ConfigError):
        PlatformConfig.from_mapping(payload)


def test_leak_config_rejects_inverted_thresholds():
    payload = config_to_mapping(default_config())
    payload["limits"]["leak"]["travel_fail_mm"] = 59.0
    with pytest.raises(ConfigError):
        PlatformConfig.from_mapping(payload)


def test_leak_config_rejects_unknown_override_field():
    payload = config_to_mapping(default_config())
    payload["limits"]["leak"]["doors"] = {"oven03": {"unknown": 1}}
    with pytest.raises(ConfigError):
        PlatformConfig.from_mapping(payload)
