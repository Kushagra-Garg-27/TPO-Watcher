"""Stored production authorization, separate from PR11's pure naming policy.

Email ownership and requested profiles grant no enrollment authority. Operator
authorization objects originate only in the configured external operator
adapter; student cookies and request models cannot construct that adapter.
"""
from dataclasses import dataclass
import hashlib
import secrets
import time

from app.database.account_schema import account_policy_enabled

CONSENT_POLICY = "student-email-v1"


@dataclass(frozen=True)
class OperatorAuthorization:
    actor: str
    authorization_reference: str

    def __post_init__(self):
        for value in (self.actor, self.authorization_reference):
            if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
                raise ValueError("External operator authorization evidence required")


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def production_notification_eligible(connection, user_id, *, destination=None, now_ms=None):
    if not account_policy_enabled(connection):
        return False
    row = connection.execute("""SELECT a.*,u.is_verified,u.is_active,u.branch_canonical,u.graduation_year
      FROM student_accounts a JOIN users u ON u.id=a.user_id WHERE a.user_id=?""", (user_id,)).fetchone()
    if not row:
        return False
    user = dict(row)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    return bool(user["role"] == "SUBSCRIBER" and user["account_state"] == "ACTIVE"
        and user["is_verified"] == 1 and user["is_active"] == 1
        and user["review_state"] == "APPROVED" and user["review_evidence"]
        and user["institution"] == "VIT_PUNE" and user["trusted_cohort"] == user["graduation_year"] == 2028
        and user["trusted_branch"] == user["branch_canonical"]
        and user["reviewed_profile_version"] == user["profile_version"]
        and user["consent_state"] == "GRANTED" and user["consent_policy"] == CONSENT_POLICY
        and user["consent_updated_ms"] is not None and 0 < user["consent_updated_ms"] <= now
        and user["notification_email"] and user["destination_verified_ms"] is not None
        and 0 < user["destination_verified_ms"] <= now
        and (destination is None or destination == user["notification_email"]))


def production_recipient_destination(connection, user_id):
    if not production_notification_eligible(connection, user_id):
        return None
    return connection.execute("SELECT notification_email FROM student_accounts WHERE user_id=?", (user_id,)).fetchone()[0]


class AccountService:
    """Mutations participate in the caller's existing SQLite write transaction."""
    def __init__(self, connection, *, clock=None):
        if not account_policy_enabled(connection):
            raise ValueError("Production account policy is not installed")
        self.c = connection
        self.clock = clock or (lambda: int(time.time() * 1000))

    def _account(self, uid):
        row = self.c.execute("SELECT * FROM student_accounts WHERE user_id=?", (uid,)).fetchone()
        if row is None:
            raise ValueError("Account unavailable")
        return dict(row)

    def _audit(self, uid, action, *, actor="ACCOUNT_HOLDER", evidence=None):
        self.c.execute("INSERT INTO account_audit(user_id,action,actor,evidence_reference,profile_version,created_ms) VALUES(?,?,?,?,?,?)",
            (uid, action, actor, evidence, self._account(uid)["profile_version"], self.clock()))

    def can_login(self, uid):
        row = self.c.execute("SELECT a.account_state,u.is_verified FROM student_accounts a JOIN users u ON u.id=a.user_id WHERE a.user_id=?", (uid,)).fetchone()
        return bool(row and row[0] == "ACTIVE" and row[1] == 1)

    def status(self, uid):
        account = self._account(uid)
        user = dict(self.c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
        return {"email": user["email"], "email_verified": bool(user["is_verified"]),
            "role": account["role"], "account_state": account["account_state"], "review_state": account["review_state"],
            "branch_canonical": user["branch_canonical"], "graduation_year": user["graduation_year"],
            "notification_email": account["notification_email"], "destination_verified": account["destination_verified_ms"] is not None,
            "consent_state": account["consent_state"], "consent_policy": CONSENT_POLICY,
            "delivery_eligible": production_notification_eligible(self.c, uid),
            "enrollment_model": "OPERATOR_REVIEW_REQUIRED"}

    def review(self, uid, decision, *, authority, institution=None, cohort=None, branch=None,
               evidence_reference, role="SUBSCRIBER"):
        if type(authority) is not OperatorAuthorization:
            raise PermissionError("External operator authorization required")
        if decision not in ("APPROVED", "REJECTED", "SUSPENDED", "REVOKED") or role not in ("SUBSCRIBER", "ADMINISTRATOR"):
            raise ValueError("Invalid review state")
        if not isinstance(evidence_reference, str) or not evidence_reference.strip() or len(evidence_reference) > 200 or any(ord(c) < 32 for c in evidence_reference):
            raise ValueError("Review evidence reference required")
        account = self._account(uid)
        if account["account_state"] == "REVOKED":
            raise ValueError("Revoked accounts require an independently reviewed recovery procedure")
        if decision == "APPROVED":
            user = self.c.execute("SELECT branch_canonical,graduation_year FROM users WHERE id=?", (uid,)).fetchone()
            from app.subscribers.canonical import CanonicalBranch
            CanonicalBranch(branch)
            if institution != "VIT_PUNE" or cohort != 2028 or (branch, cohort) != tuple(user):
                raise ValueError("Trusted review must match current institutional profile")
        self.c.execute("""UPDATE student_accounts SET role=?,account_state=?,review_state=?,institution=?,trusted_cohort=?,
            trusted_branch=?,review_evidence=?,reviewed_profile_version=profile_version,session_epoch=session_epoch+1 WHERE user_id=?""",
            (role, decision if decision in ("SUSPENDED", "REVOKED") else "ACTIVE",
             decision if decision in ("APPROVED", "REJECTED") else "UNREVIEWED", institution, cohort, branch, evidence_reference, uid))
        self.c.execute("UPDATE account_sessions SET revoked_ms=? WHERE user_id=? AND revoked_ms IS NULL", (self.clock(), uid))
        self.c.execute("UPDATE action_tokens SET used_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE user_id=? AND used_at IS NULL AND token_type IN('MANAGE_PREFS','ACCOUNT_LOGIN','SIGNUP_VERIFY')", (uid,))
        self._audit(uid, "REVIEW_" + decision, actor=authority.actor, evidence=authority.authorization_reference + "/" + evidence_reference)

    def update_profile(self, uid, *, branch, cohort):
        from app.subscribers.canonical import CanonicalBranch
        CanonicalBranch(branch)
        if cohort != 2028:
            raise ValueError("Only the reviewed 2028 cohort is supported")
        self.c.execute("UPDATE users SET branch_canonical=?,graduation_year=? WHERE id=?", (branch, cohort, uid))
        self._audit(uid, "PROFILE_UPDATED")

    def set_consent(self, uid, consent, policy):
        if type(consent) is not bool or policy != CONSENT_POLICY:
            raise ValueError("Explicit consent and current policy required")
        self.c.execute("UPDATE student_accounts SET consent_state=?,consent_policy=?,consent_updated_ms=? WHERE user_id=?",
            ("GRANTED" if consent else "WITHDRAWN", policy, self.clock(), uid))
        # Subscription activation is intentional and distinct from login/review.
        self.c.execute("UPDATE users SET is_active=? WHERE id=?", (int(consent), uid))
        self._audit(uid, "CONSENT_GRANTED" if consent else "CONSENT_WITHDRAWN", evidence=policy)

    def request_destination(self, uid, destination):
        from app.database.models import UserCreate
        normalized = UserCreate.normalize_email(destination)
        self.c.execute("UPDATE destination_challenges SET used_ms=? WHERE user_id=? AND used_ms IS NULL", (self.clock(), uid))
        self.c.execute("UPDATE student_accounts SET notification_email=?,destination_verified_ms=NULL WHERE user_id=?", (normalized, uid))
        raw = secrets.token_urlsafe(32)
        self.c.execute("INSERT INTO destination_challenges VALUES(?,?,?,?,NULL)", (_digest(raw), uid, normalized, self.clock() + 900000))
        self._audit(uid, "DESTINATION_REQUESTED")
        return raw

    def confirm_destination(self, raw):
        row = self.c.execute("""UPDATE destination_challenges SET used_ms=? WHERE token_hash=? AND used_ms IS NULL AND expires_ms>?
            AND EXISTS(SELECT 1 FROM student_accounts a WHERE a.user_id=destination_challenges.user_id AND a.account_state='ACTIVE'
              AND a.notification_email=destination_challenges.destination) RETURNING user_id,destination""", (self.clock(), _digest(raw), self.clock())).fetchone()
        if row is None:
            return None
        self.c.execute("UPDATE student_accounts SET destination_verified_ms=? WHERE user_id=?", (self.clock(), row[0]))
        self._audit(row[0], "DESTINATION_VERIFIED")
        return row[0]

    def register_session(self, uid, token):
        if not self.can_login(uid):
            raise PermissionError("Account access unavailable")
        self.c.execute("INSERT INTO account_sessions VALUES(?,?,?,?,NULL)",
            (_digest(token), uid, self._account(uid)["session_epoch"], int(token.split(":")[1]) * 1000))

    def session_valid(self, uid, token):
        return self.can_login(uid) and bool(self.c.execute("""SELECT 1 FROM account_sessions s JOIN student_accounts a USING(user_id)
            WHERE s.token_hash=? AND s.user_id=? AND s.session_epoch=a.session_epoch AND s.revoked_ms IS NULL AND s.expires_ms>?""",
            (_digest(token), uid, self.clock())).fetchone())

    def terminate_sessions(self, uid):
        self.c.execute("UPDATE student_accounts SET session_epoch=session_epoch+1 WHERE user_id=?", (uid,))
        self.c.execute("UPDATE account_sessions SET revoked_ms=? WHERE user_id=? AND revoked_ms IS NULL", (self.clock(), uid))
        self._audit(uid, "SESSIONS_TERMINATED")

    def revoke_session(self, token):
        self.c.execute("UPDATE account_sessions SET revoked_ms=? WHERE token_hash=? AND revoked_ms IS NULL", (self.clock(), _digest(token)))

    def request_closure(self, uid):
        self.set_consent(uid, False, CONSENT_POLICY)
        self.c.execute("UPDATE student_accounts SET account_state='REVOKED',notification_email=NULL,destination_verified_ms=NULL WHERE user_id=?", (uid,))
        self.terminate_sessions(uid)
        self.c.execute("DELETE FROM destination_challenges WHERE user_id=?", (uid,))
        self.c.execute("UPDATE action_tokens SET used_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE user_id=? AND used_at IS NULL", (uid,))
        self._audit(uid, "CLOSURE_REQUESTED")

    def prune_expired_credentials(self, *, retention_days=30):
        if not 0 <= retention_days <= 30:
            raise ValueError("Credential retention must be between zero and 30 days")
        cutoff = self.clock() - retention_days * 86400000
        sessions = self.c.execute("DELETE FROM account_sessions WHERE expires_ms<?", (cutoff,)).rowcount
        challenges = self.c.execute("DELETE FROM destination_challenges WHERE expires_ms<?", (cutoff,)).rowcount
        return sessions + challenges
