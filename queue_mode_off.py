"""Inert stand-in for ``cloud.queue_mode`` (fork addition, MIT).

Upstream's app.py imports the free-queue saturation policy from the optional
``cloud/`` package even with billing off. ``cloud/`` is not under the MIT
license, so this fork ships without it, and app.py falls back to this module:
the same names, with saturation permanently off. Every job then queues in
plain priority order (FIFO for a self-hosted install), exactly as upstream
behaves with billing disabled.
"""

NORMAL = "normal"
SLOW = "slow"
LANES = (NORMAL, SLOW)
PAID_PRIORITY = 1
FREE_PRIORITY = 2
SLOW_PRIORITY = 3


def enabled() -> bool:
    return False


def killed() -> bool:
    return True


def slow_countries() -> frozenset:
    return frozenset()


def slow_lane_on() -> bool:
    return False


def limits() -> dict:
    return {"recent_on": 0.0, "recent_off": 0.0, "brake_on": 0.0, "brake_off": 0.0,
            "brake_clips": 3}


def free_clip_cap(now=None) -> int:
    return 0


def request_country(headers, client_ip=None) -> str:
    return ""


def lane_for(plan, country) -> str:
    return NORMAL


def priority_for(plan_priority: int, lane: str) -> int:
    return int(plan_priority)


def lane_of_priority(priority) -> str:
    return SLOW if int(priority) >= SLOW_PRIORITY else NORMAL


def effective_priority(priority) -> int:
    return priority


def register_on_change(callback) -> None:
    """Nothing ever changes, so the callback is never called."""


def recent_first(lane) -> bool:
    return False


def brake_active() -> bool:
    return False


def enabled_since():
    return None


def forget_activation(state_dir) -> None:
    pass


def note_rejected() -> None:
    pass
