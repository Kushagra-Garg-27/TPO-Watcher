"""Review-gated production account API. Operator authority defaults to denied."""
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.api.routes_preferences import get_current_user_id, get_db_repos
from app.api.email_service import get_email_service
from app.api.rate_limiter import check_rate_limit, get_client_ip
from app.database.models import ConfirmTokenPayload
from app.database.notification_compatibility import repository_unit
from app.database.account_schema import account_policy_enabled
from app.auth.accounts import AccountService, OperatorAuthorization, CONSENT_POLICY

router = APIRouter(prefix="/api/v1", tags=["Student Account"])


class ProfilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_canonical: str = Field(max_length=64)
    graduation_year: Literal[2028] = 2028


class ConsentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: StrictBool
    policy_version: Literal["student-email-v1"]


class DestinationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)


class ReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVED", "REJECTED", "SUSPENDED", "REVOKED"]
    evidence_reference: str = Field(min_length=1, max_length=200)
    institution: str | None = Field(default=None, max_length=64)
    cohort: int | None = None
    branch: str | None = Field(default=None, max_length=64)
    role: Literal["SUBSCRIBER", "ADMINISTRATOR"] = "SUBSCRIBER"


def require_same_origin(request):
    origin = request.headers.get("origin")
    expected=getattr(request.app.state,'account_public_origin',None) or str(request.base_url).rstrip('/')
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin.rstrip("/") != expected):
        raise HTTPException(403, "Same-origin account action required")


def account_mutation_guard(request, user_id):
    require_same_origin(request)
    check_rate_limit(request=request, key=f"account-update:{user_id}", max_requests=30, window_seconds=60)


@contextmanager
def account_unit(repos, *, user_id=None, mutation=False):
    with repository_unit(repos):
        users = repos[0]
        with users._get_conn() as connection:
            if not account_policy_enabled(connection):
                raise HTTPException(503, "Reviewed account service is not enabled")
            service = AccountService(connection)
            try:
                yield service
            except (ValueError, PermissionError) as error:
                raise HTTPException(422, str(error)) from None
            if mutation and user_id is not None:
                users._after_notification_mutation(connection, user_id)


@router.get("/account")
def account_status(user_id: int = Depends(get_current_user_id), repos=Depends(get_db_repos)):
    with account_unit(repos) as service:
        return service.status(user_id)


@router.patch("/account/profile")
def update_profile(request: Request, payload: ProfilePayload, user_id: int = Depends(get_current_user_id), repos=Depends(get_db_repos)):
    account_mutation_guard(request, user_id)
    with account_unit(repos, user_id=user_id, mutation=True) as service:
        service.update_profile(user_id, branch=payload.branch_canonical, cohort=payload.graduation_year)
        return service.status(user_id)


@router.post("/account/consent")
def update_consent(request: Request, payload: ConsentPayload, user_id: int = Depends(get_current_user_id), repos=Depends(get_db_repos)):
    account_mutation_guard(request, user_id)
    with account_unit(repos, user_id=user_id, mutation=True) as service:
        service.set_consent(user_id, payload.consent, payload.policy_version)
        return service.status(user_id)


@router.post("/account/destination")
def request_destination(request: Request, payload: DestinationPayload, user_id: int = Depends(get_current_user_id), repos=Depends(get_db_repos), email_service=Depends(get_email_service)):
    account_mutation_guard(request, user_id)
    check_rate_limit(request=request, key=f"destination:{user_id}:{get_client_ip(request)}", max_requests=5, window_seconds=900)
    with account_unit(repos, user_id=user_id, mutation=True) as service:
        raw = service.request_destination(user_id, payload.email)
        destination = service.status(user_id)["notification_email"]
    try:
        email_service.send_destination_verification_email(destination, raw)
    except Exception:
        # A transport hold or uncertain provider result never grants ownership.
        pass
    return {"status": "success", "message": "Request received. A destination verification link will be attempted when account mail is authorized."}


@router.post("/account/destination/confirm")
def confirm_destination(request: Request, payload: ConfirmTokenPayload, repos=Depends(get_db_repos)):
    require_same_origin(request)
    if "token" in request.query_params:
        raise HTTPException(400, "Token must be passed in JSON body")
    check_rate_limit(request=request, key=f"destination-confirm:{get_client_ip(request)}", max_requests=30, window_seconds=60)
    with account_unit(repos) as service:
        user_id = service.confirm_destination(payload.token)
        if user_id is None:
            raise HTTPException(400, "Invalid or expired destination link")
        repos[0]._after_notification_mutation(service.c, user_id)
    return {"status": "success", "message": "Notification destination verified. Enrollment review and consent are required for alerts."}


@router.post("/account/logout-all")
def logout_all(request: Request, response: Response, user_id: int = Depends(get_current_user_id), repos=Depends(get_db_repos)):
    account_mutation_guard(request, user_id)
    with account_unit(repos) as service:
        service.terminate_sessions(user_id)
    from app.api.session import perform_logout
    return perform_logout(request, response, repos=repos)


@router.delete("/account")
def close_account(request: Request, response: Response, user_id: int = Depends(get_current_user_id), repos=Depends(get_db_repos)):
    account_mutation_guard(request, user_id)
    with account_unit(repos, user_id=user_id, mutation=True) as service:
        service.request_closure(user_id)
    from app.api.session import perform_logout
    perform_logout(request, response, repos=repos)
    return {"status": "success", "message": "Account access and consent revoked. Delivery facts and minimum audit evidence are retained; protected-record erasure requires operator review."}


def operator_authorization(request: Request):
    authorizer = getattr(request.app.state, "account_operator_authorizer", None)
    if authorizer is None:
        raise HTTPException(403, "Externally authorized operator review required")
    authority = authorizer(request)
    if type(authority) is not OperatorAuthorization:
        raise HTTPException(403, "Externally authorized operator review required")
    return authority


@router.post("/admin/accounts/{user_id}/review")
def review_account(user_id: int, payload: ReviewPayload, request: Request, authority=Depends(operator_authorization), repos=Depends(get_db_repos)):
    require_same_origin(request)
    with account_unit(repos, user_id=user_id, mutation=True) as service:
        service.review(user_id, payload.decision, authority=authority, institution=payload.institution,
            cohort=payload.cohort, branch=payload.branch, evidence_reference=payload.evidence_reference, role=payload.role)
        return service.status(user_id)
