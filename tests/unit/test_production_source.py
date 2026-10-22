"""Fictional browser boundary exercises the trusted adapter without network."""
import pytest
from app.tpo.client import AuthenticationError
from app.tpo.listing import ListingContract, ListingObservation
from app.tpo.models import CompanyRecord


def contract():
    return ListingContract('vierp', 'VIT', 2028, 'operator-reviewed-source-scope', 60000)


class Auth:
    context = object()
    async def get_valid_page(self): return object()
    async def login(self): self.refreshed = getattr(self, 'refreshed', 0) + 1
    async def stop(self): self.closed = True


class BrowserClient:
    outcomes = []
    def __init__(self, context, page, *, listing_contract, clock):
        self.contract, self.clock = listing_contract, clock
    async def fetch_listing(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception): raise outcome
        return ListingObservation(tuple(outcome), self.contract, self.clock(), False)


@pytest.mark.asyncio
async def test_expired_session_has_one_bounded_refresh_and_preserves_duplicate_names():
    from app.tpo.production_source import AuthenticatedVIERPSource, SourceAuthorization
    auth = Auth()
    BrowserClient.outcomes = [AuthenticationError('expired'), [CompanyRecord(id='1', company='Same'), CompanyRecord(id='2', company='Same')]]
    source = AuthenticatedVIERPSource(authorization=SourceAuthorization('approved-source', 'operator-authorization'),
        contract=contract(), clock=lambda:1000, auth=auth, client_factory=BrowserClient)
    result = await source.fetch_listing()
    assert [r.id for r in result.records] == ['1', '2'] and result.complete is False
    assert auth.refreshed == 1 and source.diagnostics()['recoveries'] == 1
    assert source.synthetic is False


@pytest.mark.asyncio
async def test_repeated_expiry_fails_without_empty_observation_or_unbounded_login():
    from app.tpo.production_source import AuthenticatedVIERPSource, SourceAuthorization
    auth = Auth(); BrowserClient.outcomes = [AuthenticationError('expired'), AuthenticationError('still expired')]
    source = AuthenticatedVIERPSource(authorization=SourceAuthorization('approved-source', 'operator-authorization'),
        contract=contract(), clock=lambda:1000, auth=auth, client_factory=BrowserClient)
    with pytest.raises(AuthenticationError): await source.fetch_listing()
    assert auth.refreshed == 1 and source.diagnostics()['errors'] == 1
    assert source.diagnostics()['last_success_ms'] is None


@pytest.mark.asyncio
async def test_malformed_source_never_attempts_auth_recovery():
    from app.tpo.production_source import AuthenticatedVIERPSource, SourceAuthorization
    auth = Auth(); BrowserClient.outcomes = [ValueError('malformed private response')]
    source = AuthenticatedVIERPSource(authorization=SourceAuthorization('approved-source', 'operator-authorization'),
        contract=contract(), clock=lambda:1000, auth=auth, client_factory=BrowserClient)
    with pytest.raises(ValueError): await source.fetch_listing()
    assert not hasattr(auth, 'refreshed')
    assert source.diagnostics()['last_error'] == 'SOURCE_INVALID'


def test_production_source_requires_explicit_authorization():
    from app.tpo.production_source import AuthenticatedVIERPSource
    with pytest.raises(ValueError): AuthenticatedVIERPSource(authorization=None, contract=contract(), clock=lambda:1000)
