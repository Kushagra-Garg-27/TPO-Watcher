"""Explicitly authorized VIERP browser source; importing performs no I/O."""
import asyncio
from dataclasses import dataclass

from app.tpo.client import AuthenticationError, TPOClient
from app.tpo.listing import ListingContract, ListingObservation, SourceEvidenceError


@dataclass(frozen=True)
class SourceAuthorization:
    identity: str
    evidence_reference: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 256
               for v in (self.identity, self.evidence_reference)):
            raise ValueError('explicit operator source authorization required')


class AuthenticatedVIERPSource:
    """One signed dashboard response is always an incomplete observation.

    Source scope says where observations came from. It never establishes a
    student's enrollment or the scope of physical delivery authorization.
    """
    synthetic = False

    def __init__(self, *, authorization, contract, clock, auth=None,
                 client_factory=TPOClient, timeout_seconds=120):
        if (not isinstance(authorization, SourceAuthorization) or not isinstance(contract, ListingContract)
                or contract.allow_complete or not callable(clock)
                or type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 180):
            raise ValueError('authorized partial source and bounded wait required')
        if auth is None:
            from app.auth.manager import AuthManager
            auth = AuthManager()
        self.authorization, self.contract, self.clock = authorization, contract, clock
        self.auth, self.client_factory, self.timeout_seconds = auth, client_factory, timeout_seconds
        self._lock = asyncio.Lock()
        self._metrics = dict(observations=0, errors=0, recoveries=0, last_success_ms=None,
                             last_attempt_ms=None, last_error=None)

    async def fetch_listing(self):
        if self._lock.locked():
            raise SourceEvidenceError('source observation already in progress')
        async with self._lock:
            self._metrics['last_attempt_ms'] = self.clock()
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    result = await self._fetch()
                if (not isinstance(result, ListingObservation) or result.complete
                        or result.contract != self.contract or result.observed_ms > self.clock()
                        or self.clock()-result.observed_ms > self.contract.freshness_ms):
                    raise SourceEvidenceError('current typed incomplete source evidence required')
                self._metrics.update(observations=self._metrics['observations']+1,
                                     last_success_ms=result.observed_ms, last_error=None)
                return result
            except Exception as exc:
                category = ('AUTH_EXPIRED' if isinstance(exc, AuthenticationError) else
                            'SOURCE_TIMEOUT' if isinstance(exc, TimeoutError) else 'SOURCE_INVALID')
                self._metrics.update(errors=self._metrics['errors']+1, last_error=category)
                raise

    async def _fetch(self):
        page = await self.auth.get_valid_page()
        for attempt in range(2):
            client = self.client_factory(self.auth.context, page, listing_contract=self.contract, clock=self.clock)
            try:
                return await client.fetch_listing()
            except AuthenticationError:
                if attempt: raise
                await self.auth.login()
                page = await self.auth.get_valid_page()
                self._metrics['recoveries'] += 1

    def diagnostics(self):
        return dict(self._metrics)

    async def close(self):
        await self.auth.stop()
