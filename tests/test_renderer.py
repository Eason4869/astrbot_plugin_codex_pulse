import asyncio
from unittest.mock import AsyncMock

import pytest
from data.plugins.astrbot_plugin_codex_pulse import card_renderer as module
from data.plugins.astrbot_plugin_codex_pulse.card_renderer import CardRenderer


@pytest.mark.asyncio
async def test_render_cache_reuses_same_card_but_isolates_group_state():
    renderer = AsyncMock(return_value="https://example.com/card.png")
    service = CardRenderer()
    card = {"title": "test", "group_note": "group A"}
    first, second = await asyncio.gather(
        service.render(card, renderer), service.render(card, renderer)
    )
    assert first == second == "https://example.com/card.png"
    renderer.assert_awaited_once()
    await service.render({**card, "group_note": "group B"}, renderer)
    assert renderer.await_count == 2


@pytest.mark.asyncio
async def test_upstream_html_is_escaped_and_local_logo_is_embedded():
    renderer = AsyncMock(return_value="https://example.com/card.png")
    await CardRenderer().render({"title": '<img src="http://internal/">'}, renderer)
    args = renderer.await_args
    assert "<svg" in args.args[0]
    assert "OpenAI" in args.args[0]
    assert "&lt;img" in args.args[1]["card"]["title"]
    assert args.kwargs["return_url"] is True


@pytest.mark.parametrize("result", [None, "", "not-a-file", "javascript:alert(1)"])
@pytest.mark.asyncio
async def test_invalid_render_result_falls_back(result):
    service = CardRenderer()
    assert await service.render({}, AsyncMock(return_value=result)) is None


@pytest.mark.asyncio
async def test_render_failure_has_short_cooldown():
    renderer = AsyncMock(side_effect=RuntimeError("unavailable"))
    service = CardRenderer()
    assert await service.render({}, renderer) is None
    assert await service.render({"another": True}, renderer) is None
    renderer.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeout_returns_fallback(monkeypatch):
    monkeypatch.setattr(module, "RENDER_TIMEOUT", 0.001)

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    assert await CardRenderer().render({}, slow) is None


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed():
    renderer = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await CardRenderer().render({}, renderer)
