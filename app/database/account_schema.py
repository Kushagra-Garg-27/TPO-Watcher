"""Explicit production account migration; never grants historical sending rights.

Install only in an authorized release database migration. Existing fictional S1
fixtures do not silently acquire this production policy. Delivery facts remain
in their original tables and no destructive migration is performed.
"""
import sqlite3
import re

VERSION = "student-accounts-review-v1"


def account_policy_enabled(connection):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_policy'").fetchone():
        return False
    row = connection.execute("SELECT version FROM account_policy WHERE singleton=1").fetchone()
    if row is None or row[0] != VERSION:
        raise sqlite3.IntegrityError("Unsupported production account policy")
    return True


def schema_statements(*, versioned_subscribers):
    statements = [
        "CREATE TABLE IF NOT EXISTS account_policy(singleton INTEGER PRIMARY KEY CHECK(singleton=1),version TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS student_accounts(
          user_id INTEGER PRIMARY KEY REFERENCES users(id),
          role TEXT NOT NULL DEFAULT 'SUBSCRIBER' CHECK(role IN('SUBSCRIBER','ADMINISTRATOR')),
          account_state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(account_state IN('ACTIVE','SUSPENDED','REVOKED')),
          review_state TEXT NOT NULL DEFAULT 'UNREVIEWED' CHECK(review_state IN('UNREVIEWED','APPROVED','REJECTED')),
          institution TEXT, trusted_cohort INTEGER, trusted_branch TEXT, review_evidence TEXT,
          profile_version INTEGER NOT NULL DEFAULT 0, reviewed_profile_version INTEGER,
          notification_email TEXT, destination_verified_ms INTEGER,
          consent_state TEXT NOT NULL DEFAULT 'NEVER' CHECK(consent_state IN('NEVER','GRANTED','WITHDRAWN')),
          consent_policy TEXT, consent_updated_ms INTEGER, session_epoch INTEGER NOT NULL DEFAULT 0)
        """,
        """CREATE TABLE IF NOT EXISTS account_sessions(
          token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
          session_epoch INTEGER NOT NULL,expires_ms INTEGER NOT NULL,revoked_ms INTEGER)
        """,
        """CREATE TABLE IF NOT EXISTS destination_challenges(
          token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
          destination TEXT NOT NULL,expires_ms INTEGER NOT NULL,used_ms INTEGER)
        """,
        """CREATE TABLE IF NOT EXISTS account_audit(
          id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),action TEXT NOT NULL,
          actor TEXT NOT NULL,evidence_reference TEXT,profile_version INTEGER NOT NULL,created_ms INTEGER NOT NULL)
        """,
        "CREATE INDEX IF NOT EXISTS account_sessions_user ON account_sessions(user_id)",
        "CREATE INDEX IF NOT EXISTS destination_challenges_user ON destination_challenges(user_id)",
        """CREATE TRIGGER IF NOT EXISTS account_create AFTER INSERT ON users BEGIN
          INSERT INTO student_accounts(user_id) VALUES(NEW.id); END""",
        """CREATE TRIGGER IF NOT EXISTS account_profile_change AFTER UPDATE OF branch_canonical,graduation_year,email ON users
          WHEN NEW.branch_canonical IS NOT OLD.branch_canonical OR NEW.graduation_year IS NOT OLD.graduation_year OR NEW.email IS NOT OLD.email
          BEGIN UPDATE student_accounts SET review_state='UNREVIEWED',institution=NULL,trusted_cohort=NULL,
          trusted_branch=NULL,review_evidence=NULL,reviewed_profile_version=NULL,profile_version=profile_version+1 WHERE user_id=NEW.id; END""",
        """CREATE TRIGGER IF NOT EXISTS account_unsubscribe AFTER UPDATE OF is_active ON users
          WHEN NEW.is_active=0 AND OLD.is_active<>0 BEGIN
          UPDATE student_accounts SET consent_state='WITHDRAWN',session_epoch=session_epoch+1 WHERE user_id=NEW.id;
          UPDATE account_sessions SET revoked_ms=CAST(strftime('%s','now') AS INTEGER)*1000 WHERE user_id=NEW.id AND revoked_ms IS NULL;
          UPDATE destination_challenges SET used_ms=CAST(strftime('%s','now') AS INTEGER)*1000 WHERE user_id=NEW.id AND used_ms IS NULL;
          UPDATE action_tokens SET used_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE user_id=NEW.id AND used_at IS NULL AND token_type IN('MANAGE_PREFS','ACCOUNT_LOGIN'); END""",
        """CREATE TRIGGER IF NOT EXISTS account_primary_email_change AFTER UPDATE OF email ON users
          WHEN NEW.email IS NOT OLD.email BEGIN
          UPDATE users SET is_verified=0,verified_at=NULL WHERE id=NEW.id;
          UPDATE student_accounts SET session_epoch=session_epoch+1,consent_state='WITHDRAWN',notification_email=NULL,destination_verified_ms=NULL WHERE user_id=NEW.id;
          UPDATE account_sessions SET revoked_ms=CAST(strftime('%s','now') AS INTEGER)*1000 WHERE user_id=NEW.id AND revoked_ms IS NULL;
          UPDATE destination_challenges SET used_ms=CAST(strftime('%s','now') AS INTEGER)*1000 WHERE user_id=NEW.id AND used_ms IS NULL;
          UPDATE action_tokens SET used_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE user_id=NEW.id AND used_at IS NULL; END""",
        "CREATE TRIGGER IF NOT EXISTS account_audit_immutable_update BEFORE UPDATE ON account_audit BEGIN SELECT RAISE(ABORT,'immutable account audit'); END",
        "CREATE TRIGGER IF NOT EXISTS account_audit_immutable_delete BEFORE DELETE ON account_audit BEGIN SELECT RAISE(ABORT,'immutable account audit'); END",
    ]
    # Epoch invalidation is required even when consent is withdrawn and granted
    # before a worker next runs: already sealed obligations may never revive.
    if versioned_subscribers:
        statements.append("""CREATE TRIGGER IF NOT EXISTS account_consent_epoch AFTER UPDATE OF
          role,account_state,review_state,institution,trusted_cohort,trusted_branch,profile_version,
          notification_email,destination_verified_ms,consent_state,consent_policy ON student_accounts BEGIN
          UPDATE subscriber_versions SET consent_epoch=consent_epoch+1,profile_epoch=profile_epoch+1 WHERE user_id=NEW.user_id; END""")
    return statements


def _versioned(connection):
    return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='subscriber_versions'").fetchone())


def install_account_schema(connection):
    for statement in schema_statements(versioned_subscribers=_versioned(connection)):
        connection.execute(statement)
    connection.execute("INSERT OR IGNORE INTO account_policy VALUES(1,?)", (VERSION,))
    verify_account_schema(connection)
    connection.execute("INSERT OR IGNORE INTO student_accounts(user_id) SELECT id FROM users")


def verify_account_schema(connection):
    """Return only exact reviewed extension objects; reject drift and extras."""
    if not account_policy_enabled(connection):
        raise sqlite3.IntegrityError("Production account schema required")
    def canonical(sql):
        return re.sub(r"\s+", "", re.sub(r"\bIF\s+NOT\s+EXISTS\b", "", sql, flags=re.I)).rstrip(";").lower()
    names = set()
    tables = set()
    for statement in schema_statements(versioned_subscribers=_versioned(connection)):
        kind, name = re.match(r"CREATE (TABLE|INDEX|TRIGGER) IF NOT EXISTS (\w+)", statement).groups()
        names.add(name)
        if kind == "TABLE": tables.add(name)
        row = connection.execute("SELECT type,sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if row is None or row[0] != kind.lower() or canonical(row[1]) != canonical(statement):
            raise sqlite3.IntegrityError("Account schema shape mismatch: " + name)
    actual = connection.execute("SELECT name FROM sqlite_master WHERE sql IS NOT NULL AND tbl_name IN (" + ",".join("?" for _ in tables) + ")", tuple(tables)).fetchall()
    if not {row[0] for row in actual} <= names:
        raise sqlite3.IntegrityError("Account schema has unexpected objects")
    return names
