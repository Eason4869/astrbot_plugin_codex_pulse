from datetime import datetime, timedelta, timezone

from data.plugins.astrbot_plugin_codex_pulse.forecast import Forecast
from data.plugins.astrbot_plugin_codex_pulse.presentation import build_card

TZ = timezone(timedelta(hours=8))


def forecast(**changes):
    now = datetime.now(timezone.utc)
    data = {
        "updated_at": now.isoformat(),
        "last_reset_at": (now - timedelta(days=2)).isoformat(),
        "probabilities": {"rounded_24h": 0, "rounded_48h": None},
    }
    data.update(changes)
    return Forecast.from_payload(data)


def test_compact_direct_notice_has_no_probabilities_or_group_settings():
    card = build_card(forecast(), TZ, "last", notification=True)
    assert card["title"] == "额度已直接重置"
    assert card["icon"] == "check"
    assert not card["probabilities"]
    assert not card["rows"]
    assert "个人额度" in card["note"]


def test_query_preserves_zero_vs_missing_and_group_status():
    card = build_card(forecast(), TZ, "status", group_note="本群通知：已启用（白名单）")
    assert [p["display"] for p in card["probabilities"]] == ["0%", "暂无数据"]
    assert card["group_note"] == "本群通知：已启用（白名单）"
    assert card["rows"]


def test_watch_window_and_signal_are_separate_from_forecast():
    now = datetime.now(timezone.utc)
    f = forecast(
        alert_event_id="watch:1",
        latest_alert={
            "kind": "watch",
            "state": "active",
            "score": 93,
            "source_at": (now - timedelta(minutes=2)).isoformat(),
            "summary": "<script>alert(1)</script>",
            "window": {"end_at": (now + timedelta(hours=6)).isoformat()},
        },
    )
    card = build_card(f, TZ, "watch", notification=True)
    assert card["accent"] == "gold"
    assert card["title"] == "重置已有预告"
    assert "确认" in card["note"]
    assert not card["probabilities"]
    rich = build_card(f, TZ, "forecast")
    assert any("93%" in row["value"] for row in rich["rows"])


def test_stale_warning_dominates_status():
    card = build_card(forecast(), TZ, "status", warning="数据源暂不可用，以下为旧缓存")
    assert card["badge"] == "旧缓存 · 仅供参考"
    assert card["warning"]


def test_no_history_does_not_claim_direct_reset():
    card = build_card(forecast(last_reset_at=None), TZ, "last")
    assert card["primary"] == "暂无记录"
    assert card["badge"] == "暂无记录"


def test_malformed_optional_window_does_not_break_card():
    now = datetime.now(timezone.utc)
    f = forecast(
        alert_event_id="watch:1",
        official_signal={"window": [1]},
        latest_alert={
            "kind": "watch",
            "state": "active",
            "score": 93,
            "source_at": (now - timedelta(minutes=2)).isoformat(),
            "window": None,
        },
    )
    assert build_card(f, TZ, "watch", notification=True)["primary"] == "时间待定"
