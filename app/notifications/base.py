from abc import ABC, abstractmethod
from typing import List, Tuple
from app.tpo.models import CompanyRecord

class NotificationProvider(ABC):
    @abstractmethod
    def send_new_company_notification(self, company: CompanyRecord, target_match: bool) -> bool:
        """Sends notification for a new company. Returns True if successful."""
        pass

    @abstractmethod
    def send_update_notification(self, company: CompanyRecord, changes: List[str]) -> bool:
        """Sends notification for an updated company. Returns True if successful."""
        pass
