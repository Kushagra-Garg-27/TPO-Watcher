from test_notification_migration_privacy import encrypted, NOW
from test_production_runtime import build
import pytest


def test_production_admin_requires_registered_evidence_and_revocation_is_durable(encrypted):
    from app.auth.accounts import OperatorAuthorization
    from app.database.notification_migrator import atomic
    from app.database.event_repository import EventRepository, PolicyDenied
    from app.notifications.production_admin import register_admin, revoke_admin
    from app.database.notification_migrator import create_schema
    x = encrypted
    create_schema(x['c'], now_ms=NOW)
    fictional = EventRepository(x['c'], clock=lambda: NOW).add_admin()
    operator = OperatorAuthorization('fictional-owner', 'fictional-approval')
    with build(x) as runtime:
        key = register_admin(runtime.c, 'operator@example.invalid', authority=operator,
            ownership_evidence='fictional-owned-inbox', now_ms=NOW)
        assert register_admin(runtime.c, 'operator@example.invalid', authority=operator,
            ownership_evidence='fictional-owned-inbox', now_ms=NOW) == key
        runtime.start()
        try:
            assert runtime.recipient_eligible(key) is True
            assert runtime.recipient_eligible(fictional) is False
            with pytest.raises(PolicyDenied):
                runtime.repo.add_admin()
            with atomic(runtime.c):
                revoke_admin(runtime.c, key, authority=operator, now_ms=NOW + 1)
            assert runtime.recipient_eligible(key) is False
            with pytest.raises(ValueError):
                register_admin(runtime.c, 'operator@example.invalid', authority=operator,
                    ownership_evidence='fictional-owned-inbox', now_ms=NOW + 2)
        finally:
            runtime.stop()
