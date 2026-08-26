"""Unit tests for PairingService: persistent-link pairing (zcode model).

Semantics (user-directed 2026-08-24): scan once; the saved link keeps working
on every open while the desktop app runs. Link survives restarts (disk),
sessions slide (active use never expires), only Revoke unpairs.
"""
from __future__ import annotations

import json

from sidecar.service import PairingService


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_issue_validate():
    clock = FakeClock()
    svc = PairingService(ttl_seconds=90, clock=clock)
    token = svc.issue()
    assert svc.validate(token)
    assert not svc.validate("bogus")
    assert not svc.validate(None)


def test_link_never_expires():
    """THE fix: no countdown on the saved link — works days later."""
    clock = FakeClock()
    svc = PairingService(ttl_seconds=90, session_ttl_seconds=1800, clock=clock)
    token = svc.issue()
    clock.now = 90.1      # old TTL boundary
    assert svc.check(token)
    clock.now = 3600 * 24 * 7  # a week later
    assert svc.check(token)
    assert svc.mint_session_token(token) is not None


def test_exchange_is_reusable():
    """Every open of the saved link mints a fresh session — no burn."""
    clock = FakeClock()
    svc = PairingService(ttl_seconds=90, clock=clock)
    token = svc.issue()
    s1 = svc.mint_session_token(token)
    s2 = svc.mint_session_token(token)
    assert s1 and s2 and s1 != s2
    # both sessions work
    assert svc.validate(s1) and svc.validate(s2)


def test_session_sliding_expiry():
    """Active use slides the window; abandoned sessions age out."""
    clock = FakeClock()
    svc = PairingService(ttl_seconds=90, session_ttl_seconds=1800, clock=clock)
    session = svc.mint_session_token(svc.issue())
    for _ in range(200):
        clock.now += 60  # 1 min of use per minute: never expires
        assert svc.validate(session)
    clock.now += 1800.1  # then abandoned 30 min
    assert not svc.validate(session)


def test_issue_is_idempotent_and_keeps_sessions():
    """Start/Refresh QR never kicks a working phone off."""
    clock = FakeClock()
    svc = PairingService(ttl_seconds=90, clock=clock)
    first = svc.issue()
    session = svc.mint_session_token(first)
    second = svc.issue()
    assert second == first          # same link back
    assert svc.validate(session)    # phone still paired
    assert svc.validate(first)      # link still valid


def test_restart_restores_same_link(tmp_path):
    """App restart: pane shows the SAME QR; saved links keep working."""
    clock = FakeClock()
    state = tmp_path / "link_state.json"
    svc = PairingService(ttl_seconds=90, clock=clock, persist_path=state)
    token = svc.issue()
    assert state.exists()
    svc2 = PairingService(ttl_seconds=90, clock=clock, persist_path=state)
    assert svc2.issue() == token            # same URL re-shown
    assert svc2.linked
    s = svc2.mint_session_token(token)      # phone's saved link still valid
    assert s is not None and svc2.validate(s)


def test_revoke_unpairs_everything_including_disk(tmp_path):
    clock = FakeClock()
    state = tmp_path / "link_state.json"
    svc = PairingService(ttl_seconds=90, clock=clock, persist_path=state)
    token = svc.issue()
    session = svc.mint_session_token(token)
    assert svc.revoke_all() >= 2
    assert not state.exists()
    assert not svc.validate(token)
    assert not svc.validate(session)
    fresh = svc.issue()                      # next Start mints a NEW link
    assert fresh != token


def test_corrupt_state_file_is_tolerated(tmp_path):
    state = tmp_path / "link_state.json"
    state.write_text("not json{", encoding="utf-8")
    svc = PairingService(clock=FakeClock(), persist_path=state)
    assert not svc.linked
    tok = svc.issue()                        # recovers by minting fresh
    assert svc.validate(tok)


def test_session_table_bounded():
    svc = PairingService(clock=FakeClock())
    svc.issue()
    sessions = [svc.mint_session_token(svc.link_token) for _ in range(10)]
    live = [s for s in sessions if s and svc.check(s)]
    assert len(live) <= 4


def test_revoke_all_legacy():
    clock = FakeClock()
    svc = PairingService(ttl_seconds=90, clock=clock)
    t1 = svc.issue()
    t2 = svc.issue()
    assert t1 == t2  # idempotent now — same persistent link
    assert svc.revoke_all() >= 1
    assert not svc.validate(t2)


def test_tokens_are_unique_and_random():
    a, b = PairingService().issue(), PairingService().issue()
    assert a != b and len(a) >= 32


def test_ttl_property():
    assert PairingService(ttl_seconds=42).ttl_seconds == 42


def test_status_transitions():
    """Icon-state contract: first access pairs; revoke returns to unpaired."""
    clock = FakeClock()
    svc = PairingService(ttl_seconds=90, clock=clock)
    assert svc.status()["paired"] is False
    token = svc.issue()
    assert svc.status()["paired"] is False   # issued but nobody connected yet
    assert svc.validate(token)
    status = svc.status()
    assert status["paired"] is True
    assert status["connected_at"] == 0.0
    clock.now = 5.0
    assert svc.validate(token)
    assert svc.status()["last_active"] == 5.0
    svc.revoke_all()
    assert svc.status()["paired"] is False
    assert not svc.validate(token)


def test_persisted_payload_shape(tmp_path):
    state = tmp_path / "link_state.json"
    PairingService(clock=FakeClock(), persist_path=state).issue()
    data = json.loads(state.read_text(encoding="utf-8"))
    assert set(data) == {"link", "created"}
