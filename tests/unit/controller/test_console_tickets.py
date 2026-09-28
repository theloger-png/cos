"""Unit tests for controller/console_tickets.py."""

from __future__ import annotations

import uuid

from controller import console_tickets
from controller.console_tickets import consume_ticket, issue_ticket


class _FakeClock:
    """A monotonic clock test double that advances only when told to.

    Avoids coupling tests to exactly how many time.monotonic() calls each
    operation makes internally (issue_ticket alone makes two: one via
    _purge_expired, one for the new ticket's own expiry).
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class TestIssueAndConsumeTicket:
    def setup_method(self) -> None:
        # The ticket store is a module-level dict; keep tests isolated.
        console_tickets._tickets.clear()

    def test_valid_ticket_resolves_to_issued_data(self):
        vm_id = uuid.uuid4()
        token = issue_ticket(vm_id=vm_id, node_ip="10.0.0.5", libvirt_uuid="dom-1")

        ticket = consume_ticket(token, vm_id)

        assert ticket is not None
        assert ticket.vm_id == vm_id
        assert ticket.node_ip == "10.0.0.5"
        assert ticket.libvirt_uuid == "dom-1"

    def test_ticket_is_single_use(self):
        vm_id = uuid.uuid4()
        token = issue_ticket(vm_id=vm_id, node_ip="10.0.0.5", libvirt_uuid="dom-1")

        first = consume_ticket(token, vm_id)
        second = consume_ticket(token, vm_id)

        assert first is not None
        assert second is None

    def test_wrong_vm_id_returns_none(self):
        vm_id = uuid.uuid4()
        other_vm_id = uuid.uuid4()
        token = issue_ticket(vm_id=vm_id, node_ip="10.0.0.5", libvirt_uuid="dom-1")

        ticket = consume_ticket(token, other_vm_id)

        assert ticket is None

    def test_wrong_vm_id_still_consumes_the_ticket(self):
        """A guessed/leaked token can't be retried with the correct vm_id either."""
        vm_id = uuid.uuid4()
        other_vm_id = uuid.uuid4()
        token = issue_ticket(vm_id=vm_id, node_ip="10.0.0.5", libvirt_uuid="dom-1")

        consume_ticket(token, other_vm_id)
        retry = consume_ticket(token, vm_id)

        assert retry is None

    def test_unknown_token_returns_none(self):
        assert consume_ticket("does-not-exist", uuid.uuid4()) is None

    def test_expired_ticket_returns_none(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr(console_tickets.time, "monotonic", clock)

        vm_id = uuid.uuid4()
        token = issue_ticket(vm_id=vm_id, node_ip="10.0.0.5", libvirt_uuid="dom-1")
        clock.now += console_tickets.TICKET_TTL_SECONDS + 1

        assert consume_ticket(token, vm_id) is None

    def test_expired_ticket_is_removed_from_the_store(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr(console_tickets.time, "monotonic", clock)

        vm_id = uuid.uuid4()
        token = issue_ticket(vm_id=vm_id, node_ip="10.0.0.5", libvirt_uuid="dom-1")
        clock.now += console_tickets.TICKET_TTL_SECONDS + 1
        consume_ticket(token, vm_id)

        assert token not in console_tickets._tickets

    def test_ticket_still_valid_just_before_expiry(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr(console_tickets.time, "monotonic", clock)

        vm_id = uuid.uuid4()
        token = issue_ticket(vm_id=vm_id, node_ip="10.0.0.5", libvirt_uuid="dom-1")
        clock.now += console_tickets.TICKET_TTL_SECONDS - 1

        assert consume_ticket(token, vm_id) is not None

    def test_tickets_for_different_vms_are_independent(self):
        vm_a, vm_b = uuid.uuid4(), uuid.uuid4()
        token_a = issue_ticket(vm_id=vm_a, node_ip="10.0.0.1", libvirt_uuid="dom-a")
        token_b = issue_ticket(vm_id=vm_b, node_ip="10.0.0.2", libvirt_uuid="dom-b")

        ticket_a = consume_ticket(token_a, vm_a)
        ticket_b = consume_ticket(token_b, vm_b)

        assert ticket_a.libvirt_uuid == "dom-a"
        assert ticket_b.libvirt_uuid == "dom-b"

    def test_tokens_are_unique_and_url_safe(self):
        vm_id = uuid.uuid4()
        tokens = {issue_ticket(vm_id=vm_id, node_ip="10.0.0.1", libvirt_uuid="d") for _ in range(20)}
        assert len(tokens) == 20
        for tok in tokens:
            assert all(c.isalnum() or c in "-_" for c in tok)


class TestPurgeExpired:
    def setup_method(self) -> None:
        console_tickets._tickets.clear()

    def test_issuing_a_new_ticket_purges_expired_ones(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr(console_tickets.time, "monotonic", clock)

        vm_id = uuid.uuid4()
        stale_token = issue_ticket(vm_id=vm_id, node_ip="10.0.0.1", libvirt_uuid="stale")
        assert stale_token in console_tickets._tickets

        clock.now += console_tickets.TICKET_TTL_SECONDS + 1
        issue_ticket(vm_id=vm_id, node_ip="10.0.0.1", libvirt_uuid="fresh")

        assert stale_token not in console_tickets._tickets
