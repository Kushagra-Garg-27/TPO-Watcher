"""Deterministic worker deadline, custody wait, key expiry, host death, and physical acceptance diagnostics.

Characterizes:
A. Outer request deadline exhaustion (both frame receive timeout and relay dispatch exhaustion).
B. Custody wait deadline exhaustion (custody queue timeout poisons custody lane).
C. Key-session expiry and inter-quantum refresh boundaries.
D. Controller-host death, successor generation fence, and durable fact preservation.
E. Physical acceptance uncertainty across all four delivery phases (no send, started, accepted-without-end, end).
F. Worker request wall-clock headroom and timing characterization.
"""
import asyncio
import json
import os
import secrets
import sys
import time
from contextlib import contextmanager

import pytest
import isolation_bootstrap as isolation
from test_notification_migration_privacy import encrypted, NOW
from test_cummins_end_to_end_v2 import record, Clock
from app.database.notification_migrator import create_schema
from app.database.event_repository import EventRepository, Fenced, PolicyDenied
from app.database.key_provider import KeyUnavailable
from app.notifications.transport_arbiter import get_state, put_state


@contextmanager
def hosted(x, clock, *, enable=True, custody_timeout=0.5):
    from app.monitoring.runtime_supervisor import ChildKeyRelay
    create_schema(x['c'], now_ms=NOW)
    repo = EventRepository(x['c'], clock=clock, record_mode=True)
    with isolation.start_test_child('controller-host-crash-test') as host:
        relay = ChildKeyRelay(
            repo, x['codec'], None, controller_host=host,
            provider_identity='fixture-custody', custody_generation=1,
            timeout=custody_timeout
        )
        try:
            relay.open(host)
            elected = relay.request(host, 'elect_controller', {})
            relay.accept_controller_host(host)
            if enable:
                relay.request(host, 'enable_controller', {})
                relay.refresh(host)
            yield repo, host, relay, elected
        finally:
            try:
                relay.close_child(host)
            except BaseException:
                pass
            finally:
                relay.close()


async def prepare_subscriber_delivery(x, clock):
    from test_notification_runtime_supervision import running
    with running(x) as (runtime, feed, old_authority, _):
        await runtime.poll_once()
        assert await runtime.drain_once() == 1
        runtime.repo.add_user(branch='VIT_CE')
        feed.records = [record(id='runtime-new', maxPackage='29000')]
        await runtime.poll_once()
        runtime.pipeline.fanout_once()


# ---------------------------------------------------------------------------
# Diagnostic A: Outer Request Deadline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d01_outer_request_deadline_exhaustion_in_dispatch(encrypted):
    """A. Outer request deadline: request timeout classifies as TimeoutError; dispatch deadline exhaustion marks session dead."""
    x = encrypted
    clock = Clock()
    clock.now = NOW
    await prepare_subscriber_delivery(x, clock)

    with isolation.start_test_child('authority') as authority, isolation.start_test_child('client') as client:
        with hosted(x, clock) as (repo, host, relay, elected):
            relay.open(authority)
            relay.request(authority, 'elect', {})
            relay.open(client)
            relay.request(authority, 'configure', dict(kind='UNKNOWN', ended=False, fault=None))

            # 1. Outer request timeout trips fail-closed, classified as TimeoutError
            start_t = time.monotonic()
            with pytest.raises(TimeoutError, match='guarded child response timeout'):
                relay.request(client, 'worker', {'channel': 'SUBSCRIBER'}, transport=authority, timeout=0.04)
            elapsed = time.monotonic() - start_t
            assert elapsed < 1.0

            # Safe SQLite state: delivery 2 was not marked SENT
            sent_count = x['c'].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE delivery_id=2 AND status='SENT'").fetchone()[0]
            assert sent_count == 0

            # 2. Dispatch deadline exhaustion directly: when monotonic deadline is in the past, marks session dead
            client_session = relay.sessions[client.ready['identity']]
            client_session['deadline'] = time.monotonic() - 1.0  # Expired monotonic deadline
            with pytest.raises(KeyUnavailable):
                relay._rpc_on(client, 'protected-key-v1', {
                    'capability': client_session['cap'],
                    'counter': 1,
                    'operation': 'schema_required',
                    'arguments': {'namespace': x['codec'].namespace}
                }, x['c'])


def test_d02_outer_request_deadline_frame_receive_timeout(encrypted):
    """A. Outer request deadline: child frame receive times out, cleanly classifies as TimeoutError."""
    x = encrypted
    clock = Clock()
    clock.now = NOW

    with isolation.start_test_child('authority') as authority, isolation.start_test_child('client') as client:
        with hosted(x, clock) as (repo, host, relay, elected):
            relay.open(authority)
            relay.request(authority, 'elect', {})
            relay.open(client)

            start_t = time.monotonic()
            with pytest.raises(TimeoutError, match='guarded child response timeout'):
                client.request('worker', {'channel': 'SUBSCRIBER'}, timeout=0.001)
            elapsed = time.monotonic() - start_t
            assert elapsed < 1.0


# ---------------------------------------------------------------------------
# Diagnostic B: Custody Wait Deadline
# ---------------------------------------------------------------------------

def test_d03_custody_wait_deadline_exhaustion_poisons_lane(encrypted):
    """B. Custody wait deadline: custody operation timeout poisons the custody lane (fail-closed)."""
    x = encrypted
    clock = Clock()
    clock.now = NOW

    with hosted(x, clock, custody_timeout=0.04) as (repo, host, relay, elected):
        assert relay.custody.failed is False
        assert relay.custody.timeout == 0.04

        orig_unwrap = relay.original_provider.unwrap

        def slow_unwrap(*args, **kwargs):
            time.sleep(0.08)
            return orig_unwrap(*args, **kwargs)

        relay.custody.methods['unwrap'] = slow_unwrap

        with pytest.raises(KeyUnavailable, match='fictional custody outcome unresolved'):
            relay.custody.unwrap('dummy-ref', None)

        assert relay.custody.failed is True

        with pytest.raises(KeyUnavailable, match='fictional custody unavailable'):
            relay.custody.schema_required(x['codec'].namespace)

        with isolation.start_test_child('authority') as authority:
            with pytest.raises(KeyUnavailable, match='live guarded child required'):
                relay.open(authority)


# ---------------------------------------------------------------------------
# Diagnostic C: Key-Session Expiry and Refresh Boundary
# ---------------------------------------------------------------------------

def test_d04_key_session_fictional_expiry_and_refresh(encrypted):
    """C. Key-session expiry: expired capability is rejected; inter-quantum refresh works; dead session cannot be resurrected."""
    x = encrypted
    clock = Clock()
    clock.now = NOW

    with isolation.start_test_child('authority') as authority:
        with hosted(x, clock) as (repo, host, relay, elected):
            relay.open(authority)
            auth_ident = authority.ready['identity']
            session = relay.sessions[auth_ident]
            old_cap = dict(session['cap'])

            # 1. Expire capability by setting expires_ms in the past
            session['cap'] = dict(old_cap, expires_ms=clock.now - 1)

            # Expired capability presentation rejected fail-closed
            with pytest.raises(KeyUnavailable):
                relay._rpc_on(authority, 'protected-key-v1', {
                    'capability': session['cap'],
                    'counter': 1,
                    'operation': 'schema_required',
                    'arguments': {'namespace': x['codec'].namespace}
                }, x['c'])

            # 2. Inter-quantum refresh on live authority generates fresh valid capability
            session['cap'] = old_cap  # restore capability for refresh
            refreshed = relay.refresh(authority)
            new_cap = relay.sessions[auth_ident]['cap']
            assert new_cap['token'] != old_cap['token']
            assert new_cap['expires_ms'] == clock.now + relay.lifetime_ms
            assert refreshed['now'] == clock.now

            # 3. Dead session cannot be resurrected
            relay.sessions[auth_ident]['dead'] = True
            with pytest.raises(KeyUnavailable, match='live child session required'):
                relay.refresh(authority)


# ---------------------------------------------------------------------------
# Diagnostic D: Controller-Host Death
# ---------------------------------------------------------------------------

def test_d05_controller_host_death_fences_old_owner_and_preserves_facts(encrypted):
    """D. Controller-host death: terminated controller cannot regain ownership; successor generation fences old authority."""
    x = encrypted
    clock = Clock()
    clock.now = NOW

    with hosted(x, clock) as (repo, host, relay, elected):
        old_controller = relay.controller
        assert old_controller._owned()['generation'] == elected['generation']

        exit_proof = isolation.terminate_test_controller(host)
        assert exit_proof['pid'] == host.ready['pid']
        assert host.process.poll() is not None

        with pytest.raises((BrokenPipeError, OSError, EOFError)):
            relay.request(host, 'controller_status', {})

        clock.now += 150
        with pytest.raises(Fenced):
            old_controller._owned()

    with hosted(x, clock) as (successor_repo, successor_host, successor_relay, next_elected):
        assert next_elected['generation'] == elected['generation'] + 1


# ---------------------------------------------------------------------------
# Diagnostic E: Physical Acceptance Uncertainty (Four Phases)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize('phase_name,fault,expected_ended,expected_ledger_phases,expected_status', [
    ('phase1_no_send', 'after_admission', False, [], 'UNCERTAIN'),
    ('phase2_started_not_accepted', 'during_physical', False, ['START'], 'UNCERTAIN'),
    ('phase3_accepted_no_end', 'accept_then_error', False, ['START', 'ACCEPTED'], 'UNCERTAIN'),
    ('phase4_durably_completed', None, True, ['START', 'ACCEPTED', 'END'], 'SENT'),
])
async def test_d06_physical_acceptance_four_phase_distinction(
    encrypted, phase_name, fault, expected_ended, expected_ledger_phases, expected_status
):
    """E. Physical acceptance uncertainty: distinguishes no send, started, accepted-without-end, and complete."""
    x = encrypted
    clock = Clock()
    clock.now = NOW
    await prepare_subscriber_delivery(x, clock)

    with isolation.start_test_child('authority') as authority, isolation.start_test_child('client') as client:
        with hosted(x, clock) as (repo, host, relay, elected):
            relay.open(authority)
            relay.request(authority, 'elect', {})
            relay.open(client)
            relay.request(authority, 'configure', dict(kind='ACCEPTED' if fault is None else 'UNKNOWN', ended=expected_ended, fault=fault))

            res = relay.request(client, 'worker', {'channel': 'SUBSCRIBER'}, transport=authority)
            if fault is None:
                assert res['sent'] == 1
            else:
                assert res['sent'] == 0

            # Delivery 2 status
            d_status = x['c'].execute("SELECT status FROM notification_deliveries_v2 WHERE delivery_id=2").fetchone()[0]
            assert d_status == expected_status

            # Get invocation for delivery 2
            inv_row = x['c'].execute("SELECT invocation_id FROM transport_invocations WHERE delivery_id=2").fetchone()
            invocation = inv_row[0] if inv_row else None

            ledger_path = x['path'].with_suffix('.fake-calls.jsonl')
            if ledger_path.exists() and invocation:
                lines = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
                observed_phases = [l['phase'] for l in lines if l.get('invocation') == invocation]
            else:
                observed_phases = []

            for p in expected_ledger_phases:
                assert p in observed_phases, f"Expected phase {p} for invocation {invocation}: {observed_phases}"

            if not expected_ended:
                assert 'END' not in observed_phases, "Never fabricate a positive END on incomplete physical call"

            if expected_status == 'UNCERTAIN':
                facts = {r[0] for r in x['c'].execute("SELECT fact_kind FROM transport_facts WHERE invocation_id=?", (invocation,)).fetchall()}
                assert 'UNKNOWN' in facts or 'PROCESS_EXIT_CONFIRMED' in facts
                # Never automatically resend an UNKNOWN or UNCERTAIN delivery
                assert x['c'].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE delivery_id=2 AND status='PENDING'").fetchone()[0] == 0
                if authority.process.poll() is None:
                    res2 = relay.request(client, 'worker', {'channel': 'SUBSCRIBER'}, transport=authority)
                    assert res2['sent'] == 0


# ---------------------------------------------------------------------------
# Diagnostic F: Headroom and Historical Reproduction Qualification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d07_worker_request_headroom_and_timing_characterization(encrypted):
    """F. Quantitative worker request headroom characterization under standard 5.0s budget."""
    x = encrypted
    clock = Clock()
    clock.now = NOW
    await prepare_subscriber_delivery(x, clock)

    with isolation.start_test_child('authority') as authority, isolation.start_test_child('client') as client:
        with hosted(x, clock) as (repo, host, relay, elected):
            relay.open(authority)
            relay.request(authority, 'elect', {})
            relay.open(client)
            relay.request(authority, 'configure', dict(kind='UNKNOWN', ended=False, fault='accept_then_error'))

            t0 = time.monotonic()
            res = relay.request(client, 'worker', {'channel': 'SUBSCRIBER'}, transport=authority, timeout=5.0)
            elapsed = time.monotonic() - t0

            headroom_s = 5.0 - elapsed
            assert res['sent'] == 0
            assert headroom_s > 0, f"Worker exceeded 5.0s budget: took {elapsed:.3f}s"

            d_status = x['c'].execute("SELECT status FROM notification_deliveries_v2 WHERE delivery_id=2").fetchone()[0]
            assert d_status == 'UNCERTAIN'

            inv_row = x['c'].execute("SELECT invocation_id FROM transport_invocations WHERE delivery_id=2").fetchone()
            invocation = inv_row[0]
            facts = {r[0] for r in x['c'].execute("SELECT fact_kind FROM transport_facts WHERE invocation_id=?", (invocation,)).fetchall()}
            assert 'UNKNOWN' in facts

            timing_report = {
                'operation': 'worker_subscriber_5s_budget',
                'elapsed_ms': round(elapsed * 1000, 2),
                'headroom_ms': round(headroom_s * 1000, 2),
                'delivery_status': d_status,
                'sent': res['sent']
            }
            sys.__stdout__.write(f"\nDIAGNOSTIC_TIMING {json.dumps(timing_report, sort_keys=True)}\n")
