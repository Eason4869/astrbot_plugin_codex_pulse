"""Banked-reset announcements are independent of direct quota resets."""

from dataclasses import dataclass
from datetime import datetime, timezone

from .forecast import parse_time


@dataclass(frozen=True)
class BankedEvent:
    id: str
    at: datetime
    state: str
    url: str | None

    @property
    def receipt(self) -> dict:
        return {"id": self.id, "at": self.at.isoformat(), "state": self.state}

    def follows(self, receipt: dict) -> bool:
        """Never replay older grants or downgrade a known delivery stage."""
        previous = parse_time(receipt["at"])
        if self.at != previous:
            return self.at > previous
        ranks = {"unknown": 0, "announced": 1, "arriving": 2, "available": 3}
        return self.id == receipt["id"] and ranks[self.state] > ranks[receipt["state"]]


@dataclass(frozen=True)
class BankedSnapshot:
    updated_at: datetime
    latest: BankedEvent | None
    stale: bool = False

    @classmethod
    def from_payload(cls, payload: dict) -> "BankedSnapshot":
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise ValueError("Missing timeline events")
        updated = parse_time(payload.get("updated_at"))
        events = []
        for item in payload["events"]:
            if not isinstance(item, dict):
                continue
            state = item.get("banked_state")
            if (
                item.get("group") != "credits"
                or item.get("type") != "credits"
                or not isinstance(state, str)
                or state not in {"announced", "arriving", "available", "unknown"}
                or not isinstance(item.get("id"), str)
                or not item["id"].strip()
                or item.get("preview") is True
            ):
                continue
            try:
                at = parse_time(item.get("announced_at"))
            except ValueError:
                continue
            if at > updated:
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.startswith("https://"):
                url = None
            events.append(BankedEvent(item["id"], at, state, url))
        return cls(
            updated,
            max(events, key=lambda event: event.at, default=None),
            bool(payload.get("stale", False)),
        )

    def is_fresh(self) -> bool:
        age = (datetime.now(timezone.utc) - self.updated_at).total_seconds()
        return not self.stale and -300 <= age <= 1800

    def notifiable(self) -> BankedEvent | None:
        if self.is_fresh() and self.latest and self.latest.state != "unknown":
            return self.latest
        return None
