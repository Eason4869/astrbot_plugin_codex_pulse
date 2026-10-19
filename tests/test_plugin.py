import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from data.plugins.astrbot_plugin_codex_pulse import forecast as forecast_module
from data.plugins.astrbot_plugin_codex_pulse.forecast import Forecast
from data.plugins.astrbot_plugin_codex_pulse.main import CodexPulsePlugin

NOW = datetime(2026, 9, 22, 7, tzinfo=timezone.utc)
GROUP = "bot:GroupMessage:123"
OTHER = "bot:GroupMessage:456"


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Freeze time at the observed live Watch snapshot."""

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(forecast_module, "datetime", FrozenDatetime)


@pytest.fixture
def payload():
    """Provide the API fields involved in the September 22 regression."""
    window = {
        "target_at": "2026-09-23T06:59:59.999Z",
        "end_at": "2026-09-23T06:59:59.999Z",
        "target_kind": "deadline",
    }
    return {
        "updated_at": NOW.isoformat(),
        "last_reset_at": "2026-09-12T08:09:17Z",
        "probabilities": {"rounded_24h": 20, "rounded_48h": 35},
        "confidence": "low",
        "alert_event_id": "signal:2102254445082116335:likely",
        "official_signal": {"window": deepcopy(window)},
        "latest_alert": {
            "kind": "watch",
            "state": "active",
            "source_at": "2026-09-22T04:31:32Z",
            "summary": "A reset is planned for Tuesday.",
            "url": "https://x.com/thsottiaux/status/2102254445082116335",
            "score": 93,
            "window": window,
        },
    }


@pytest.fixture
def plugin():
    """Create a plugin with isolated storage and mocked message delivery."""
    context = SimpleNamespace(
        send_message=AsyncMock(return_value=True),
        platform_manager=SimpleNamespace(platform_insts=[]),
    )
    instance = CodexPulsePlugin(context, {"group_ids": [GROUP]})
    instance.known_groups = {GROUP}
    instance.delivery_cursors = {GROUP: "2026-09-12T08:09:17+00:00"}
    instance.put_kv_data = AsyncMock()
    # Existing receipt tests also exercise the unavailable-renderer fallback.
    instance.html_render = AsyncMock(side_effect=RuntimeError("renderer offline"))
    return instance


def test_forecast_shows_promise_score_window_and_source(payload):
    forecast = Forecast.from_payload(payload)
    rendered = forecast.render(timezone(timedelta(hours=8)), "forecast")
    assert "Tibo 已预告重置" in rendered
    assert "93%（不是 24 小时概率）" in rendered
    assert "24 小时内重置概率：20%" in rendered
    assert "2026-09-23 14:59:59" in rendered
    assert "2026-09-22 12:31:32" in rendered
    assert payload["latest_alert"]["url"] in rendered
    assert "来源：https://codex-reset.com/" in rendered
    last = forecast.render(timezone.utc, "last")
    assert "2026-09-12 08:09:17" in last
    assert "预告" not in last


@pytest.mark.parametrize("score", [83, 93])
@pytest.mark.asyncio
async def test_existing_group_gets_promise_without_new_reset(plugin, payload, score):
    payload["latest_alert"]["score"] = score
    forecast = Forecast.from_payload(payload)
    before = dict(plugin.delivery_cursors)
    await plugin.notify_groups(forecast)
    await plugin.notify_groups(forecast)
    plugin.context.send_message.assert_awaited_once()
    message = plugin.context.send_message.await_args.args[1].chain[0].text
    assert "【Codex 重置预告】" in message
    assert "尚未确认重置完成" in message
    assert "历史模型" not in message
    assert plugin.delivery_cursors == before
    assert plugin.alert_cursors[GROUP] == payload["alert_event_id"]
    saved = plugin.put_kv_data.await_args.args[1]
    assert saved["alerts"][GROUP] == payload["alert_event_id"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "reset"),
        ("kind", "banked"),
        ("state", "expired"),
        ("state", "corrected"),
        ("score", 50),
        ("score", True),
        ("score", {}),
        ("score", []),
        ("score", "93"),
        ("source_at", "invalid"),
        ("source_at", "2026-09-12T08:09:17Z"),
        ("source_at", "2026-09-23T00:00:00Z"),
        ("window", {"end_at": "2026-09-22T06:59:59Z"}),
        ("window", {"end_at": "invalid"}),
        ("window", [1]),
    ],
)
@pytest.mark.asyncio
async def test_inactive_or_malformed_signal_does_not_notify(
    plugin, payload, field, value
):
    payload["latest_alert"][field] = value
    await plugin.notify_groups(Forecast.from_payload(payload))
    plugin.context.send_message.assert_not_awaited()
    assert plugin.alert_cursors == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("latest_alert", None),
        ("alert_event_id", None),
        ("alert_event_id", ""),
        ("alert_event_id", []),
        ("stale", True),
        ("updated_at", "2026-09-22T06:00:00Z"),
    ],
)
@pytest.mark.asyncio
async def test_missing_or_stale_source_does_not_notify(plugin, payload, field, value):
    payload[field] = value
    await plugin.notify_groups(Forecast.from_payload(payload))
    plugin.context.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_undated_promise_can_notify_without_reset_history(plugin, payload):
    payload["last_reset_at"] = None
    payload["latest_alert"]["window"] = None
    payload["official_signal"] = None
    payload["latest_alert"]["score"] = 83
    plugin.delivery_cursors[GROUP] = None
    await plugin.notify_groups(Forecast.from_payload(payload))
    plugin.context.send_message.assert_awaited_once()
    assert plugin.delivery_cursors[GROUP] is None


@pytest.mark.asyncio
async def test_new_group_skips_past_reset_but_receives_current_promise(plugin, payload):
    plugin.delivery_cursors[GROUP] = None
    await plugin.notify_groups(Forecast.from_payload(payload))
    plugin.context.send_message.assert_awaited_once()
    assert plugin.delivery_cursors[GROUP] == "2026-09-12T08:09:17+00:00"


@pytest.mark.parametrize("failure", [False, RuntimeError("offline")])
@pytest.mark.asyncio
async def test_failed_group_retries_independently(plugin, payload, failure):
    plugin.config["group_ids"].append(OTHER)
    plugin.delivery_cursors[OTHER] = plugin.delivery_cursors[GROUP]
    plugin.context.send_message.side_effect = [failure, True, True]
    forecast = Forecast.from_payload(payload)
    await plugin.notify_groups(forecast)
    assert GROUP not in plugin.alert_cursors
    assert plugin.alert_cursors[OTHER] == payload["alert_event_id"]
    await plugin.notify_groups(forecast)
    assert plugin.context.send_message.await_count == 3
    assert plugin.context.send_message.await_args.args[0] == GROUP


@pytest.mark.asyncio
async def test_confirmation_after_promise_still_notifies(plugin, payload):
    await plugin.notify_groups(Forecast.from_payload(payload))
    payload["last_reset_at"] = "2026-09-22T06:30:00Z"
    # Even a lagging active Watch must not be sent after the reset fulfills it.
    await plugin.notify_groups(Forecast.from_payload(payload))
    await plugin.notify_groups(Forecast.from_payload(payload))
    assert plugin.context.send_message.await_count == 2
    text = plugin.context.send_message.await_args.args[1].chain[0].text
    assert "【Codex 额度重置通知】" in text
    assert plugin.delivery_cursors[GROUP] == "2026-09-22T06:30:00+00:00"


@pytest.mark.asyncio
async def test_failed_persistence_does_not_consume_promise(plugin, payload):
    plugin.put_kv_data.side_effect = RuntimeError("storage unavailable")
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await plugin.notify_groups(Forecast.from_payload(payload))
    assert plugin.alert_cursors.get(GROUP) is None
    plugin.put_kv_data.side_effect = None
    await plugin.notify_groups(Forecast.from_payload(payload))
    assert plugin.context.send_message.await_count == 2


@pytest.mark.parametrize("already_sent", [False, True])
@pytest.mark.asyncio
async def test_upgrade_and_reload_preserve_receipts(plugin, payload, already_sent):
    stored = {
        "known_groups": [GROUP],
        "cursors": dict(plugin.delivery_cursors),
    }
    if already_sent:
        stored["alerts"] = {GROUP: payload["alert_event_id"]}
    plugin.get_kv_data = AsyncMock(return_value=stored)
    plugin.config["enabled"] = False
    await plugin.initialize()
    try:
        plugin.config["enabled"] = True
        await plugin.notify_groups(Forecast.from_payload(payload))
        assert plugin.context.send_message.await_count == (0 if already_sent else 1)
    finally:
        await plugin.terminate()


@pytest.mark.parametrize(
    "mode,groups,enabled",
    [
        ("whitelist", [], True),
        ("blacklist", [GROUP], True),
        ("whitelist", [GROUP], False),
    ],
)
@pytest.mark.asyncio
async def test_policy_applies_to_promise_notifications(
    plugin, payload, mode, groups, enabled
):
    plugin.config.update(group_mode=mode, group_ids=groups, enabled=enabled)
    await plugin.notify_groups(Forecast.from_payload(payload))
    plugin.context.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_reenabling_group_does_not_replay_same_promise(plugin, payload):
    forecast = Forecast.from_payload(payload)
    await plugin.notify_groups(forecast)
    plugin.config["group_ids"] = []
    await plugin.refresh_groups()
    plugin.config["group_ids"] = [GROUP]
    await plugin.refresh_groups()
    await plugin.notify_groups(forecast)
    plugin.context.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_later_promise_is_delivered(plugin, payload):
    await plugin.notify_groups(Forecast.from_payload(payload))
    payload["alert_event_id"] = "signal:new:likely"
    payload["latest_alert"]["source_at"] = "2026-09-22T06:00:00Z"
    await plugin.notify_groups(Forecast.from_payload(payload))
    assert plugin.context.send_message.await_count == 2


@pytest.mark.asyncio
async def test_notification_uses_image_and_clickable_source(plugin, payload):
    from astrbot.api.message_components import Image, Plain

    plugin.html_render = AsyncMock(return_value="https://example.com/notice.png")
    await plugin.notify_groups(Forecast.from_payload(payload))
    chain = plugin.context.send_message.await_args.args[1].chain
    assert any(isinstance(item, Image) for item in chain)
    assert any(
        isinstance(item, Plain) and "https://codex-reset.com/" in item.text
        for item in chain
    )
    assert plugin.alert_cursors[GROUP] == payload["alert_event_id"]


@pytest.mark.asyncio
async def test_two_groups_share_one_notice_render(plugin, payload):
    plugin.html_render = AsyncMock(return_value="https://example.com/notice.png")
    plugin.config["group_ids"].append(OTHER)
    plugin.delivery_cursors[OTHER] = plugin.delivery_cursors[GROUP]
    await plugin.notify_groups(Forecast.from_payload(payload))
    assert plugin.html_render.await_count == 1
    assert plugin.context.send_message.await_count == 2


@pytest.mark.parametrize(
    "action", ["状态", "上次", "预测", "status", "last", "forecast"]
)
@pytest.mark.asyncio
async def test_query_is_image_by_default(plugin, payload, action):
    from astrbot.api.event import MessageEventResult
    from astrbot.api.message_components import Image

    plugin.html_render = AsyncMock(return_value="https://example.com/query.png")
    plugin.fetch_forecast = AsyncMock(return_value=Forecast.from_payload(payload))
    plugin.fetch_banked = AsyncMock(return_value=None)
    event = SimpleNamespace(
        get_group_id=lambda: "123",
        get_platform_id=lambda: "bot",
        chain_result=lambda chain: MessageEventResult(chain),
        plain_result=lambda text: MessageEventResult().message(text),
    )
    results = [result async for result in plugin.codex(event, action)]
    assert len(results) == 1
    assert any(isinstance(item, Image) for item in results[0].chain)
    assert results[0].use_t2i_ is False


@pytest.mark.asyncio
async def test_query_render_failure_retains_full_text(plugin, payload):
    from astrbot.api.event import MessageEventResult

    plugin.fetch_forecast = AsyncMock(return_value=Forecast.from_payload(payload))
    plugin.fetch_banked = AsyncMock(return_value=None)
    event = SimpleNamespace(
        get_group_id=lambda: "123",
        get_platform_id=lambda: "bot",
        chain_result=lambda chain: MessageEventResult(chain),
        plain_result=lambda text: MessageEventResult().message(text),
    )
    results = [result async for result in plugin.codex(event)]
    text = results[0].chain[0].text
    assert "24 小时内重置概率：20%" in text
    assert "本群通知：已启用" in text
    assert results[0].use_t2i_ is False


@pytest.mark.asyncio
async def test_explicit_text_mode_skips_renderer(plugin, payload):
    plugin.config["notification_output"] = "text"
    plugin.html_render = AsyncMock(return_value="https://example.com/notice.png")
    await plugin.notify_groups(Forecast.from_payload(payload))
    plugin.html_render.assert_not_awaited()
    assert plugin.context.send_message.await_args.args[1].chain[0].text


@pytest.mark.asyncio
async def test_banked_notice_does_not_advance_direct_reset_receipt(plugin, payload):
    from datetime import timedelta

    from data.plugins.astrbot_plugin_codex_pulse.banked import BankedSnapshot

    plugin.html_render = AsyncMock(return_value="https://example.com/ticket.png")
    payload["latest_alert"] = None
    # Banked module uses real time; use a fresh publication and recent event.
    now = datetime.now(timezone.utc)

    def ticket(at, id):
        return BankedSnapshot.from_payload(
            {
                "updated_at": now.isoformat(),
                "events": [
                    {
                        "id": id,
                        "type": "credits",
                        "group": "credits",
                        "banked_state": "available",
                        "announced_at": at.isoformat(),
                    }
                ],
            }
        )

    baseline = ticket(now - timedelta(days=1), "old")
    latest = ticket(now - timedelta(minutes=1), "new")
    direct_before = dict(plugin.delivery_cursors)
    await plugin.notify_groups(Forecast.from_payload(payload), banked=baseline)
    plugin.context.send_message.assert_not_awaited()
    await plugin.notify_groups(Forecast.from_payload(payload), banked=latest)
    await plugin.notify_groups(Forecast.from_payload(payload), banked=latest)
    plugin.context.send_message.assert_awaited_once()
    assert plugin.delivery_cursors == direct_before
    assert plugin.banked_cursors[GROUP]["id"] == "new"
    assert "直接恢复额度" in plugin.html_render.await_args.args[1]["card"]["note"]


@pytest.mark.asyncio
async def test_reload_preserves_banked_receipt(plugin):
    receipt = {"id": "ticket:1", "at": NOW.isoformat(), "state": "available"}
    plugin.get_kv_data = AsyncMock(
        return_value={
            "known_groups": [GROUP],
            "cursors": dict(plugin.delivery_cursors),
            "alerts": {},
            "banked": {GROUP: receipt},
        }
    )
    plugin.config["enabled"] = False
    await plugin.initialize()
    try:
        assert plugin.banked_cursors == {GROUP: receipt}
        await plugin.save_delivery_state()
        assert plugin.put_kv_data.await_args.args[1]["banked"] == {GROUP: receipt}
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_ticket_notifies_when_forecast_is_unavailable(plugin, monkeypatch):
    from data.plugins.astrbot_plugin_codex_pulse import main as main_module
    from data.plugins.astrbot_plugin_codex_pulse.banked import BankedSnapshot

    now = datetime.now(timezone.utc)
    banked = BankedSnapshot.from_payload(
        {
            "updated_at": now.isoformat(),
            "events": [
                {
                    "id": "ticket:new",
                    "group": "credits",
                    "type": "credits",
                    "banked_state": "available",
                    "announced_at": (now - timedelta(minutes=1)).isoformat(),
                }
            ],
        }
    )
    plugin.banked_cursors[GROUP] = {
        "id": "ticket:old",
        "state": "available",
        "at": (now - timedelta(days=1)).isoformat(),
    }
    plugin.html_render = AsyncMock(return_value="https://example.com/ticket.png")
    plugin.refresh_groups = AsyncMock()
    plugin.fetch_forecast = AsyncMock(side_effect=RuntimeError("forecast offline"))
    plugin.fetch_banked = AsyncMock(return_value=banked)
    monkeypatch.setattr(
        main_module.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)
    )
    with pytest.raises(asyncio.CancelledError):
        await plugin.poll()
    plugin.context.send_message.assert_awaited_once()
    assert plugin.banked_cursors[GROUP]["id"] == "ticket:new"
    assert plugin.delivery_cursors[GROUP] == "2026-09-12T08:09:17+00:00"


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.asyncio
async def test_source_link_switch_controls_both_site_and_tibo(plugin, payload, enabled):
    from astrbot.api.message_components import Image, Plain
    from data.plugins.astrbot_plugin_codex_pulse.presentation import build_card

    plugin.config["send_source_links"] = enabled
    plugin.html_render = AsyncMock(return_value="https://example.com/card.png")
    card = build_card(
        Forecast.from_payload(payload), timezone.utc, "watch", notification=True
    )
    chain = await plugin.card_message(card, "fallback")
    assert any(isinstance(item, Image) for item in chain.chain)
    texts = [item.text for item in chain.chain if isinstance(item, Plain)]
    if enabled:
        assert "https://codex-reset.com/" in "\n".join(texts)
        assert payload["latest_alert"]["url"] in "\n".join(texts)
    else:
        assert texts == []


@pytest.mark.asyncio
async def test_link_switch_does_not_strip_fallback_sources(plugin, payload):
    from data.plugins.astrbot_plugin_codex_pulse.presentation import build_card

    plugin.config["send_source_links"] = False
    f = Forecast.from_payload(payload)
    message = await plugin.card_message(
        build_card(f, timezone.utc), f.render(timezone.utc)
    )
    assert "https://codex-reset.com/" in message.chain[0].text


@pytest.mark.parametrize(
    "key,action", [("notify_direct_reset", "direct"), ("notify_reset_watch", "watch")]
)
@pytest.mark.asyncio
async def test_independent_notification_switches_skip_and_baseline(
    plugin, payload, key, action
):
    plugin.config[key] = False
    if action == "direct":
        payload["last_reset_at"] = "2026-09-22T06:30:00Z"
        payload["latest_alert"] = None
    f = Forecast.from_payload(payload)
    await plugin.notify_groups(f)
    plugin.context.send_message.assert_not_awaited()
    plugin.config[key] = True
    await plugin.notify_groups(f)
    plugin.context.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabling_watch_does_not_disable_direct_reset(plugin, payload):
    plugin.config["notify_reset_watch"] = False
    payload["last_reset_at"] = "2026-09-22T06:30:00Z"
    await plugin.notify_groups(Forecast.from_payload(payload))
    plugin.context.send_message.assert_awaited_once()
    assert "直接重置" in plugin.context.send_message.await_args.args[1].chain[0].text


@pytest.mark.asyncio
async def test_admin_test_command_returns_three_demo_cards_without_network_or_receipts(
    plugin,
):
    from astrbot.api.event import MessageEventResult
    from astrbot.api.message_components import Image

    plugin.html_render = AsyncMock(return_value="https://example.com/demo.png")
    plugin.fetch_forecast = AsyncMock()
    plugin.fetch_banked = AsyncMock()
    plugin.config.update(
        enabled=False,
        notification_output="text",
        query_output="text",
        notify_direct_reset=False,
        notify_reset_watch=False,
        notify_banked_reset=False,
    )
    before = deepcopy(
        (plugin.delivery_cursors, plugin.alert_cursors, plugin.banked_cursors)
    )
    event = SimpleNamespace(
        get_group_id=lambda: "123",
        is_admin=lambda: True,
        chain_result=lambda chain: MessageEventResult(chain),
        plain_result=lambda text: MessageEventResult().message(text),
    )
    results = [result async for result in plugin.codex(event, "测试")]
    assert len(results) == 3
    assert all(
        any(isinstance(item, Image) for item in result.chain) for result in results
    )
    assert all(
        call.args[1]["card"]["test"] for call in plugin.html_render.await_args_list
    )
    assert all(
        "示例数据" in call.args[1]["card"]["test_banner"]
        for call in plugin.html_render.await_args_list
    )
    assert before == (
        plugin.delivery_cursors,
        plugin.alert_cursors,
        plugin.banked_cursors,
    )
    plugin.fetch_forecast.assert_not_awaited()
    plugin.fetch_banked.assert_not_awaited()
    plugin.put_kv_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_admin_cannot_test_cards(plugin):
    from astrbot.api.event import MessageEventResult

    event = SimpleNamespace(
        get_group_id=lambda: "123",
        is_admin=lambda: False,
        plain_result=lambda text: MessageEventResult().message(text),
    )
    results = [result async for result in plugin.codex(event, "测试")]
    assert "仅 AstrBot 管理员" in results[0].chain[0].text
    plugin.html_render.assert_not_awaited()


@pytest.mark.asyncio
async def test_banked_switch_suppresses_only_tickets_and_does_not_replay(plugin):
    from data.plugins.astrbot_plugin_codex_pulse.banked import BankedSnapshot

    plugin.config["notify_banked_reset"] = False
    now = datetime.now(timezone.utc)
    plugin.banked_cursors[GROUP] = {
        "id": "old",
        "state": "available",
        "at": (now - timedelta(days=1)).isoformat(),
    }
    banked = BankedSnapshot.from_payload(
        {
            "updated_at": now.isoformat(),
            "events": [
                {
                    "id": "new",
                    "group": "credits",
                    "type": "credits",
                    "banked_state": "available",
                    "announced_at": (now - timedelta(minutes=1)).isoformat(),
                }
            ],
        }
    )
    await plugin.notify_groups(None, banked)
    plugin.context.send_message.assert_not_awaited()
    assert plugin.banked_cursors[GROUP]["id"] == "new"
    plugin.config["notify_banked_reset"] = True
    await plugin.notify_groups(None, banked)
    plugin.context.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "kind", ["direct", "ticket", "watch", "直接重置", "发卡", "预告"]
)
@pytest.mark.asyncio
async def test_admin_can_test_one_type_with_text_fallback(plugin, kind):
    from astrbot.api.event import MessageEventResult

    event = SimpleNamespace(
        get_group_id=lambda: "123",
        is_admin=lambda: True,
        chain_result=lambda chain: MessageEventResult(chain),
        plain_result=lambda text: MessageEventResult().message(text),
    )
    results = [result async for result in plugin.codex(event, "test", kind)]
    assert len(results) == 1
    assert "示例数据，不是真实重置通知" in results[0].chain[0].text
    assert results[0].use_t2i_ is False


@pytest.mark.asyncio
async def test_test_command_requires_group_and_rejects_unknown_type(plugin):
    from astrbot.api.event import MessageEventResult

    event = SimpleNamespace(
        get_group_id=lambda: "",
        is_admin=lambda: True,
        plain_result=lambda text: MessageEventResult().message(text),
    )
    results = [result async for result in plugin.codex(event, "测试")]
    assert "群聊" in results[0].chain[0].text
    event.get_group_id = lambda: "123"
    results = [result async for result in plugin.codex(event, "测试", "invalid")]
    assert "用法" in results[0].chain[0].text
    plugin.html_render.assert_not_awaited()
