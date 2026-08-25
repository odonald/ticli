"""Cross-process request throttle for the agent surface.

Exists because the working rules' rate limits lived in a Markdown file and an
agent proved that documentation is not enforcement: it fired ~30 requests in
seconds while building a playlist, unaware the rule existed (2026-08-25). The
agent before it got the owner's IP blocked the same way. This module moves the
brake from agent discipline into code, where it cannot be skipped by not
having read it.

Two mechanisms, one file:

- **Spacing.** Every request an agent makes reserves a slot: under an `flock`
  on the state file, read `next_free_at`, claim `max(now, next_free_at)` as
  this request's start, write the claim + interval back, release, and sleep
  until the claimed time. Reservation-then-sleep rather than sleep-under-lock,
  so the lock is never held across a wait or a network call, and N concurrent
  processes serialize into N spaced slots instead of stampeding when the
  first one finishes.

- **The trip.** A 429, or a 401 carrying TIDAL's subStatus 4006 ("Session
  does not have streaming privileges" — the bot-detection escalation), writes
  a tripped record. From then on every agent request fails fast with a
  structured error, because the working rule is *stop entirely and report* —
  retries extend these blocks. Nothing clears it automatically: a human runs
  `ticli agent unblock` after deciding it is safe, since the cost of a wrong
  guess is the owner's music stopping, not a failed request.

The state file lives in the same directory as the instance lock and the
player state. Like `DOWNLOAD_ROOT` and `player.STATE_DIR`, the directory is
read at call time so the test suite's rail can redirect it.
"""

import fcntl
import json
import os
import time
from pathlib import Path

# Same directory player.py calls STATE_DIR. Not imported from there — player's
# import chain is the whole TUI, and `ticli agent --help` must stay instant.
# Kept in step by a test rather than an import, the same way cli.py's
# QUALITY_NAMES are.
STATE_DIR = Path.home() / ".config" / "ticli"

# The TUI floors interactive fetches at 1.0s with a human on the keys
# (SEARCH_FETCH_MIN_INTERVAL). Agents are unattended and usually looping, so
# the floor is doubled. This is spacing for a handful of calls, not a budget
# for bulk work — an agent that needs hundreds of requests should be told no
# by design, not throttled into taking ten minutes.
MIN_INTERVAL_SECONDS = 2.0


def _throttle_path() -> Path:
    """Derived at call time rather than bound at import, so redirecting
    STATE_DIR redirects this with it — same reason as _instance_lock_path."""
    return STATE_DIR / "agent-throttle.json"


class Tripped(Exception):
    """The stop is in force. Carries the record so callers can report it."""

    def __init__(self, record: dict):
        self.record = record
        super().__init__(record.get("reason", "tripped"))


def _read_state(fd) -> dict:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 65536)
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(fd, state: dict) -> None:
    payload = json.dumps(state).encode()
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, payload)


def _locked_state():
    """Open the state file and take its flock. Caller must close the fd —
    closing is what releases the lock. The whole read-modify-write cycle
    happens under one hold, per the multi-writer-JSON rule: the stale read is
    what loses the other writer's update."""
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(_throttle_path(), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def acquire(now=time.time, sleep=time.sleep) -> None:
    """Block until this process may make one request, or raise Tripped.

    `now` and `sleep` are injectable for tests — the suite asserts the
    reservation arithmetic without waiting out real intervals.
    """
    fd = _locked_state()
    try:
        state = _read_state(fd)
        if state.get("tripped"):
            raise Tripped(state["tripped"])
        current = now()
        start = max(current, float(state.get("next_free_at", 0)))
        state["next_free_at"] = start + MIN_INTERVAL_SECONDS
        _write_state(fd, state)
    finally:
        os.close(fd)
    wait = start - current
    if wait > 0:
        sleep(wait)


def trip(reason: str, detail: str = "", now=time.time) -> dict:
    """Record the stop. Returns the record written, for the caller's report."""
    record = {"reason": reason, "detail": detail, "at": now()}
    fd = _locked_state()
    try:
        state = _read_state(fd)
        # First trip wins: a second failure racing in must not overwrite the
        # original evidence with a later, blurrier symptom.
        if not state.get("tripped"):
            state["tripped"] = record
            _write_state(fd, state)
        else:
            record = state["tripped"]
    finally:
        os.close(fd)
    return record


def tripped() -> dict | None:
    """The trip record if the stop is in force, else None. Read-only."""
    if not _throttle_path().exists():
        return None
    fd = _locked_state()
    try:
        return _read_state(fd).get("tripped")
    finally:
        os.close(fd)


def unblock() -> bool:
    """Clear the trip. Returns whether one was in force. A human's command —
    nothing in this module calls it."""
    if not _throttle_path().exists():
        return False
    fd = _locked_state()
    try:
        state = _read_state(fd)
        was = bool(state.get("tripped"))
        state["tripped"] = None
        _write_state(fd, state)
        return was
    finally:
        os.close(fd)
