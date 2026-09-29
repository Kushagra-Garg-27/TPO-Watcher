from typing import Protocol, List, Optional, Dict, Any
from datetime import datetime

class UserRepositoryProtocol(Protocol):
    def get_by_id(self, user_id: int) -> Optional[Dict[str, Any]]: ...
    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]: ...
    def create_user(
        self, 
        email: str, 
        graduation_year: int, 
        branch_canonical: str,
        pref_internship: bool = True,
        pref_placement: bool = True,
        pref_ppo: bool = True
    ) -> int: ...
    def set_verified(self, user_id: int) -> None: ...
    def set_unsubscribed(self, user_id: int) -> None: ...
    def get_active_subscribers_for_branch(
        self, 
        branch_canonical: str, 
        graduation_year: int = 2028
    ) -> List[Dict[str, Any]]: ...
    def get_preferences(self, user_id: int) -> Optional[Dict[str, Any]]: ...
    def update_preferences(
        self, 
        user_id: int, 
        pref_internship: bool, 
        pref_placement: bool, 
        pref_ppo: bool
    ) -> None: ...

class TokenRepositoryProtocol(Protocol):
    def create_token(
        self, 
        user_id: int, 
        token_hash: str, 
        token_type: str, 
        expires_at: datetime
    ) -> int: ...
    def get_valid_token(
        self, 
        token_hash: str, 
        token_type: str
    ) -> Optional[Dict[str, Any]]: ...
    def mark_token_used(self, token_id: int) -> None: ...
    def invalidate_user_tokens(self, user_id: int, token_type: str) -> None: ...

class DeliveryRepositoryProtocol(Protocol):
    def enqueue_deliveries(self, deliveries: List[Dict[str, Any]]) -> int: ...
    def claim_pending_batch(
        self, 
        batch_size: int = 25, 
        lease_seconds: int = 300
    ) -> List[Dict[str, Any]]: ...
    def mark_sent(self, delivery_id: int) -> None: ...
    def release_failed(
        self, 
        delivery_id: int, 
        error_message: str, 
        terminal: bool = False
    ) -> None: ...
    def recover_stale_leases(self) -> int: ...
    def cancel_pending_deliveries_for_user(self, user_id: int) -> int: ...
    def get_delivery_stats(self) -> Dict[str, int]: ...
