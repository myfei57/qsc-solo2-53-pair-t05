"""炉门密封泄漏判定。

判定粒度定死为逐门独立判；两路判据（闩锁压紧行程回读、清扫前后压差）任一采
样失败都只是部分失败，结论只能是判不准而不是合格；泄漏与判不准的处置按炉门
策略（再压紧或直接报警）执行；结论只追加、不可改写，并同步审计供控制台展示。
"""

from __future__ import annotations

import dataclasses

import pytest

from coke.config import PlatformConfig, config_to_mapping, default_config
from coke.console.api import Api
from coke.door.latch import DoorLatch
from coke.errors import ConfigError
from coke.seal.policy import DoorSealPolicy
from coke.seal.verdict import (
    ACTION_ALARM,
    ACTION_PASS,
    ACTION_REPRESS,
    JUDGE_SCOPE,
    SealEvidence,
    decide_action,
    judge_seal,
)
from coke.sequence import clean_door


def _evidence(readback=60.0, before=30.0, after=30.0, tol=2.0, band=5.0, leak=15.0):
    return SealEvidence(
        oven="oven01",
        attempt=1,
        latch_travel_mm=60.0,
        latch_tolerance_mm=tol,
        latch_readback_mm=readback,
        pressure_before_pa=before,
        pressure_after_pa=after,
        pressure_band_pa=band,
        leak_delta_pa=leak,
    )


def test_sealed_when_travel_full_and_delta_in_band():
    verdict, reasons = judge_seal(_evidence(after=34.0))
    assert verdict == "sealed"
    assert reasons == []


def test_latch_readback_failure_is_partial_not_sealed():
    verdict, reasons = judge_seal(_evidence(readback=None))
    assert verdict == "inconclusive"
    assert "latch_readback_failed" in reasons


def test_pressure_sample_failure_is_partial_not_sealed():
    verdict, reasons = judge_seal(_evidence(after=None))
    assert verdict == "inconclusive"
    assert "pressure_sample_failed" in reasons
    verdict_before, reasons_before = judge_seal(_evidence(before=None))
    assert verdict_before == "inconclusive"
    assert "pressure_sample_failed" in reasons_before


def test_short_travel_is_hard_leak_even_when_pressure_fails():
    verdict, reasons = judge_seal(_evidence(readback=50.0, after=None))
    assert verdict == "leak"
    assert "latch_travel_short" in reasons
    assert "pressure_sample_failed" in reasons


def test_delta_bands_split_borderline_and_leak():
    assert judge_seal(_evidence(after=38.0))[0] == "inconclusive"
    assert "pressure_delta_borderline" in judge_seal(_evidence(after=38.0))[1]
    assert judge_seal(_evidence(after=46.0))[0] == "leak"
    assert "pressure_delta_exceeded" in judge_seal(_evidence(after=46.0))[1]


def test_decide_action_follows_verdict_and_policy():
    assert decide_action("sealed", ACTION_REPRESS, ACTION_ALARM, 1, 1)[0] == ACTION_PASS
    action, escalated = decide_action("leak", ACTION_REPRESS, ACTION_ALARM, 1, 1)
    assert action == ACTION_REPRESS and escalated is False
    action, escalated = decide_action("leak", ACTION_REPRESS, ACTION_ALARM, 2, 1)
    assert action == ACTION_ALARM and escalated is True
    assert decide_action("leak", ACTION_ALARM, ACTION_REPRESS, 1, 1)[0] == ACTION_ALARM
    action, escalated = decide_action("inconclusive", ACTION_REPRESS, ACTION_ALARM, 1, 1)
    assert action == ACTION_ALARM and escalated is False


def test_sequence_pressure_failure_alarms_without_repress(platform):
    report = platform.clean(oven="oven01", pressure_after=None)
    assert report["seal_verdict"] == "inconclusive"
    assert report["seal_action"] == ACTION_ALARM
    assert report["ok"] is False
    assert len(report["seal_attempts"]) == 1
    assert platform.door.latch.cycles() == 1


def test_sequence_clamp_readback_failure_is_not_sealed(platform):
    report = platform.clean(oven="oven01", clamp_readback=None)
    assert report["seal_verdict"] == "inconclusive"
    assert "latch_readback_failed" in report["seal_reasons"]
    assert report["seal_action"] == ACTION_ALARM


def test_sequence_leak_represses_once_then_passes(platform):
    report = platform.clean(oven="oven01", pressure_after=100.0)
    assert [a["verdict"] for a in report["seal_attempts"]] == ["leak", "sealed"]
    assert [a["action"] for a in report["seal_attempts"]] == ["repress", "pass"]
    assert platform.door.latch.cycles() == 2
    records = platform.seals.entries("oven01")
    assert records[0]["verdict"] == "leak"
    assert records[1]["parent_id"] == records[0]["verdict_id"]
    assert records[1]["scope"] == JUDGE_SCOPE


def test_sequence_repress_that_cannot_rescue_escalates_to_alarm(platform):
    report = platform.clean(oven="oven01", sampler=lambda: 95.0, pressure_before=30.0)
    attempts = [(a["verdict"], a["action"], a["repress_escalated"]) for a in report["seal_attempts"]]
    assert attempts == [("leak", "repress", False), ("leak", "alarm", True)]
    assert report["ok"] is False
    assert "seal_alarm" in report["violations"]
    assert platform.door.latch.cycles() == 2
    assert platform.seals.count() == 2


def test_verdicts_are_historical_and_immutable(platform):
    platform.clean(oven="oven01", pressure_after=100.0)
    first = platform.seals.entries("oven01")[0]
    snapshot = dict(first)
    platform.clean(oven="oven01")
    again = platform.seals.entries("oven01")[0]
    assert again == snapshot
    assert again["verdict"] == "leak"


def test_policy_is_per_oven(platform):
    cfg = dataclasses.replace(
        default_config(),
        seal=dataclasses.replace(
            default_config().seal,
            oven_policies={
                "oven09": DoorSealPolicy(on_leak=ACTION_ALARM, on_inconclusive=ACTION_REPRESS)
            },
        ),
    )
    from coke.app import build_platform

    custom = build_platform(cfg, storage_root=platform.store.root + "-custom")
    leak = custom.clean(oven="oven09", pressure_after=100.0)
    assert len(leak["seal_attempts"]) == 1
    assert leak["seal_action"] == ACTION_ALARM
    inconclusive = custom.clean(oven="oven09", pressure_after=38.0)
    assert [a["verdict"] for a in inconclusive["seal_attempts"]] == ["inconclusive", "sealed"]
    default_oven = custom.clean(oven="oven01", pressure_after=100.0)
    assert default_oven["seal_attempts"][0]["action"] == ACTION_REPRESS


def test_verdict_events_carried_to_audit_with_severity(platform):
    platform.clean(oven="oven01", pressure_after=100.0)
    entries = platform.audit.entries(limit=100)
    by_action = {entry["action"]: entry for entry in entries}
    assert by_action["seal_repress"]["severity"] == "warning"
    assert by_action["seal_passed"]["severity"] == "notice"
    assert by_action["seal_passed"]["payload"]["scope"] == JUDGE_SCOPE
    assert by_action["seal_passed"]["payload"]["parent_id"]

    platform.clean(oven="oven02", pressure_after=38.0)
    entries = platform.audit.entries(limit=100)
    alarm = next(entry for entry in entries if entry["action"] == "seal_alarm")
    assert alarm["severity"] == "alarm"
    assert alarm["oven"] == "oven02"
    assert "pressure_delta_borderline" in alarm["payload"]["reasons"]


def test_console_exposes_verdicts_and_accepts_null_signals(platform):
    platform.clean(oven="oven01", pressure_after=None)
    api = Api(platform)
    status, body = api.handle("GET", "/api/seals", {}, {})
    assert status == 200 and body["scope"] == JUDGE_SCOPE and len(body["seals"]) == 1
    status, latest = api.handle("GET", "/api/seal/oven01", {}, {})
    assert latest["latest"]["verdict"] == "inconclusive"

    status, passed = api.handle("POST", "/api/clean", {}, {"oven": "oven03"})
    assert passed["seal_verdict"] == "sealed"
    status, failed = api.handle("POST", "/api/clean", {}, {"oven": "oven04", "clamp_readback_mm": None})
    assert failed["seal_verdict"] == "inconclusive"
    status, bad = api.handle("POST", "/api/clean", {}, {"oven": "oven05", "pressure_after_pa": "nope"})
    assert status == 409 and bad["component"] == "console"

    # 逐门独立：oven02 的后续数据不能改写 oven01。
    api.handle("POST", "/api/clean", {}, {"oven": "oven02"})
    assert api.handle("GET", "/api/seal/oven01", {}, {})[1]["latest"]["verdict"] == "inconclusive"
    overview = platform.status()["seal"]
    assert overview["latest_by_oven"]["oven01"]["verdict"] == "inconclusive"
    assert overview["latest_by_oven"]["oven02"]["verdict"] == "sealed"


def test_history_replays_across_restart(tmp_path):
    from coke.app import build_platform

    root = str(tmp_path / "state")
    first = build_platform(default_config(), storage_root=root)
    first.clean(oven="oven01", pressure_after=None)
    reopened = build_platform(default_config(), storage_root=root)
    assert reopened.seals.count() == 1
    latest = reopened.seals.latest_for("oven01")
    assert latest["verdict"] == "inconclusive"
    assert latest["scope"] == JUDGE_SCOPE


def test_config_rejects_non_door_scope():
    payload = config_to_mapping(default_config())
    payload["seal"]["scope"] = "battery"
    with pytest.raises(ConfigError):
        PlatformConfig.from_mapping(payload)


def test_config_rejects_band_above_leak_threshold():
    payload = config_to_mapping(default_config())
    payload["seal"]["pressure_band_pa"] = 50.0
    with pytest.raises(ConfigError):
        PlatformConfig.from_mapping(payload)


def test_config_rejects_illegal_policy_action():
    payload = config_to_mapping(default_config())
    payload["seal"]["default_policy"]["on_leak"] = "pass"
    with pytest.raises(ConfigError):
        PlatformConfig.from_mapping(payload)


def test_door_latch_marks_readback_failure_as_partial():
    latch = DoorLatch(60.0)
    latch.release()
    assert latch.set_engaged(None) is True
    assert latch.is_engaged() is True
    assert latch.readback_ok is False
    assert latch.engaged_readback_mm() is None
