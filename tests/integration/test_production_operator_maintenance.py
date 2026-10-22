"""Owner maintenance admission tested only on disposable fictional accounts."""
import hashlib
import gc
import json
import secrets
import sqlite3

from test_notification_migration_privacy import history


def test_operator_review_is_explicit_durable_and_never_grants_consent(tmp_path, capsys):
    from app.database.production_maintenance import main
    path = tmp_path / 'fictional.sqlite'
    history(path)
    secret = tmp_path / 'wrapping-key'
    secret.write_bytes(secrets.token_bytes(32))
    secret.chmod(0o600)
    def args():
        # Legacy fixture context managers commit but do not close SQLite handles.
        # Finish their final WAL checkpoint before freezing the maintenance hash.
        gc.collect()
        return ['--database', str(path), '--database-sha256', hashlib.sha256(path.read_bytes()).hexdigest(),
            '--vault', str(tmp_path / 'vault'), '--checkpoint', str(tmp_path / 'checkpoint.sqlite'),
            '--wrapping-key', '1=' + str(secret), '--active-generation', '1',
            '--approval-reference', 'fictional-owner-approval', '--quiescence-reference', 'fictional-all-writers-stopped']
    assert main(['migrate', *args(), '--initialize-vault', '--recovery-directory', str(tmp_path / 'backup'),
        '--policy-reference', 'fictional-no-send']) == 0
    assert main(['review-account', *args(), '--user-id', '1', '--decision', 'APPROVED',
        '--actor', 'fictional-operator', '--review-evidence', 'fictional-roster-evidence',
        '--institution', 'VIT_PUNE', '--cohort', '2028', '--branch', 'VIT_CE']) == 0
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT review_state,consent_state,destination_verified_ms FROM student_accounts WHERE user_id=1').fetchone() == ('APPROVED', 'NEVER', None)
        assert connection.execute("SELECT actor FROM account_audit WHERE action='REVIEW_APPROVED'").fetchone()[0] == 'fictional-operator'
        assert connection.execute('SELECT count(*) FROM transport_invocations').fetchone()[0] == 0
    assert 'ACCOUNT_REVIEW_RECORDED_NO_SEND' in capsys.readouterr().out
    from app.monitoring.production_runtime import OwnerLock
    lock = OwnerLock(path.with_suffix(path.suffix + '.runtime.lock'))
    lock.acquire()
    try:
        assert main(['verify', *args()]) == 1
    finally:
        lock.close()
    assert main(['review-account', *args(), '--user-id', '1', '--decision', 'APPROVED',
        '--actor', 'fictional-operator', '--review-evidence', 'wrong-profile',
        '--institution', 'VIT_PUNE', '--cohort', '2028', '--branch', 'VIT_IT']) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT trusted_branch FROM student_accounts WHERE user_id=1').fetchone()[0] == 'VIT_CE'
    destination = tmp_path / 'administrator-destination'
    destination.write_text('operator@example.invalid')
    destination.chmod(0o600)
    capsys.readouterr()
    assert main(['register-admin', *args(), '--actor', 'fictional-owner', '--review-evidence', 'fictional-inbox-ownership',
        '--admin-destination-file', str(destination)]) == 0
    output = capsys.readouterr().out
    assert 'operator@example.invalid' not in output
    key = json.loads(output)['admin_recipient_key']
    assert main(['revoke-admin', *args(), '--actor', 'fictional-owner', '--admin-recipient-key', key]) == 0
    with sqlite3.connect(path) as connection:
        record = json.loads(connection.execute('SELECT value FROM system_state WHERE key=?', ('production:admin:' + key,)).fetchone()[0])
        assert record['active'] is False
