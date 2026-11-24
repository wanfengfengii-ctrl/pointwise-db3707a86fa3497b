"""Deterministic link for exercising station calls without opening the other database."""
from collections import deque
from collections.abc import Callable
import json


Message = dict[str, object]


class ReplyUnavailable(ConnectionError):
    """No reply was delivered to the caller."""


def _wire_copy(message: Message) -> Message:
    if not isinstance(message, dict):
        raise TypeError("station messages must be JSON objects")
    return json.loads(json.dumps(message, allow_nan=False))


class TrialLink:
    """Deliver JSON copies and optionally intercept one matching call."""

    def __init__(self, handler: Callable[[Message], Message]):
        self._handler = handler
        self._held: deque[Message] = deque()
        self._armed: tuple[str, Callable[[Message], bool] | None] | None = None

    def arm(self, action: str, matches: Callable[[Message], bool] | None = None) -> None:
        if action not in ("hold", "lose_reply", "unavailable") or self._armed is not None:
            raise ValueError("expected one new link action")
        self._armed = (action, matches)

    @property
    def held_count(self) -> int:
        return len(self._held)

    def __call__(self, message: Message) -> Message:
        wire = _wire_copy(message)
        action = None
        if self._armed is not None:
            selected, matches = self._armed
            if matches is None or matches(wire):
                self._armed = None
                action = selected
        if action == "hold":
            self._held.append(wire)
            raise ReplyUnavailable("request held for later delivery")
        if action == "unavailable":
            raise ReplyUnavailable("request did not reach the receiver")
        reply = self._receive(wire)
        if action == "lose_reply":
            raise ReplyUnavailable("receiver returned but the reply was lost")
        return reply

    def _receive(self, wire: Message) -> Message:
        return _wire_copy(self._handler(wire))

    def deliver_held(self) -> Message:
        """Deliver the oldest delayed request and show its reply to the test driver."""
        return self._receive(self._held.popleft())


def check_link() -> None:
    seen = []

    def echo(message: Message) -> Message:
        seen.append(message["value"])
        return {"seen": message["value"]}

    def no_reply(message: Message) -> None:
        try:
            link(message)
        except ReplyUnavailable:
            return
        raise AssertionError("expected an unavailable reply")

    link = TrialLink(echo)
    link.arm("hold", lambda msg: msg["value"] == 2)
    assert link({"value": 1}) == {"seen": 1}
    delayed = {"value": 2}
    no_reply(delayed)
    delayed["value"] = 9
    assert link.held_count == 1 and seen == [1]
    link.arm("lose_reply")
    no_reply({"value": 3})
    link.arm("unavailable")
    no_reply({"value": 4})
    assert seen == [1, 3]
    assert link.deliver_held() == {"seen": 2}
    assert seen == [1, 3, 2] and link.held_count == 0
    print("TRIAL_LINK received=[1,3,2] held=0")


if __name__ == "__main__":
    check_link()
