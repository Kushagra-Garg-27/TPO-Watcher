"""Explicit local v2 orchestration through existing watcher/fanout/worker boundaries.

No schema installation or production transport. Every instance requires the
installed disposable isolation guard and an already-created S1 schema.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
from pathlib import Path

from app.database.notification_migrator import atomic, verify_schema
from app.database.event_repository import EventRepository, FakeTransport, Fenced, PolicyDenied
from app.tpo.models import CompanyRecord
from app.tpo.normalization import registration_values, registration_conflicts
from app.notifications.opportunity_renderer import render_opportunity


@dataclass(frozen=True)
class V2Options:
    writer: bool = False
    fanout: bool = False
    drain: bool = False
    transport: bool = False
    info_ttl_ms: int | None = None
    idle_wait_ms: int = 100

    def __post_init__(self):
        if any(type(getattr(self,k)) is not bool for k in ("writer","fanout","drain","transport")):
            raise PolicyDenied("explicit boolean v2 switches required")
        if self.info_ttl_ms is not None and (type(self.info_ttl_ms) is not int or not 1<=self.info_ttl_ms<=86400000):
            raise PolicyDenied("bounded explicit fictional INFO lifetime required")
        if type(self.idle_wait_ms) is not int or not 10<=self.idle_wait_ms<=1000:
            raise PolicyDenied("finite non-spinning drain wait required")


@dataclass(frozen=True)
class TransportResult:
    kind: str
    ended: bool
    def __post_init__(self):
        if self.kind not in ("ACCEPTED","NOT_ACCEPTED","UNKNOWN") or type(self.ended) is not bool:
            raise ValueError("typed finalized transport outcome/end evidence required")


class NotificationPipeline:
    def __init__(self,repository,*,transport=None,options=None,renderer=render_opportunity,role=None,controller=None):
        if not isinstance(repository,EventRepository):
            raise PolicyDenied("explicit disposable event repository required")
        self.repository=repository
        verify_schema(repository.c)
        self._database_path=Path(repository.c.execute("PRAGMA database_list").fetchone()[2]).resolve()
        self.schema_installed=True
        self.options=options or V2Options()
        if not isinstance(self.options,V2Options):
            raise PolicyDenied("explicit v2 options required")
        from app.monitoring.runtime_supervisor import current_runtime
        if current_runtime(self._database_path) is not None and self.options.writer:
            raise PolicyDenied('only the supervisor-owned pipeline may write source observations')
        if self.options.fanout and self.options.info_ttl_ms is None:
            raise PolicyDenied("choose a fictional INFO lifetime for local fanout")
        if self.options.transport and (not isinstance(transport,FakeTransport) or not callable(getattr(transport,"submit_message",None))):
            raise PolicyDenied("only an explicit fictional message transport is permitted")
        self.transport,self.renderer=transport,renderer
        self.repository.record_mode=True
        self.repository.info_ttl_ms=self.options.info_ttl_ms
        self.faults={}
        self.intake=None
        self.outbox_handles={}
        self.closed=False
        self.last_events=[]
        self.runtime_owner=None
        self.listing_contract=repository._listing_policy()
        from app.notifications.transport_arbiter import RuntimeController,get_state,AuthorityGateway
        if role not in (None,"controller","client"):
            raise PolicyDenied("explicit controller/client role required")
        # Preserve the older isolated, single-process fixture. Once shared R2a
        # control exists, even an old caller attaches without global mutations.
        self.role=role or ("client" if get_state(repository,"r2a:controller") else "legacy_fixture")
        if get_state(repository,"r2a:controller") and self.options.transport and not isinstance(transport,AuthorityGateway):
            raise PolicyDenied("registered shared runtime requires bound authority transport")
        self.controller=controller
        if self.role=="controller":
            if not isinstance(controller,RuntimeController) or controller.repository is not repository:
                raise PolicyDenied("explicit elected controller required")
            self._set_modes()
        elif self.role=="legacy_fixture":
            if isinstance(transport,AuthorityGateway):raise PolicyDenied("shared transport requires explicit client/controller")
            self._set_modes()

    @property
    def database_path(self):
        # API hooks use their own thread-owned connection; compare immutable
        # binding metadata without touching the watcher thread's connection.
        return self._database_path

    def _set_modes(self):
        from app.notifications.transport_arbiter import get_state
        if self.role=="client":raise PolicyDenied("attached client cannot change runtime")
        if self.role=="controller":
            self.controller.enable(self.options)
            return
        with atomic(self.repository.c):
            if get_state(self.repository,"r2a:controller"):
                raise PolicyDenied("legacy fixture cannot overwrite shared controller")
            desired=("V2" if self.options.writer else "OFF",
                     "V2" if self.options.fanout else "OFF",
                     "V2" if self.options.drain and self.options.transport else "OFF")
            row=self.repository._runtime()
            changed=desired!=(row["writer_mode"],row["fanout_mode"],row["send_mode"])
            self.repository.c.execute("""UPDATE notification_runtime SET writer_mode=?,fanout_mode=?,send_mode=?,
                epoch=epoch+? WHERE singleton=1""",(*desired,int(changed)))

    def bind_legacy(self,db):
        if Path(db.db_path).resolve()!=self.database_path:
            raise PolicyDenied("watcher and v2 repository must use the same disposable database")
        db.users.bind_notification_v2(self)
        db.tokens.bind_notification_v2(self)

    def consent_repository(self,connection):
        repo=EventRepository(connection,clock=self.repository.clock,record_mode=True,info_ttl_ms=self.options.info_ttl_ms)
        if Path(connection.execute("PRAGMA database_list").fetchone()[2]).resolve()!=self.database_path:
            raise PolicyDenied("consent transaction belongs to another database")
        return repo

    def bind_listing_contract(self,contract):
        from app.tpo.listing import ListingContract
        if not isinstance(contract,ListingContract):
            raise PolicyDenied("explicit listing contract required")
        encoded=json.dumps(contract.to_wire(),sort_keys=True)
        with atomic(self.repository.c):
            old=self.repository._one("SELECT value FROM system_state WHERE key='notification:listing-contract'")
            if old and old["value"]!=encoded:
                raise PolicyDenied("installed listing contract cannot silently change")
            self.repository.c.execute("""INSERT INTO system_state(key,value,updated_at)
                VALUES('notification:listing-contract',?,?) ON CONFLICT(key) DO NOTHING""",
                (encoded,str(self.repository.now())))
        self.listing_contract=contract

    @staticmethod
    def _deadline(record,client):
        # Scheduler timezone is never consulted. Trust is explicitly supplied by
        # the fictional source adapter; contradictory candidates remain unverified.
        name=getattr(client,"source_timezone",None)
        evidence=getattr(client,"source_timezone_evidence",None)
        if not name or not isinstance(evidence,str) or not evidence.strip():
            return None,None
        values=registration_values(record.model_dump())
        if any(values[k].status=="unparsed" for k in ("regEnddate","regEndtime","regEnddatenew")):
            return None,None
        if "regEnd" in registration_conflicts(record.model_dump()):
            return None,None
        primary,clock,alternate=(values.get(k) for k in ("regEnddate","regEndtime","regEnddatenew"))
        if primary is None:return None,None
        try:
            zone=ZoneInfo(name)
            candidates=[]
            if primary.status=="datetime":candidates.append(primary.value)
            if primary.status in ("date","datetime") and clock and clock.status=="time":
                day=primary.value.date() if primary.status=="datetime" else primary.value
                candidates.append(datetime.combine(day,clock.value))
            if not candidates:return None,None
            if alternate and alternate.status=="datetime":candidates.append(alternate.value)
            instants=[]
            for candidate in candidates:
                if candidate.tzinfo is None:
                    local=candidate.replace(tzinfo=zone,fold=0)
                    # A trusted zone does not choose an ambiguous fold or invent
                    # a local time that did not occur during a clock transition.
                    if local.utcoffset()!=candidate.replace(tzinfo=zone,fold=1).utcoffset():return None,None
                    if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)!=candidate:return None,None
                    candidate=local
                instants.append(candidate.astimezone(timezone.utc))
            if any(instant!=instants[0] for instant in instants[1:]):return None,None
            return int(instants[0].timestamp()*1000),evidence
        except (ValueError,OverflowError,ZoneInfoNotFoundError):
            return None,None

    async def poll(self,client):
        if self.runtime_owner is not None:self.runtime_owner.require_running('writer')
        if self.closed or not self.options.writer:
            raise PolicyDenied("v2 source writer is OFF")
        if getattr(client,"synthetic",False) is not True:
            raise PolicyDenied("only an explicitly fictional source adapter is permitted")
        repo=self.repository
        runtime=repo._runtime()
        if self.intake is None or runtime["intake_token"]!=self.intake.token or runtime["intake_lease_ms"] is None or runtime["intake_lease_ms"]<=repo.now():
            self.intake=repo.acquire_intake()
        batch=repo._runtime()["active_batch_id"]
        if batch is None:
            observed=repo.now()
            if self.listing_contract and callable(getattr(client,"fetch_listing",None)):
                from app.tpo.listing import ListingObservation
                observation=await client.fetch_listing()
                if (not isinstance(observation,ListingObservation) or observation.contract!=self.listing_contract
                        or observation.observed_ms>repo.now()
                        or repo.now()-observation.observed_ms>self.listing_contract.freshness_ms):
                    raise PolicyDenied("current matching listing evidence required")
                companies=list(observation.records)
                complete=observation.complete
                observed=observation.observed_ms
            else:
                companies=await client.fetch_companies()
                complete=getattr(client,"source_complete",None)
            if type(complete) is not bool or not isinstance(companies,list) or any(not isinstance(c,CompanyRecord) for c in companies):
                raise PolicyDenied("typed CompanyRecord list and explicit source-completeness marker required")
            if self.listing_contract and complete and not self.listing_contract.allow_complete:
                raise PolicyDenied("absence reconciliation requires an explicit complete-source contract")
            items=[]
            for company in companies:
                deadline,evidence=self._deadline(company,self.listing_contract or client)
                identity=company.id if company.id.strip() and company.id not in ("None","null") else None
                items.append(dict(id=identity,state="OBSERVED",record=company.model_dump(),
                                  verified_deadline=deadline is not None,deadline_ms=deadline,timezone_evidence=evidence))
                if self.listing_contract:
                    items[-1]["listing_observed_ms"]=observed
            batch=repo.admit(self.intake,admission_key=repo.new_key(),items=items,complete=complete,
                observed_ms=observed if self.listing_contract else None)
        status=repo._one("SELECT status FROM source_batches WHERE batch_id=?",(batch,))["status"]
        if status=="HELD_GLOBAL":raise PolicyDenied("source admission is held")
        self.last_events=[]
        for row in repo.c.execute("SELECT item_index FROM source_batch_items WHERE batch_id=? AND status='PENDING' ORDER BY item_index",(batch,)).fetchall():
            event=repo.apply_item(self.intake,batch,row[0],fault=self.faults.get("source"))
            if event:self.last_events.append(event)
        for row in repo.c.execute("SELECT company_key FROM source_reconciliation_work WHERE batch_id=? AND status='PENDING' ORDER BY company_key",(batch,)).fetchall():
            event=repo.reconcile(self.intake,batch,row[0])
            if event:self.last_events.append(event)
        result=repo.finalize_batch(self.intake,batch)
        self.intake=None
        if self.faults.get("after_source"):
            self.faults["after_source"]()
        return result

    def fanout_once(self,*,pages=20):
        if self.runtime_owner is not None:self.runtime_owner.require_running('drain')
        if self.closed or not self.options.fanout:return 0
        from app.subscribers.fanout import FanoutEngine
        return FanoutEngine.advance_v2(self.repository,self.outbox_handles,pages=pages,faults=self.faults)

    def render_delivery(self,handle,links):
        repo=self.repository
        delivery=repo._delivery(handle,("PROCESSING",))
        audience=repo._audience(delivery)
        intent,event=repo._context(delivery["intent_id"])
        if event is None:raise PolicyDenied("historical catchup rendering requires separate approval")
        snapshot=repo._load(intent["snapshot_payload_id"])
        rendered=self.renderer(snapshot=snapshot,reasons=json.loads(event["reasons_json"]),
            role=audience["role"],channel=delivery["channel"],branch=audience["selected_branch"],
            observed_ms=event["observed_ms"],persisted_ms=event["persisted_ms"],rendered_ms=repo.now(),links=links)
        return dict(delivery_id=handle.key,channel=delivery["channel"],recipient_key=delivery["recipient_key"],
                    subject=rendered.subject,html=rendered.html,observed_ms=rendered.observed_ms,
                    persisted_ms=rendered.persisted_ms,rendered_ms=rendered.rendered_ms)

    def recover_once(self):
        repo=self.repository
        for row in repo.c.execute("""SELECT delivery_id FROM notification_deliveries_v2
            WHERE status IN('PROCESSING','SENDING') AND (lease_ms<=? OR runtime_epoch<>?)
            ORDER BY delivery_id LIMIT 25""",(repo.now(),repo._runtime()["epoch"])).fetchall():
            try:
                repo.recover_delivery(row[0])
            except Fenced:
                # Selection is advisory. A competing worker can recover and
                # reclaim the row before this recovery transaction begins.
                continue

    def next_wait_ms(self):
        repo=self.repository
        gate=repo._one("SELECT state FROM smtp_account_gate WHERE account_key='account-1'")
        if self.closed or gate["state"]!="FREE":return self.options.idle_wait_ms
        earliest=repo.c.execute("SELECT min(next_ms) FROM notification_deliveries_v2 WHERE status='PENDING'").fetchone()[0]
        if earliest is None:return self.options.idle_wait_ms
        return max(10,min(self.options.idle_wait_ms,earliest-repo.now()))

    def shutdown(self):
        if self.closed:return
        self.closed=True
        repo=self.repository
        from app.notifications.transport_arbiter import get_state
        if self.role=="client" or (self.role=="legacy_fixture" and get_state(repo,"r2a:controller")):
            return
        if self.role=="controller":
            self.controller.shutdown()
            self.recover_once()
            return
        with atomic(repo.c):
            if get_state(repo,"r2a:controller"):
                return # A controller elected after the advisory read owns modes.
            repo.c.execute("UPDATE notification_runtime SET writer_mode='OFF',fanout_mode='OFF',send_mode='OFF',epoch=epoch+1")
            with repo._permit_lock:
                for permit in repo._permits.values():permit.retired=True
        self.recover_once()

