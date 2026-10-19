from datetime import datetime, timedelta, timezone

import pytest
from data.plugins.astrbot_plugin_codex_reset.banked import BankedSnapshot


def snapshot(events):
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "events": events}


def event(**changes):
    record = {
        "id": "ticket-1",
        "group": "credits",
        "type": "credits",
        "banked_state": "available",
        "announced_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "url": "https://x.com/tibo/status/1",
    }
    record.update(changes)
    return record


def test_ticket_is_separate_from_direct_reset_and_api_credits():
    banked = BankedSnapshot.from_payload(
        snapshot(
            [
                event(id="direct", group="reset", type="reset", banked_state=None),
                event(id="api-credits", banked_state=None),
                event(),
            ]
        )
    )
    assert banked.latest.id == "ticket-1"
    assert banked.latest.state == "available"


def test_unknown_state_is_visible_but_never_notifiable():
    banked = BankedSnapshot.from_payload(snapshot([event(banked_state="unknown")]))
    assert banked.latest.state == "unknown"
    assert banked.notifiable() is None


@pytest.mark.parametrize("state", ["announced", "arriving", "available"])
def test_confirmed_ticket_stages_are_recognized(state):
    banked = BankedSnapshot.from_payload(snapshot([event(banked_state=state)]))
    assert banked.notifiable().state == state


def test_malformed_or_future_event_is_ignored():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    banked = BankedSnapshot.from_payload(
        snapshot(
            [
                event(announced_at="broken"),
                event(id=[], announced_at=future),
                event(id="future", announced_at=future),
                event(id="safe"),
            ]
        )
    )
    assert banked.latest.id == "safe"


def test_stale_timeline_cannot_notify():
    data = snapshot([event()])
    data["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert BankedSnapshot.from_payload(data).notifiable() is None


def test_latest_ticket_uses_timestamp_not_payload_order():
    banked = BankedSnapshot.from_payload(
        snapshot(
            [
                event(id="old", announced_at="2026-09-01T00:00:00Z"),
                event(id="new"),
            ]
        )
    )
    assert banked.latest.id == "new"


def test_receipt_allows_stage_progression_but_not_downgrade_or_older_event():
    a = BankedSnapshot.from_payload(snapshot([event(banked_state="announced")])).latest
    data = event(announced_at=a.at.isoformat(), banked_state="available")
    b = BankedSnapshot.from_payload(snapshot([data])).latest
    assert b.follows(a.receipt)
    assert not a.follows(b.receipt)
    assert not b.follows(b.receipt)
    older = BankedSnapshot.from_payload(
        snapshot([event(announced_at="2026-01-01T00:00:00Z")])
    ).latest
    assert not older.follows(b.receipt)
