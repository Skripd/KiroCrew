"""Who stopped a worker from the Workers tab, until that worker's next turn starts.

:mod:`..worker_stop` notes a stop it carried out; the board's one read path
(:mod:`.source`) asks :func:`current` for each worker row, so the board and the
Workers tab both show "Stopped by @user · HH:MM" from the same snapshot, through
the same refresh. The note lives in this process only and holds while the
worker's slot is still on the turn generation that was stopped: the next turn
(a new generation) clears it, and so does the slot going away.

Only a Slack user id (the clicker's, from the verified payload) and a clock time
are kept: never a session key, a command or any transcript text.
"""

from __future__ import annotations

import dataclasses
import re
import time
from dataclasses import dataclass
from typing import Any, TypeVar

_S = TypeVar("_S")

#: A Slack user id: the only form that becomes a mention.
_USER_ID = re.compile(r"^[UW][A-Z0-9]{2,30}$")
_MAX_TRACKED = 4096


@dataclass(frozen=True)
class Stop:
    user: str
    #: ``HH:MM`` UTC, when the stop was carried out.
    at: str
    #: The worker slot's turn generation that was stopped.
    generation: int


#: ``worker slot key -> Stop``.
_stops: dict[str, Stop] = {}
#: Worker slot keys whose stop has not been published yet: the next board and
#: Workers-tab publish skip their edit gap once, so it shows within seconds.
_urgent: set[str] = set()


def take_urgent(worker_keys: Any) -> bool:
    """Whether any of *worker_keys* has an unpublished stop; consumes those marks."""
    hit = {str(k) for k in worker_keys or () if k} & _urgent
    _urgent.difference_update(hit)
    return bool(hit)


def generation_of(slot: Any) -> int:
    """*slot*'s turn generation now: read it BEFORE awaiting the stop."""
    value = getattr(slot, "_turn_generation", 0)
    return value if isinstance(value, int) else 0


def note(slot: Any, user: str, *, generation: int) -> None:
    """Record that *user* stopped *slot*'s turn *generation* (a well-formed user id only).

    *generation* is the turn the stop was aimed at, read before any await: the
    slot may already be on its next (queued) turn by the time the stop returns,
    and that turn was stopped by nobody. A note for a generation the slot has
    moved past is not kept.
    """
    key = str(getattr(slot, "key", "") or "")
    if not key or not _USER_ID.match(user or ""):
        return
    if generation_of(slot) != generation:
        # Moved on: nothing to note. A note for a LATER turn (another Stop that
        # landed meanwhile) is that turn's and stays.
        stale = _stops.get(key)
        if stale is not None and stale.generation == generation:
            _stops.pop(key, None)
        return
    if len(_stops) > _MAX_TRACKED:
        _stops.clear()
        _urgent.clear()
    _stops[key] = Stop(user=user, at=time.strftime("%H:%M", time.gmtime()), generation=generation)
    _urgent.add(key)


def current(state: Any, worker_key: str) -> Stop | None:
    """The stop still showing for *worker_key*, or ``None`` (and forgotten) once its
    slot started a new turn or is gone."""
    from kiro_crew.slack.code_channels.board.source import find_slot

    slot = find_slot(state, worker_key) if worker_key else None
    key = str(getattr(slot, "key", "") or "")
    stop = _stops.get(key) if key else None
    if stop is None:
        return None
    if generation_of(slot) != stop.generation:
        _stops.pop(key, None)  # the next turn started: the stop is history
        _urgent.discard(key)  # and its unpublished mark grants no edit-gap skip
        return None
    return stop


def reconcile(snapshot: _S, state: Any = None) -> _S:
    """*snapshot* with every "Stopped by" overlay its worker has moved past removed.

    A snapshot is read before the awaits of a publish, and a queued turn can start
    in between; that turn was stopped by nobody. Synchronous: called right before
    each render (and before an urgent mark is taken), after the publish's awaits.
    """
    rows = tuple(getattr(snapshot, "rows", ()) or ())
    if not any(getattr(r, "stopped_by", "") for r in rows):
        return snapshot
    if state is None:
        from kiro_crew.slack.code_channels import surface

        state = surface.dashboard_state()
    kept = []
    changed = False
    for row in rows:
        if row.stopped_by and (state is None or current(state, row.session_key) is None):
            row = dataclasses.replace(row, stopped_by="", stopped_at="")
            changed = True
        kept.append(row)
    if not changed:
        return snapshot
    return dataclasses.replace(snapshot, rows=tuple(kept))  # type: ignore[type-var]


def reset_for_tests() -> None:
    _stops.clear()
    _urgent.clear()
