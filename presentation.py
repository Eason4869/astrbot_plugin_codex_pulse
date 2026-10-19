"""Structured card content; data validation stays in Forecast/BankedSnapshot."""

import math
from datetime import datetime, timedelta, timezone

from .banked import BankedSnapshot
from .forecast import Forecast, parse_time

SOURCE = "https://codex-reset.com/"
BANKED_LABELS = {
    "announced": "重置卡发放已公告",
    "arriving": "重置卡正在发放",
    "available": "已观测到重置卡发放",
    "unknown": "重置卡状态待确认",
}


def _date(value, tz):
    try:
        return parse_time(value).astimezone(tz) if value else None
    except ValueError:
        return None


def build_card(
    forecast: Forecast | None,
    tz: timezone,
    action: str = "status",
    *,
    notification: bool = False,
    warning: str = "",
    group_note: str = "",
    banked: BankedSnapshot | None = None,
    banked_notice: bool = False,
) -> dict:
    """Build compact notices or complete query cards from validated snapshots."""
    if forecast is None and not (banked_notice and banked and banked.latest):
        raise ValueError("A query requires a forecast; ticket notices require a ticket")
    data = forecast.payload if forecast else {}
    watch = forecast.active_watch() if forecast else None
    updated = forecast.updated_at if forecast else banked.updated_at
    offset = updated.astimezone(tz).strftime("%z")
    zone = f"UTC{offset[:3]}:{offset[3:]}"
    card = {
        "accent": "blue",
        "icon": "clock",
        "title": "公共重置状态",
        "subtitle": "公共额度直接重置与重置卡分别追踪",
        "badge": "暂无有效预告",
        "time_label": "上次额度直接重置",
        "primary": "暂无记录",
        "date": "",
        "zone": zone,
        "note": "公共事件不代表每个账号已到账，个人额度请在 Codex 中确认。",
        "warning": warning,
        "group_note": group_note,
        "rows": [],
        "probabilities": [],
        "source": SOURCE,
        "updated": updated.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
        "links": [SOURCE],
        "notification": notification,
    }
    last = (
        forecast.last_reset.astimezone(tz) if forecast and forecast.last_reset else None
    )
    if last:
        card.update(
            primary=last.strftime("%H:%M"), date=last.strftime("%Y 年 %m 月 %d 日")
        )
    if action == "last":
        card.update(
            title="额度已直接重置" if notification else "上次额度直接重置",
            badge="网站已确认" if last else "暂无记录",
            icon="check",
            time_label="公共重置确认时间",
            subtitle="额度直接恢复 · 与发放重置卡不同",
        )
        alert = data.get("latest_alert") or {}
        if alert.get("kind") == "reset" and _date(alert.get("source_at"), tz) == last:
            url = alert.get("url")
            if isinstance(url, str) and url.startswith("https://"):
                card["links"].append(url)
    elif watch:
        _, alert = watch
        window = (
            alert.get("window")
            or (data.get("official_signal") or {}).get("window")
            or {}
        )
        if not isinstance(window, dict):
            window = {}
        target = _date(window.get("end_at") or window.get("target_at"), tz)
        card.update(
            accent="gold",
            title="重置已有预告",
            badge="等待确认",
            subtitle="公开预告 · 尚未确认完成",
            time_label="预告公告窗口截止",
            primary=target.strftime("%H:%M") if target else "时间待定",
            date=target.strftime("%Y 年 %m 月 %d 日") if target else "暂无明确公告窗口",
            note="尚未确认重置，发放方式与实际到账以公告和个人账号为准。",
        )
        url = alert.get("url")
        if isinstance(url, str) and url.startswith("https://"):
            card["links"].append(url)
        if not notification:
            card["rows"].append(
                {
                    "label": "预告信号评分",
                    "value": f"{alert['score']:g}% · 不等于 24 小时重置概率",
                }
            )
            card["rows"].append(
                {
                    "label": "预告发布时间",
                    "value": parse_time(alert["source_at"])
                    .astimezone(tz)
                    .strftime("%Y-%m-%d %H:%M"),
                }
            )
            if isinstance(alert.get("summary"), str) and alert["summary"].strip():
                card["rows"].append(
                    {"label": "预告原文", "value": alert["summary"].strip()}
                )
    elif action == "forecast":
        card.update(
            title="下一次重置预测",
            subtitle="网站预测 · 不保证发生时间",
            time_label="下次确切时间",
            primary="暂无明确预告",
            date="等待可确认的公告窗口",
        )

    if not notification:
        rows = card["rows"]
        if action != "last":
            for hours in (24, 48):
                value = data["probabilities"].get(f"rounded_{hours}h")
                card["probabilities"].append(
                    {
                        "label": f"{hours} 小时内",
                        "value": value,
                        "display": f"{value:g}%" if value is not None else "暂无数据",
                    }
                )
            rows.append(
                {
                    "label": "预测计算起点",
                    "value": f"网站更新时刻 {card['updated']} {zone}",
                }
            )
            confidence = {"low": "低", "medium": "中", "high": "高"}.get(
                data.get("confidence"), "未知"
            )
            rows.append({"label": "预测置信度", "value": confidence})
            if not watch:
                signal = data.get("official_signal") or {}
                window = signal.get("window") or signal
                target = (
                    _date(window.get("target_at") or window.get("end_at"), tz)
                    if isinstance(window, dict)
                    else None
                )
                if target:
                    suffix = (
                        " · 窗口已过，尚不能认定已重置"
                        if target < datetime.now(tz)
                        else " · 不代表个人到账时刻"
                    )
                    rows.append(
                        {
                            "label": "公告窗口截止",
                            "value": target.strftime("%Y-%m-%d %H:%M") + suffix,
                        }
                    )
            median = (data.get("cadence") or {}).get("recent_median_days")
            if (
                last
                and isinstance(median, (int, float))
                and not isinstance(median, bool)
                and math.isfinite(median)
                and 0 < median <= 365
            ):
                estimate = last + timedelta(days=median)
                suffix = (
                    "；时间已过不代表即将重置" if estimate < datetime.now(tz) else ""
                )
                rows.append(
                    {
                        "label": "历史间隔参考",
                        "value": f"近期中位间隔 {median:g} 天，推算 {estimate:%Y-%m-%d %H:%M}，仅供参考{suffix}",
                    }
                )
            peak = data.get("time_window") or {}
            start, end = peak.get("start_hour"), peak.get("end_hour")
            if peak.get("timezone") == "UTC" and all(
                isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 23
                for h in (start, end)
            ):
                anchor = datetime(2000, 1, 1, tzinfo=timezone.utc)
                a = (anchor + timedelta(hours=start)).astimezone(tz)
                b = (
                    anchor + timedelta(hours=end if end > start else end + 24)
                ).astimezone(tz)
                across = "次日 " if b.date() > a.date() else ""
                rows.append(
                    {
                        "label": "历史常见时段",
                        "value": f"{a:%H:%M}—{across}{b:%H:%M} {zone}",
                    }
                )
        if last:
            elapsed = max(0, int((datetime.now(tz) - last).total_seconds()) // 3600)
            rows.append(
                {
                    "label": "上次直接重置",
                    "value": f"{last:%Y-%m-%d %H:%M} · 距今 {elapsed // 24} 天 {elapsed % 24} 小时",
                }
            )
        if banked and banked.latest:
            event = banked.latest
            stale = " · 旧记录，当前状态未核实" if not banked.is_fresh() else ""
            rows.append(
                {
                    "label": "最近重置卡记录",
                    "value": f"{BANKED_LABELS[event.state]} · {event.at.astimezone(tz):%Y-%m-%d %H:%M}{stale}。发卡不等于直接恢复额度。",
                }
            )
            if event.url:
                card["links"].append(event.url)

    if banked_notice and banked and banked.latest:
        event = banked.latest
        local = event.at.astimezone(tz)
        card.update(
            accent="blue",
            icon="ticket",
            title=BANKED_LABELS[event.state],
            subtitle="重置卡发放 · 不是额度直接重置",
            badge={
                "announced": "已公告，待到账",
                "arriving": "发放进行中",
                "available": "网站已观测到",
                "unknown": "待确认",
            }[event.state],
            time_label="发放记录时间",
            primary=local.strftime("%H:%M"),
            date=local.strftime("%Y 年 %m 月 %d 日"),
            note="发卡不等于直接恢复额度，请在 Codex 中确认重置卡与使用方式。",
            updated=banked.updated_at.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
        )
        card["links"] = [SOURCE] + ([event.url] if event.url else [])
    if warning:
        card.update(badge="旧缓存 · 仅供参考", accent="gold")
    return card


def banked_text(snapshot: BankedSnapshot, tz: timezone) -> str:
    if not snapshot.latest:
        return "重置卡：网站暂无可识别的发放记录。"
    event = snapshot.latest
    stale = "（旧记录，当前状态未核实）" if not snapshot.is_fresh() else ""
    return (
        f"重置卡：{BANKED_LABELS[event.state]}{stale}\n"
        f"发放记录：{event.at.astimezone(tz):%Y-%m-%d %H:%M %Z}\n"
        "发卡不等于直接恢复额度，请在 Codex 中确认重置卡与使用方式。\n"
        + (f"公告：{event.url}\n" if event.url else "")
        + f"来源：{SOURCE}"
    )


def build_test_cards(tz: timezone, kind: str = "all") -> list[dict]:
    """Build unmistakable synthetic samples without fetching or storing live data."""
    if kind not in {"all", "direct", "ticket", "watch"}:
        raise ValueError("Unknown test card type")
    now = datetime.now(timezone.utc)
    base = {
        "updated_at": now.isoformat(),
        "last_reset_at": (now - timedelta(minutes=5)).isoformat(),
        "probabilities": {"rounded_24h": None, "rounded_48h": None},
    }
    direct = Forecast.from_payload(base)
    watch = Forecast.from_payload(
        {
            **base,
            "last_reset_at": (now - timedelta(days=2)).isoformat(),
            "alert_event_id": "demo:watch",
            "latest_alert": {
                "kind": "watch",
                "state": "active",
                "score": 93,
                "source_at": (now - timedelta(minutes=1)).isoformat(),
                "window": {"end_at": (now + timedelta(hours=4)).isoformat()},
            },
        }
    )
    ticket = BankedSnapshot.from_payload(
        {
            "updated_at": now.isoformat(),
            "events": [
                {
                    "id": "demo:ticket",
                    "group": "credits",
                    "type": "credits",
                    "banked_state": "available",
                    "announced_at": (now - timedelta(minutes=3)).isoformat(),
                }
            ],
        }
    )
    factories = {
        "direct": lambda: build_card(direct, tz, "last", notification=True),
        "ticket": lambda: build_card(
            None, tz, notification=True, banked=ticket, banked_notice=True
        ),
        "watch": lambda: build_card(watch, tz, "watch", notification=True),
    }
    cards = []
    for selected in factories if kind == "all" else (kind,):
        card = factories[selected]()
        card.update(
            test=True,
            test_banner="管理员测试 · 示例数据，不是真实重置通知",
            badge="测试示例",
        )
        cards.append(card)
    return cards
