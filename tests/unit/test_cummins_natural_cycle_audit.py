"""Phase C1: synthetic correctness contracts; confirmed defects intentionally stay red.

Run only through tests/isolation_bootstrap.py. No production corrections.
F03/F15 desired copy policies are distinct from unavailable employer metadata.
"""
import ast
import json
from datetime import datetime as RealDateTime, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import isolation_bootstrap as isolation

if isolation.ROOT is None:
    raise RuntimeError("C1 requires the established isolation bootstrap")

import pytest
from fastapi.testclient import TestClient
from app.api import routes_auth, routes_preferences
from app.api.app import create_app
from app.api.email_service import get_email_service
from app.database import repository as company_db, sqlite_repository as subscriber_db
from app.database.repository import DatabaseRepository
from app.monitoring import watcher as watcher_module
from app.monitoring.detector import CompanyDetector
from app.notifications import email as email_module
from app.notifications.email import EmailNotificationProvider
from app.subscribers import worker as worker_module
from app.subscribers.canonical import CanonicalBranch, extract_eligible_canonical_branches
from app.subscribers.fanout import FanoutEngine
from app.subscribers.matcher import SubscriptionMatcher
from app.subscribers.worker import DeliveryWorker
from app.tpo.models import CompanyRecord

SYNTHETIC_ID = "SYNTHETIC-CUMMINS-AUDIT-001"
CE = "VIT-BTech-Computer Engineering"
DS = "VIT-BTech-Computer Science and Engineering (Data Science)"
AIDS = "VIT-BTech-Artificial Intelligence & Data Science"
IT = "VIT-BTech-Information Technology"
OBSERVED_PROGRAMS = ", ".join([
    CE,
    "VIT-BTech - Computer Science and Engineering (Artificial Intelligence and Machine Learning)",
    AIDS,
    "VIT-BTech-Electronics and Telecommunication Engg",
    "VIT-B.Tech. Computer Science and Artificial Intelligence",
    "VIT-BTech-Instrumentation and Control Engineering",
    "VIT-null",
    IT,
    "VIT-BTech-Computer Science and Engineering (Artificial Intelligence)",
    "VIT-BTech-Mechanical Engineering",
])


class ContractFailure(AssertionError):
    """Only a desired behavioral assertion raises this; setup errors do not."""


def require_contract(ok, finding, observed):
    if not ok:
        raise ContractFailure(f"{finding}: unmet declared contract; observed={observed!r}")


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    @classmethod
    def read(cls, markup):
        parser = cls()
        parser.feed(markup)
        return " ".join(" ".join(parser.parts).split())


class FakeNotifier:
    """Retain visible synthetic facts only, never headers/action-token URLs."""
    def __init__(self):
        self.student_text = []
        self.admin_records = []

    def _send_email_to(self, to_email, subject, html_content, extra_headers=None):
        assert to_email.endswith("@example.invalid")
        self.student_text.append(VisibleText.read(html_content))
        return True

    def send_new_company_notification(self, company, target_match):
        self.admin_records.append(company)
        return True

    def send_update_notification(self, company, changes):
        self.admin_records.append(company)
        return True


def make_company(**changes):
    values = dict(
        id=SYNTHETIC_ID, company="Fictional Internship Employer",
        company_code="SYNTHETIC-ONLY", regStartdate="13-Oct-2026",
        regStarttime="11:31", regEnddate="14-Oct-2026", regEndtime="10:00",
        maxPackage="25000.0", minPackage="25000.0",
        placementtype="Internship + Performance based PPO", internshiptype="N/A",
        academicyear="2026-27", isactive="True", tpoprogram=CE, organization="VIT",
    )
    values.update(changes)
    return CompanyRecord(**values)


@pytest.fixture
def audit_repo(tmp_path):
    path = (tmp_path / "synthetic-cummins-unit.sqlite").resolve()
    assert path.is_relative_to(isolation.ROOT)
    return DatabaseRepository(str(path))


def user(repo, serial=0, branch="VIT_CE", verified=True, **prefs):
    uid = repo.users.create_user(f"synthetic-{serial}@example.invalid", 2028, branch, **prefs)
    if verified:
        repo.users.set_verified(uid)
    return uid


def seed_seen(repo, record):
    repo.upsert_company(record)
    repo.set_baseline_initialized()
    return CompanyDetector(repo)


def worker(repo, notifier):
    return DeliveryWorker(repo.deliveries, repo.tokens, notifier, send_interval_seconds=0)


def safe_watcher(repo, monkeypatch, notifier):
    class NoLiveAuth:
        context = None

        async def is_authenticated(self):
            return True

        async def get_valid_page(self):
            return None

    class NoLiveClient:
        def __init__(self, *args):
            pass

        async def fetch_companies(self):
            raise RuntimeError("Unit fixture must never fetch")

    monkeypatch.setattr(watcher_module, "AuthManager", NoLiveAuth)
    monkeypatch.setattr(watcher_module, "TPOClient", NoLiveClient)
    monkeypatch.setattr(watcher_module, "TimezoneScheduler", lambda **kw: SimpleNamespace())
    monkeypatch.setattr(watcher_module, "EmailNotificationProvider", lambda: notifier)
    monkeypatch.setattr(watcher_module, "send_healthcheck_ping", lambda *a, **kw: None)
    monkeypatch.setattr(worker_module.settings, "BASE_URL", "https://audit.example.invalid")
    result = watcher_module.PlacementWatcher(db=repo)
    result.delivery_worker.send_interval_seconds = 0
    return result


def render_subscriber(repo, record=None, kind="NEW"):
    return VisibleText.read(worker(repo, FakeNotifier())._format_opportunity_email(
        record or make_company(), kind, "VIT_CE",
        "https://example.invalid/unsubscribe", "https://example.invalid/preferences",
    ))


def render_admin(monkeypatch, record=None):
    captured = []
    provider = EmailNotificationProvider()
    monkeypatch.setattr(provider, "_send_email", lambda subject, body: captured.append(
        VisibleText.read(body)) or True)
    assert provider.send_new_company_notification(record or make_company(), True)
    return captured[0]


@pytest.mark.parametrize("field,value", [
    ("regStarttime", "10:31"), ("regStarttime", "12:31"),
    ("regEndtime", "09:00"), ("regEndtime", "18:00"),
])
def test_f01_time_only_change_persists_raw_but_requires_update(audit_repo, field, value):
    detector = seed_seen(audit_repo, make_company())
    result = detector.process_fetched_companies([make_company(**{field: value})])
    stored = audit_repo.get_company(SYNTHETIC_ID)
    raw = json.loads(stored["raw_data_json"])
    assert raw[field] == value
    assert stored["registration_start"] == "13-Oct-2026"
    assert stored["registration_end"] == "14-Oct-2026"
    assert not result.new_companies and not result.deactivated_companies
    require_contract(len(result.updated_companies) == 1, "F01",
                     {"changed_raw": raw[field], "updates": len(result.updated_companies),
                      "admin_pending": len(audit_repo.get_pending_notifications())})


def test_f02_distinct_update_after_sent_is_independently_queueable(audit_repo):
    user(audit_repo)
    detector = seed_seen(audit_repo, make_company())
    fanout = FanoutEngine(audit_repo.users, audit_repo.deliveries)
    first = detector.process_fetched_companies([make_company(regEnddate="15-Oct-2026")])
    assert len(first.updated_companies) == 1
    assert fanout.dispatch_opportunity(first.updated_companies[0][0], "UPDATE") == 1
    assert fanout.dispatch_opportunity(first.updated_companies[0][0], "UPDATE") == 0
    claimed = audit_repo.deliveries.claim_pending_batch(batch_size=1)
    assert len(claimed) == 1 and claimed[0]["notification_type"] == "UPDATE"
    audit_repo.deliveries.mark_sent(claimed[0]["id"])
    assert audit_repo.deliveries.get_delivery_stats()["SENT"] == 1
    second = detector.process_fetched_companies([make_company(regEnddate="16-Oct-2026")])
    assert len(second.updated_companies) == 1
    assert len(audit_repo.get_pending_notifications()) == 2
    assert json.loads(audit_repo.get_company(SYNTHETIC_ID)["raw_data_json"])["regEnddate"] == "16-Oct-2026"
    inserted = fanout.dispatch_opportunity(second.updated_companies[0][0], "UPDATE")
    require_contract(inserted == 1, "F02", {"second_update_inserted": inserted, "first_status": "SENT"})


def test_f03_unknown_compensation_units_must_not_be_asserted_as_lpa(audit_repo):
    text = render_subscriber(audit_repo)
    assert "25,000" in text
    assert "Unit not specified by TPO" in text
    assert "Internship + Performance based PPO" in text
    # No invented degree/unit fields: raw numeric magnitude proves neither.
    require_contract("LPA" not in text, "F03",
                     {"amount_preserved": True, "annual_lpa_label": "LPA" in text})


@pytest.mark.parametrize("branch,expected", [
    ("VIT_CSE_DS", False), ("VIT_AIDS", True), ("VIT_CE", True),
    ("VIT_CSE_AIML", True), ("VIT_IT", True), ("VIT_MECH", True),
    ("UNSUPPORTED", False),
])
def test_observed_email_program_matrix_uses_saved_code(branch, expected):
    record = make_company(tpoprogram=OBSERVED_PROGRAMS)
    branches = extract_eligible_canonical_branches(record.tpoprogram, None, "VIT")
    assert branches == {
        CanonicalBranch.VIT_CE, CanonicalBranch.VIT_CSE_AIML, CanonicalBranch.VIT_AIDS,
        CanonicalBranch.VIT_ENTC, CanonicalBranch.VIT_CS_AI, CanonicalBranch.VIT_ICE,
        CanonicalBranch.VIT_IT, CanonicalBranch.VIT_CSE_AI, CanonicalBranch.VIT_MECH,
    }
    profile = dict(id=1, branch_canonical=branch, pref_internship=True,
                   pref_placement=False, pref_ppo=False)
    assert SubscriptionMatcher.matches_subscriber(record, profile, branches) is expected


@pytest.mark.parametrize("token,expected", [
    (DS, {CanonicalBranch.VIT_CSE_DS}), (AIDS, {CanonicalBranch.VIT_AIDS}),
    (CE, {CanonicalBranch.VIT_CE}),
    ("VIT-BTech-Computer Engineering (Software Engineering)", {CanonicalBranch.VIT_CE_SE}),
    ("VIIT-BTech-Computer Engineering", set()),
])
def test_f08_exact_branch_and_institution_controls(token, expected):
    organization = "VIIT" if token.startswith("VIIT-") else "VIT"
    assert extract_eligible_canonical_branches(token, None, organization) == expected


@pytest.mark.parametrize("programs,organization,want", [
    (DS + ", VIIT-BTech-Computer Engineering", "VIT, VIIT", {CanonicalBranch.VIT_CSE_DS}),
    ("UNKNOWN-BTech-Computer Engineering", "VIT", set()),
])
def test_f08_qualified_foreign_tokens_cannot_infer_vit_branch(programs, organization, want):
    actual = extract_eligible_canonical_branches(programs, None, organization)
    require_contract(actual == want, "F08", sorted(b.value for b in actual))


class CountOnlyEmailService:
    def __init__(self):
        self.verification_count = 0
        self.preference_count = 0

    def send_verification_email(self, to_email, raw_token):
        assert to_email.endswith("@example.invalid")
        self.verification_count += 1
        return True

    def send_preference_link_email(self, to_email, raw_token):
        assert to_email.endswith("@example.invalid")
        self.preference_count += 1
        return True


def route_client(repo, uid):
    app = create_app()
    email = CountOnlyEmailService()
    app.dependency_overrides[routes_auth.get_db_repos] = lambda: (repo.users, repo.tokens)
    app.dependency_overrides[routes_preferences.get_db_repos] = lambda: (repo.users, repo.tokens)
    app.dependency_overrides[get_email_service] = lambda: email
    app.dependency_overrides[routes_preferences.get_current_user_id] = lambda: uid
    return TestClient(app), email


def test_f07_authenticated_branch_correction_requires_persisted_readback(audit_repo):
    uid = user(audit_repo, branch="VIT_AIDS")
    client, _ = route_client(audit_repo, uid)
    with client:
        assert client.get("/api/v1/preferences").json()["branch_canonical"] == "VIT_AIDS"
        response = client.put("/api/v1/preferences", json=dict(
            branch_canonical="VIT_CSE_DS", pref_internship=True,
            pref_placement=False, pref_ppo=True))
        assert response.status_code in (200, 422)
        if response.status_code == 422:
            errors = response.json()["detail"]
            assert any(e["loc"][-1] == "branch_canonical" and
                       e["type"] == "extra_forbidden" for e in errors)
        saved = client.get("/api/v1/preferences").json()["branch_canonical"]
    assert audit_repo.users.get_by_id(uid)["branch_canonical"] == saved
    require_contract(response.status_code == 200 and saved == "VIT_CSE_DS", "F07",
                     {"status": response.status_code, "saved_branch": saved})


@pytest.mark.parametrize("verified", [False, True])
def test_f07_resignup_does_not_silently_reassign_existing_branch(audit_repo, verified):
    uid = user(audit_repo, branch="VIT_AIDS", verified=verified)
    client, email = route_client(audit_repo, uid)
    with client:
        response = client.post("/api/v1/auth/signup", json=dict(
            email="synthetic-0@example.invalid", graduation_year=2028,
            branch_canonical="VIT_CSE_DS", pref_internship=False,
            pref_placement=False, pref_ppo=True))
        assert response.status_code == 201
        assert client.get("/api/v1/preferences").json()["branch_canonical"] == "VIT_AIDS"
    assert audit_repo.users.get_by_id(uid)["branch_canonical"] == "VIT_AIDS"
    prefs = audit_repo.users.get_preferences(uid)
    assert bool(prefs["pref_internship"]) is verified
    assert (email.preference_count, email.verification_count) == ((1, 0) if verified else (0, 1))


@pytest.mark.asyncio
@pytest.mark.parametrize("field,want", [("regStarttime", "11:31"), ("regEndtime", "10:00")])
async def test_f09_admin_reconstruction_preserves_times(audit_repo, monkeypatch, field, want):
    audit_repo.set_baseline_initialized()
    CompanyDetector(audit_repo).process_fetched_companies([make_company()])
    assert json.loads(audit_repo.get_company(SYNTHETIC_ID)["raw_data_json"])[field] == want
    notifier = FakeNotifier()
    watcher = safe_watcher(audit_repo, monkeypatch, notifier)
    await watcher._process_notifications()
    assert len(notifier.admin_records) == 1
    assert not audit_repo.get_pending_notifications()
    require_contract(getattr(notifier.admin_records[0], field) == want, "F09",
                     {"field": field, "reconstructed": getattr(notifier.admin_records[0], field)})


def test_f09_direct_admin_renderer_control_has_both_times(monkeypatch):
    text = render_admin(monkeypatch)
    assert "13-Oct-2026 11:31" in text and "14-Oct-2026 10:00" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("old,new,words", [
    ("True", "False", ("registration closed", "company inactive", "active status: false", "active status false")),
    ("False", "True", ("registration reopened", "registration open", "company active", "active status: true", "active status true")),
])
async def test_f10_first_status_update_communicates_lifecycle(audit_repo, old, new, words):
    user(audit_repo)
    detector = seed_seen(audit_repo, make_company(isactive=old))
    updated = make_company(isactive=new)
    result = detector.process_fetched_companies([updated])
    assert len(result.updated_companies) == 1
    assert any("Active Status" in c for c in result.updated_companies[0][1])
    assert FanoutEngine(audit_repo.users, audit_repo.deliveries).dispatch_opportunity(updated, "UPDATE") == 1
    notifier = FakeNotifier()
    assert await worker(audit_repo, notifier).process_batch_once() == 1
    body = notifier.student_text[0].lower()
    require_contract(any(word in body for word in words), "F10",
                     {"explicit_status": new, "status_context_visible": False})


def test_f10_disappearance_is_not_explicit_closure_and_empty_feed_preserves(audit_repo):
    main = make_company()
    control = make_company(id="SYNTHETIC-CONTROL", company="Fictional Control")
    detector = CompanyDetector(audit_repo)
    detector.process_fetched_companies([main, control])
    absent = detector.process_fetched_companies([control])
    assert absent.deactivated_companies == [SYNTHETIC_ID]
    assert not absent.updated_companies
    stored = audit_repo.get_company(SYNTHETIC_ID)
    assert stored["is_active"] == "False"
    assert json.loads(stored["raw_data_json"])["isactive"] == "True"
    empty = detector.process_fetched_companies([])
    assert not empty.deactivated_companies
    assert audit_repo.get_company("SYNTHETIC-CONTROL")["is_active"] == "True"


def frozen_clock(instant):
    class Frozen(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(instant.timestamp(), tz)
    return Frozen


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["admin", "subscriber"])
async def test_f11_detected_label_uses_new_event_clock(audit_repo, monkeypatch, channel):
    event = RealDateTime(2026, 10, 13, 11, 30, tzinfo=timezone.utc)
    render = RealDateTime(2026, 10, 13, 11, 35, tzinfo=timezone.utc)
    monkeypatch.setattr(company_db, "datetime", frozen_clock(event))
    monkeypatch.setattr(subscriber_db, "datetime", frozen_clock(event))
    audit_repo.set_baseline_initialized()
    CompanyDetector(audit_repo).process_fetched_companies([make_company()])
    stored = audit_repo.get_company(SYNTHETIC_ID)
    assert stored["first_seen_at"] == event.isoformat()
    if channel == "admin":
        captured = []
        provider = EmailNotificationProvider()
        monkeypatch.setattr(provider, "_send_email", lambda subject, body:
                            captured.append(VisibleText.read(body)) or True)
        monkeypatch.setattr(email_module, "datetime", frozen_clock(render))
        watcher = safe_watcher(audit_repo, monkeypatch, provider)
        await watcher._process_notifications()
        assert len(captured) == 1
        text = captured[0]
    else:
        user(audit_repo)
        assert FanoutEngine(audit_repo.users, audit_repo.deliveries).dispatch_opportunity(make_company()) == 1
        with audit_repo._get_conn() as conn:
            assert conn.execute("SELECT created_at FROM notification_deliveries").fetchone()[0] == event.isoformat()
        monkeypatch.setattr(worker_module, "datetime", frozen_clock(render))
        notifier = FakeNotifier()
        assert await worker(audit_repo, notifier).process_batch_once() == 1
        text = notifier.student_text[0]
    label = "Detected:" if channel == "admin" else "Detected at:"
    require_contract(f"{label} 13-Oct-2026 17:00:00 IST" in text, "F11",
                     {"channel": channel, "event_ist": "17:00:00",
                      "render_ist_visible": "13-Oct-2026 17:05:00 IST" in text})


@pytest.mark.asyncio
async def test_f11_queued_new_keeps_event_deadline_snapshot(audit_repo):
    user(audit_repo)
    audit_repo.upsert_company(make_company())
    assert FanoutEngine(audit_repo.users, audit_repo.deliveries).dispatch_opportunity(make_company()) == 1
    audit_repo.upsert_company(make_company(regEnddate="15-Oct-2026", regEndtime="18:00"))
    assert json.loads(audit_repo.get_company(SYNTHETIC_ID)["raw_data_json"])["regEnddate"] == "15-Oct-2026"
    notifier = FakeNotifier()
    assert await worker(audit_repo, notifier).process_batch_once() == 1
    body = notifier.student_text[0]
    require_contract("14-Oct-2026 10:00" in body, "F11",
                     {"original_deadline_present": False, "latest_deadline_present": "15-Oct-2026 18:00" in body})


@pytest.mark.parametrize("field,value", [("organization", "VIT"), ("programnew", CE + ", " + IT)])
def test_f14_independent_matching_input_change_requires_update(audit_repo, field, value):
    initial = make_company(organization="VU") if field == "organization" else make_company(programnew=CE)
    updated = initial.model_copy(update={field: value})
    before = extract_eligible_canonical_branches(initial.tpoprogram, initial.programnew, initial.organization)
    after = extract_eligible_canonical_branches(updated.tpoprogram, updated.programnew, updated.organization)
    expected_before = set() if field == "organization" else {CanonicalBranch.VIT_CE}
    expected_after = {CanonicalBranch.VIT_CE} if field == "organization" else {
        CanonicalBranch.VIT_CE, CanonicalBranch.VIT_IT}
    assert before == expected_before and after == expected_after
    detector = seed_seen(audit_repo, initial)
    result = detector.process_fetched_companies([updated])
    raw = json.loads(audit_repo.get_company(SYNTHETIC_ID)["raw_data_json"])
    assert raw[field] == value and raw["tpoprogram"] == CE
    require_contract(len(result.updated_companies) == 1, "F14",
                     {"field": field, "updates": len(result.updated_companies),
                      "branches_before": sorted(b.value for b in before),
                      "branches_after": sorted(b.value for b in after)})


@pytest.mark.parametrize("field,value,updates", [
    ("tpoprogram", CE + ", " + IT, 1),
    ("academicyear", "2027-28", 0),
    ("company_code", "SYNTHETIC-CHANGED", 0),
])
def test_f14_other_metadata_detection_characterization(audit_repo, field, value, updates):
    detector = seed_seen(audit_repo, make_company())
    result = detector.process_fetched_companies([make_company(**{field: value})])
    assert len(result.updated_companies) == updates
    assert json.loads(audit_repo.get_company(SYNTHETIC_ID)["raw_data_json"])[field] == value


@pytest.mark.parametrize("channel", ["admin", "subscriber"])
def test_f15_branch_match_copy_states_full_eligibility_is_unconfirmed(audit_repo, monkeypatch, channel):
    text = render_admin(monkeypatch) if channel == "admin" else render_subscriber(audit_repo)
    lower = text.lower()
    assert "Internship + Performance based PPO" in text
    caution = any(phrase in lower for phrase in (
        "does not confirm full eligibility", "does not establish full eligibility",
        "not a guarantee of eligibility", "eligibility has not been verified"))
    criteria_instruction = "criteria" in lower and any(
        word in lower for word in ("verify", "check"))
    disclaimer = caution and criteria_instruction
    # Declared future copy policy, not a demand to invent absent PDF metadata.
    require_contract(disclaimer, "F15", {"channel": channel, "full_criteria_disclaimer": disclaimer})


def _literal_array(source, export_name):
    """Bounded reader of current static JS literals, not a TypeScript evaluator."""
    marker = "export const " + export_name
    start = source.index("=", source.index(marker)) + 1
    length = len(source)
    items = 0

    def ws(pos):
        while pos < length:
            if source[pos].isspace():
                pos += 1
            elif source.startswith("//", pos):
                newline = source.find("\n", pos)
                pos = length if newline < 0 else newline + 1
            elif source.startswith("/*", pos):
                end = source.find("*/", pos + 2)
                if end < 0:
                    raise ValueError("Unclosed literal comment")
                pos = end + 2
            else:
                break
        return pos

    def value(pos, depth):
        nonlocal items
        items += 1
        if depth > 8 or items > 1000:
            raise ValueError("Static literal exceeds bound")
        pos = ws(pos)
        if pos >= length:
            raise ValueError("Incomplete static literal")
        ch = source[pos]
        if ch in "'\"":
            end = pos + 1
            while end < length:
                if source[end] == "\\":
                    end += 2
                elif source[end] == ch:
                    return ast.literal_eval(source[pos:end + 1]), end + 1
                else:
                    end += 1
            raise ValueError("Unclosed string")
        if ch == "[":
            result, pos = [], ws(pos + 1)
            while source[pos] != "]":
                item, pos = value(pos, depth + 1)
                result.append(item)
                pos = ws(pos)
                if source[pos] == ",":
                    pos = ws(pos + 1)
                elif source[pos] != "]":
                    raise ValueError("Unsupported array syntax")
            return result, pos + 1
        if ch == "{":
            result, pos = {}, ws(pos + 1)
            while source[pos] != "}":
                end = pos
                while end < length and (source[end].isalnum() or source[end] == "_"):
                    end += 1
                key = source[pos:end]
                if not key or key in result:
                    raise ValueError("Unsupported/duplicate object key")
                pos = ws(end)
                if source[pos] != ":":
                    raise ValueError("Unsupported object syntax")
                result[key], pos = value(pos + 1, depth + 1)
                pos = ws(pos)
                if source[pos] == ",":
                    pos = ws(pos + 1)
                elif source[pos] != "}":
                    raise ValueError("Unsupported object syntax")
            return result, pos + 1
        raise ValueError("Expressions/spreads/computed syntax are not supported")

    result, end = value(start, 0)
    suffix = ws(end)
    if source.startswith("as const", suffix):
        suffix += len("as const")
    suffix = ws(suffix)
    if suffix < length and source[suffix] == ";":
        suffix = ws(suffix + 1)
    if suffix < length and not source.startswith("export ", suffix):
        raise ValueError("Trailing literal expressions are not supported")
    if not isinstance(result, list):
        raise ValueError("Export initializer must be an array")
    return result


def test_f17_actual_frontend_catalog_and_groups_match_backend():
    source = (Path(__file__).resolve().parents[2] / "frontend/src/lib/branches.ts").read_text(encoding="utf-8")
    assert len(source) < 24000
    catalog = _literal_array(source, "CANONICAL_BRANCHES")
    groups = _literal_array(source, "BRANCH_GROUPS")
    ids = [item["value"] for item in catalog]
    grouped = [value for group in groups for value in group["branches"]]
    backend = {branch.value for branch in CanonicalBranch} - {"VIT_CS_AI", "VIT_ELEC"}
    assert len(ids) == len(set(ids)) == 12
    assert set(ids) == backend
    assert len(grouped) == len(set(grouped)) == 12
    assert set(grouped) == set(ids)
    assert [len(group["branches"]) for group in groups] == [8, 2, 1, 1]
    assert next(row["label"] for row in catalog if row["value"] == "VIT_CSE_DS") != next(
        row["label"] for row in catalog if row["value"] == "VIT_AIDS")


@pytest.mark.parametrize("saved", ["VIT_CE", "VIT_AIDS"])
def test_saved_wrong_branch_routes_by_persisted_code(audit_repo, saved):
    """Intended DS is fixture context, never an invented employer equivalence."""
    wrong_profile = user(audit_repo, serial=0, branch=saved)
    correctly_saved_ds = user(audit_repo, serial=1, branch="VIT_CSE_DS")
    record = make_company(tpoprogram=OBSERVED_PROGRAMS)
    audit_repo.upsert_company(record)
    assert FanoutEngine(audit_repo.users, audit_repo.deliveries).dispatch_opportunity(record) == 1
    with audit_repo._get_conn() as conn:
        recipients = [row[0] for row in conn.execute(
            "SELECT user_id FROM notification_deliveries").fetchall()]
    assert recipients == [wrong_profile]
    assert correctly_saved_ds not in recipients


def test_f10_clock_expiry_without_feed_event_is_not_explicit_closure(audit_repo, monkeypatch):
    before = RealDateTime(2026, 10, 14, 4, 29, tzinfo=timezone.utc)
    after = RealDateTime(2026, 10, 14, 4, 31, tzinfo=timezone.utc)
    monkeypatch.setattr(company_db, "datetime", frozen_clock(before))
    detector = seed_seen(audit_repo, make_company())
    monkeypatch.setattr(company_db, "datetime", frozen_clock(after))
    result = detector.process_fetched_companies([make_company()])
    stored = audit_repo.get_company(SYNTHETIC_ID)
    assert stored["last_seen_at"] == after.isoformat()
    assert stored["is_active"] == "True"
    assert not result.updated_companies and not result.deactivated_companies
    # Synthetic clock passage is distinct from an explicit employer closure.


@pytest.mark.parametrize("initializer", [
    "[].filter(x => x)", "[].map(x => x)",
    "[] as const.filter(x => x)", "[] as const\n.filter(x => x)",
])
def test_f17_literal_reader_rejects_suffix_expressions(initializer):
    with pytest.raises(ValueError, match="Trailing literal expressions"):
        _literal_array("export const BRANCH_GROUPS = " + initializer, "BRANCH_GROUPS")

