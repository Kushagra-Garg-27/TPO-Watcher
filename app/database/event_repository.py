"""Guard-confined F-S0 transaction experiment, never application integration.

Synthetic bytes/keys and fake outcome evidence are not cryptography/provider proof.
All units own one configured disposable connection and BEGIN IMMEDIATE.
"""
from dataclasses import dataclass
import hashlib
import json
import sqlite3
from threading import RLock, get_ident
from time import perf_counter
import uuid
from weakref import WeakValueDictionary

from app.database.notification_migrator import atomic, payload, require_connection


class Fenced(RuntimeError):
    pass


class PolicyDenied(RuntimeError):
    pass


class SealBudgetExceeded(PolicyDenied):
    pass


class CommitUnknown(RuntimeError):
    def __init__(self, operation, key):
        self.operation, self.key = operation, key
        super().__init__("committed acknowledgment deliberately lost: " + operation)


@dataclass(frozen=True)
class Handle:
    kind: str
    key: object
    token: str
    generation: int
    epoch: int
    lease_ms: int
    gate_generation: int = 0
    submission_key: str = ""


@dataclass(frozen=True)
class OperatorApproval:
    """Fictional trusted operator adapter; no real signature/authentication."""
    action: str
    target: str
    reference: str


@dataclass
class Permit:
    invocation_id: str
    handle: Handle
    consumed: bool = False
    retired: bool = False


class FakeTransport:
    """A list append, with no SMTP/network library or provider interaction."""
    def __init__(self, outcome="ACCEPTED"):
        if outcome not in ("ACCEPTED", "NOT_ACCEPTED", "UNKNOWN"):
            raise ValueError("typed finalized fictional outcome required")
        self.outcome, self.calls = outcome, []

    def submit(self, invocation_id):
        self.calls.append(invocation_id)
        return self.outcome


class EventRepository:
    # Volatile fake permits are process-local. Sharing retirement across fixture
    # connections prevents a late unused permit outliving durable END evidence.
    _permit_lock = RLock()
    _issued_permits = WeakValueDictionary()
    _retired_invocations = set()

    def __init__(self, conn, *, clock, record_mode=False, info_ttl_ms=None):
        require_connection(conn)
        from app.database.protected_payloads import require_binding
        codec = require_binding(conn)
        if codec and not conn.in_transaction: codec.warm(conn)
        self.c, self.clock = conn, clock
        self._connection_thread=get_ident()
        self._database_path=conn.execute("PRAGMA database_list").fetchone()[2]
        self.record_mode, self.info_ttl_ms = record_mode, info_ttl_ms
        self._permits = {}
        self.transport_authority = None
        self.transport_slot = None

    @staticmethod
    def new_key():
        return uuid.uuid4().hex

    def now(self):
        value = self.clock()
        if type(value) is not int or value < 0:
            raise PolicyDenied("supplied nonnegative integer clock required")
        return value

    def _one(self, sql, values=()):
        row = self.c.execute(sql, values).fetchone()
        return dict(row) if row is not None else None

    def _change(self, sql, values=()):
        if self.c.execute(sql, values).rowcount != 1:
            raise Fenced("CAS must change exactly one row")

    def _runtime(self):
        row = self._one("SELECT * FROM notification_runtime WHERE singleton=1")
        if row is None or row["config_version"] != "F-S0" or row["policy_version"] != "F-S0":
            raise PolicyDenied("fictional runtime/version required")
        return row

    def _mode(self, mode):
        row = self._runtime()
        if row[mode + "_mode"] != "V2":
            raise PolicyDenied(mode + " is OFF")
        if mode=="send":self._shared_transport()
        return row

    def _shared_transport(self):
        """Durable registration fences every producer, including older objects."""
        shared=self._one("SELECT 1 FROM system_state WHERE key='r2a:controller'") is not None
        if shared:
            if not self.transport_authority:raise Fenced("registered shared runtime requires bound authority")
            self.check_transport_authority()
        return shared

    def activate(self):
        # Explicit fixture action, never called on import or application startup.
        with atomic(self.c):
            if self._one("SELECT 1 FROM system_state WHERE key='r2a:controller'"):
                raise PolicyDenied("designated RuntimeController owns shared modes")
            self.c.execute("UPDATE notification_runtime SET writer_mode='V2',fanout_mode='V2',send_mode='V2'")

    def bump_epoch(self):
        with atomic(self.c):
            if self._one("SELECT 1 FROM system_state WHERE key='r2a:controller'"):
                raise PolicyDenied("designated RuntimeController owns shared epoch")
            self.c.execute("UPDATE notification_runtime SET epoch=epoch+1")

    def _lease(self, milliseconds):
        if type(milliseconds) is not int or not 1 <= milliseconds <= 60000:
            raise PolicyDenied("finite fictional lease required")
        return self.now() + milliseconds

    def _intake(self, h, batch=None):
        r = self._mode("writer")
        if (h.kind != "INTAKE" or h.token != r["intake_token"] or h.generation != r["intake_generation"]
                or h.epoch != r["epoch"] or r["intake_lease_ms"] is None
                or r["intake_lease_ms"] <= self.now() or (batch is not None and r["active_batch_id"] != batch)):
            raise Fenced("stale intake ownership")
        return r

    def acquire_intake(self, *, lease_ms=1000):
        with atomic(self.c):
            r = self._mode("writer")
            if r["intake_token"] is not None and r["intake_lease_ms"] > self.now():
                raise Fenced("intake already owned; no extra FETCH")
            token, gen, lease = self.new_key(), r["intake_generation"] + 1, self._lease(lease_ms)
            self._change("""UPDATE notification_runtime SET intake_token=?,intake_generation=?,intake_lease_ms=?
                WHERE singleton=1 AND intake_generation=? AND intake_token IS ?""",
                (token, gen, lease, r["intake_generation"], r["intake_token"]))
            return Handle("INTAKE", r["active_batch_id"], token, gen, r["epoch"], lease)

    def admit(self, h, *, admission_key, items, complete, max_items=2000, observed_ms=None):
        # T1 validation is pure; data is fictional and retained without truncation.
        if type(complete) is not bool or type(max_items) is not int or max_items < 1:
            raise PolicyDenied("explicit completeness/bounded intake contract required")
        if observed_ms is not None and (type(observed_ms) is not int or not 0<=observed_ms<=self.now()):
            raise PolicyDenied("explicit nonfuture observation clock required")
        body = json.dumps(items, sort_keys=True, separators=(",", ":"))
        seen = [x.get("id") for x in items]
        bad_ids = any(not isinstance(x, str) or not x for x in seen) or len(set(x for x in seen if isinstance(x,str))) != len(seen)
        global_hold = bad_ids or len(items) > max_items
        from app.database.protected_payloads import require_binding
        codec = require_binding(self.c)
        if codec and not self._one('SELECT 1 FROM source_batches WHERE admission_key=?',(admission_key,)):
            codec.prepare('SOURCE',items,{'admission':admission_key})
            for item in items:codec.prepare('SOURCE',item,{'source':item.get('id')})
        with atomic(self.c):
            old = self._one("SELECT * FROM source_batches WHERE admission_key=?", (admission_key,))
            if old:
                if self._load(old["payload_id"]) != items:
                    raise PolicyDenied("admission operation key reused with different content")
                if old["completeness"] != ("VERIFIED_COMPLETE" if complete else "PARTIAL"):
                    raise PolicyDenied("admission operation key reused with different completeness")
                if observed_ms is not None and old["observed_ms"]!=observed_ms:
                    raise PolicyDenied("admission operation key reused with different observation time")
                # max_items is a first-admission capacity bound, not new source
                # evidence and cannot retroactively alter a committed batch.
                return old["batch_id"]
            r = self._intake(h)
            if r["active_batch_id"] is not None:
                raise Fenced("RESUME active batch; FETCH admission forbidden")
            observed=self.now() if observed_ms is None else observed_ms
            policy=self._listing_policy()
            if policy is not None:
                if complete and not policy.allow_complete:
                    raise PolicyDenied("installed source contract does not authorize a complete census")
                if self.now()-observed>policy.freshness_ms:
                    raise PolicyDenied("source observation expired before admission")
                if complete and any(json.loads(row[0])["observed_ms"]>observed for row in self.c.execute(
                        "SELECT value FROM system_state WHERE key LIKE 'notification:listing:%'")):
                    raise PolicyDenied("older complete census cannot replace newer listing evidence")
            batch = self.new_key()
            source = payload(self.c, "SOURCE", items, self.now(),owner={'admission':admission_key})
            allowed = int(complete and not global_hold)
            self.c.execute("""INSERT INTO source_batches
                (batch_id,source_key,intake_generation,admission_key,payload_id,observed_ms,admitted_ms,
                 source_contract,normalizer_version,config_version,policy_version,record_count,seen_digest,
                 completeness,reconciliation_allowed,status,hold_reason)
                VALUES(?,?,?,?,?,?,?,'F-S0','F-S0','F-S0','F-S0',?,?,?,?,?,?)""",
                (batch,"fictional",h.generation,admission_key,source,observed,self.now(),len(items),
                 hashlib.sha256(body.encode()).hexdigest(),"VERIFIED_COMPLETE" if complete else "PARTIAL",
                 allowed,"HELD_GLOBAL" if global_hold else "ACCEPTED","IDENTITY_OR_SIZE" if global_hold else None))
            for index,item in enumerate(items):
                item_payload = payload(self.c,"SOURCE",item,self.now(),owner={'source':item.get('id')})
                self.c.execute("""INSERT INTO source_batch_items(batch_id,item_index,trusted_company_id,payload_id,status)
                    VALUES(?,?,?,?,'PENDING')""", (batch,index,item.get("id") if isinstance(item.get("id"),str) else None,item_payload))
            if allowed:
                for state in self.c.execute("SELECT company_key,legacy_company_id FROM opportunity_state WHERE source_key='fictional'").fetchall():
                    if state["legacy_company_id"] not in seen:
                        self.c.execute("INSERT INTO source_reconciliation_work(batch_id,company_key,status) VALUES(?,?,'PENDING')", (batch,state["company_key"]))
            self._change("UPDATE notification_runtime SET active_batch_id=? WHERE singleton=1 AND intake_token=? AND intake_generation=?", (batch,h.token,h.generation))
            self._intake(h,batch)
            return batch

    def _load(self, key, *, owner=None):
        from app.database.protected_payloads import require_binding
        codec = require_binding(self.c)
        if codec:return codec.load(self.c,key,owner=owner)
        row = self._one("SELECT encrypted_bytes FROM protected_payloads WHERE payload_id=?", (key,))
        if not row:
            raise PolicyDenied("missing/erased fictional payload")
        return json.loads(row["encrypted_bytes"])

    def _batch(self, h, batch):
        self._intake(h,batch)
        row = self._one("SELECT * FROM source_batches WHERE batch_id=?", (batch,))
        if row is None or row["status"] not in ("ACCEPTED","INGESTING"):
            raise PolicyDenied("batch held or terminal")
        if row["status"] == "ACCEPTED":
            self.c.execute("UPDATE source_batches SET status='INGESTING' WHERE batch_id=?", (batch,))
        return row

    def inspect_item(self, batch, index):
        row = self._one("SELECT status,event_id FROM source_batch_items WHERE batch_id=? AND item_index=?", (batch,index))
        return row["event_id"] if row and row["status"] == "APPLIED" else None

    def _material(self,data):
        if self.record_mode:
            return {k:data.get(k) for k in ("state","record","deadline_ms","verified_deadline","timezone_evidence")}
        return {k: data.get(k) for k in ("state","branch","placement","deadline_ms","verified_deadline")}

    def _listing_policy(self):
        row=self._one("SELECT value FROM system_state WHERE key='notification:listing-contract'")
        if row is None:return None
        from app.tpo.listing import ListingContract
        return ListingContract(**json.loads(row["value"]))

    def _source_recent(self,event):
        policy=self._listing_policy()
        if policy is None:return True
        if event is None:return False
        row=self._one("SELECT value FROM system_state WHERE key=?",
            ("notification:listing:"+event["company_key"],))
        if row is None:return False
        evidence=json.loads(row["value"])
        observed=evidence.get("observed_ms")
        return (type(observed) is int and observed<=self.now()
                and self.now()-observed<=policy.freshness_ms)

    def _current_source(self,event):
        if self._listing_policy() is None:return True
        if event is None:return False
        head=self._one("SELECT head_event_id FROM opportunity_state WHERE company_key=?",
            (event["company_key"],))
        # Revision identity, not value equality: A -> B -> A cannot resurrect A.
        if head is None or head["head_event_id"]!=event["event_id"]:return False
        if "NEW" in json.loads(event["reasons_json"]):
            snapshot=self._load(event["after_payload_id"])
            record=self._snapshot_record(snapshot)
            if (snapshot.get("state")=="ABSENT" or record is None
                    or str(record.isactive).strip().casefold()!="true"
                    or (event["window_quality"]=="VERIFIED_INSTANT" and event["deadline_ms"]<=self.now())):
                return False
        return True

    def _record_listing(self,company_key,data,batch):
        policy=self._listing_policy()
        if policy is None:return
        batch_observed=self._one("SELECT observed_ms FROM source_batches WHERE batch_id=?",(batch,))["observed_ms"]
        observed=data.get("listing_observed_ms",batch_observed)
        if type(observed) is not int or observed!=batch_observed:
            raise PolicyDenied("item and census observation clocks must agree")
        old=self._one("SELECT value FROM system_state WHERE key=?",("notification:listing:"+company_key,))
        if old and json.loads(old["value"])["observed_ms"]>observed:
            raise PolicyDenied("older listing evidence cannot replace a later observation")
        self.c.execute("""INSERT INTO system_state(key,value,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            ("notification:listing:"+company_key,json.dumps(dict(observed_ms=observed,
             state=data["state"],source=policy.source_namespace,institution=policy.institution,
             cohort=policy.cohort,evidence=policy.evidence_reference,batch=batch),sort_keys=True),str(self.now())))
        if 0<=self.now()-observed<=policy.freshness_ms:
            self.c.execute("""UPDATE notification_deliveries_v2 SET next_ms=?,reason_code=NULL
                WHERE company_key=? AND status='PENDING' AND reason_code='SOURCE_FRESHNESS_HOLD'""",
                (self.now(),company_key))

    def _valid_item(self,data):
        if self.record_mode:
            from app.tpo.models import CompanyRecord
            from app.tpo.normalization import tokenize_programmes,normalize_organizations
            if not isinstance(data,dict) or not isinstance(data.get("record"),dict):return False
            try:record=CompanyRecord(**data["record"])
            except (ValueError,TypeError):return False
            return (record.id==data.get("id") and bool(record.id) and record.id not in ("None","null")
                and data.get("state")=="OBSERVED" and bool(record.company.strip())
                and not tokenize_programmes(record.tpoprogram).malformed
                and not tokenize_programmes(record.programnew).malformed
                and not normalize_organizations(record.organization).malformed)
        # Deliberately small fictional contract, not a production TPO normalizer.
        return (isinstance(data,dict) and data.get("valid",True) is True
            and isinstance(data.get("state"),str) and 0<len(data["state"])<=256
            and isinstance(data.get("branch"),str) and 0<len(data["branch"])<=64
            and ("verified_deadline" not in data or type(data["verified_deadline"]) is bool)
            and ("deadline_ms" not in data or (data["deadline_ms"] is None and data.get("verified_deadline") is False)
                 or (type(data["deadline_ms"]) is int and data["deadline_ms"]>=0)))

    def _outboxes(self, intent, *, held_channel=None):
        for channel in ("ADMIN","SUBSCRIBER"):
            self.c.execute("""INSERT INTO event_outbox
                (intent_id,channel,status,runtime_epoch,next_ms,max_failures,created_ms,hold_reason)
                VALUES(?,?,?,?,?,3,?,?)""",
                (intent,channel,"HELD" if channel==held_channel else "PENDING",self._runtime()["epoch"],
                 self.now(),self.now(),"FICTIONAL_POLICY_HOLD" if channel==held_channel else None))

    def _transition(self, batch, item_index, company_id, data, *, origin, fault=None):
        def checkpoint(name):
            if fault: fault(name)
        encoded = json.dumps(self._material(data), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        old = self._one("SELECT * FROM opportunity_state WHERE legacy_company_id=?", (company_id,))
        previous_legacy=self._one("SELECT * FROM companies WHERE id=?", (company_id,))
        new_arrival = previous_legacy is None
        changes=None
        if self.record_mode:
            from app.monitoring.detector import CompanyDetector
            from app.tpo.models import CompanyRecord
            current=CompanyRecord(**data["record"])
            prior_snapshot=self._load(old["snapshot_payload_id"]) if old else None
            prior_record=self._snapshot_record(prior_snapshot) if prior_snapshot else None
            compared=dict(previous_legacy or {})
            if prior_record is not None:compared=self._projection(prior_record)
            changes=["NEW"] if new_arrival else CompanyDetector.meaningful_changes(compared,current)
            if prior_snapshot and prior_snapshot.get("state","OBSERVED")!=data["state"]:
                changes.append("Source visibility changed; employer registration status is unconfirmed.")
            if prior_snapshot and "record" in prior_snapshot and any(prior_snapshot.get(k)!=data.get(k) for k in ("deadline_ms","verified_deadline","timezone_evidence")):
                changes.append("Trusted registration evidence changed.")
            projection=self._projection(current)
            columns=list(projection)
            self.c.execute("INSERT INTO companies("+",".join(columns)+",first_seen_at,last_seen_at) VALUES("+
                ",".join("?" for _ in range(len(columns)+2))+") ON CONFLICT(id) DO UPDATE SET "+
                ",".join(k+"=excluded."+k for k in columns if k!="id")+",last_seen_at=excluded.last_seen_at",
                (*projection.values(),self._projection_time(),self._projection_time()))
        else:
            self.c.execute("""INSERT INTO companies(id,company,first_seen_at,last_seen_at,raw_data_json)
                VALUES(?, 'Fictional company',?,?,?)
                ON CONFLICT(id) DO UPDATE SET last_seen_at=excluded.last_seen_at,raw_data_json=excluded.raw_data_json""",
                (company_id,str(self.now()),str(self.now()),encoded))
        checkpoint("legacy")
        if not old:
            company_key = self.new_key()
            baseline = payload(self.c,"SNAPSHOT",{},self.now(),owner={'company':company_id})
            self.c.execute("""INSERT INTO opportunity_state
                (company_key,source_key,legacy_company_id,revision,snapshot_payload_id,material_digest,
                 normalizer_version,matcher_version,updated_ms) VALUES(?,'fictional',?,0,?,'','F-S0','F-S0',?)""",
                (company_key,company_id,baseline,self.now()))
            old = self._one("SELECT * FROM opportunity_state WHERE company_key=?", (company_key,))
        checkpoint("baseline")
        self._record_listing(old["company_key"],data,batch)
        if self.record_mode and self._listing_policy() is not None and not changes:
            # Authentic extra fields are retained by CompanyRecord; changes to
            # these facts must not leave an old queued notice current.
            prior_record=self._snapshot_record(self._load(old["snapshot_payload_id"]))
            if prior_record is not None and prior_record.model_dump()!=current.model_dump():
                from app.tpo.normalization import registration_values
                ignored={"regStartdate","regStarttime","regEnddate","regEndtime","regStartdatenew",
                         "regEnddatenew","tpoprogram","programnew","organization"}
                if any(prior_record.model_dump().get(k)!=current.model_dump().get(k)
                       for k in set(prior_record.model_dump())|set(current.model_dump()) if k not in ignored):
                    changes.append("Official opportunity details changed.")
        if (self.record_mode and not changes) or (not self.record_mode and old["material_digest"] == digest):
            return None
        before = self._load(old["snapshot_payload_id"])
        after = payload(self.c,"SNAPSHOT",self._material(data),self.now(),owner={'company':company_id})
        event, intent_id, revision = self.new_key(), self.new_key(), old["revision"] + 1
        verified = data.get("verified_deadline", True) is True
        deadline = data.get("deadline_ms", self.now()+100000) if verified else None
        self.c.execute("""INSERT INTO opportunity_events
            (event_id,company_key,revision,previous_revision,previous_event_id,batch_id,item_index,origin,
             before_payload_id,after_payload_id,reasons_json,observed_ms,persisted_ms,normalizer_version,
             matcher_version,window_quality,deadline_ms,zone_evidence_reference)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'F-S0','F-S0',?,?,?)""",
            (event,old["company_key"],revision,old["revision"] if old["revision"] else None,old["head_event_id"],
             batch,item_index,origin,old["snapshot_payload_id"],after,
             json.dumps(changes if self.record_mode else ["NEW" if new_arrival else "UPDATE"]),
             self._one("SELECT observed_ms FROM source_batches WHERE batch_id=?",(batch,))["observed_ms"],self.now(),
             "VERIFIED_INSTANT" if verified else "SOURCE_LOCAL" if self.record_mode else "UNKNOWN",deadline,
             data.get("timezone_evidence") if self.record_mode and verified else "FICTIONAL_EXPLICIT_ZONE" if verified else None))
        checkpoint("event")
        for side,representation in (("BEFORE",before),("AFTER",data)):
            if self.record_mode:
                record=self._snapshot_record(representation)
                if record is not None and representation.get("state")!="ABSENT":
                    for branch in self._branches(record):
                        self.c.execute("INSERT INTO event_routes VALUES(?,?,'VIT',?,'B1F:explicit-VIT')",(event,side,branch.value))
            elif representation.get("branch") and representation.get("state") != "ABSENT":
                self.c.execute("INSERT INTO event_routes VALUES(?,?,'VIT',?,'F-S0:explicit-VIT')", (event,side,representation["branch"]))
        checkpoint("routes")
        self.c.execute("""INSERT INTO notification_intents
            (intent_id,company_key,kind,source_event_id,snapshot_payload_id,policy_version,config_version,created_ms)
            VALUES(?,?,'SOURCE_EVENT',?,?,'F-S0','F-S0',?)""", (intent_id,old["company_key"],event,after,self.now()))
        checkpoint("intent")
        self._outboxes(intent_id,held_channel="ADMIN" if data.get("hold_admin") else None)
        checkpoint("outboxes")
        self._change("""UPDATE opportunity_state SET revision=?,head_event_id=?,snapshot_payload_id=?,
            material_digest=?,last_batch_id=?,updated_ms=? WHERE company_key=? AND revision=? AND head_event_id IS ?""",
            (revision,event,after,digest,batch,self.now(),old["company_key"],old["revision"],old["head_event_id"]))
        checkpoint("head")
        return event

    @staticmethod
    def _projection(record):
        return dict(id=record.id,company=record.company,company_code=record.company_code,
            registration_start=record.regStartdate,registration_end=record.regEnddate,
            max_package=record.maxPackage,min_package=record.minPackage,placement_type=record.placementtype,
            academic_year=record.academicyear,company_type=record.companytype,internship_type=record.internshiptype,
            skills=record.skill,eligible_programs=record.tpoprogram or record.programnew,
            organizations=record.organization,is_active=record.isactive,
            raw_data_json=json.dumps(record.model_dump(),sort_keys=True))

    def _projection_time(self):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.now()/1000,timezone.utc).isoformat()

    def _prepare_transition(self, data, company_id):
        from app.database.protected_payloads import require_binding
        codec=require_binding(self.c)
        if codec:
            if self.c.in_transaction:raise PolicyDenied('protected preparation requires no write unit')
            if not self._one('SELECT 1 FROM opportunity_state WHERE legacy_company_id=?',(company_id,)):
                codec.prepare('SNAPSHOT',{}, {'company':company_id})
            codec.prepare('SNAPSHOT',self._material(data), {'company':company_id})

    @staticmethod
    def _snapshot_record(snapshot):
        from app.tpo.models import CompanyRecord
        if not isinstance(snapshot,dict):return None
        try:
            data=snapshot.get("record")
            if data is None and snapshot.get("raw_data_json"):data=json.loads(snapshot["raw_data_json"])
            return CompanyRecord(**data) if isinstance(data,dict) else None
        except (ValueError,TypeError,RecursionError):return None

    @staticmethod
    def _branches(record):
        from app.subscribers.canonical import extract_eligible_canonical_branches
        return extract_eligible_canonical_branches(record.tpoprogram,record.programnew,record.organization)

    def apply_item(self, h, batch, index, *, fault=None, lose_ack=False):
        pending=self._one('SELECT * FROM source_batch_items WHERE batch_id=? AND item_index=?',(batch,index))
        if pending and pending['status']=='PENDING':
            self._prepare_transition(self._load(pending['payload_id']),pending['trusted_company_id'])
        with atomic(self.c):
            item = self._one("SELECT * FROM source_batch_items WHERE batch_id=? AND item_index=?", (batch,index))
            if not item: raise PolicyDenied("missing indexed source item")
            if item["status"] != "PENDING": return item["event_id"]
            self._batch(h,batch)
            first = self.c.execute("SELECT min(item_index) FROM source_batch_items WHERE batch_id=? AND status='PENDING'",(batch,)).fetchone()[0]
            if first != index: raise Fenced("ordered source item required")
            data = self._load(item["payload_id"])
            duplicate_identity=self.c.execute("""SELECT count(*) FROM source_batch_items
                WHERE batch_id=? AND trusted_company_id=?""",(batch,item["trusted_company_id"])).fetchone()[0]>1
            if not item["trusted_company_id"] or duplicate_identity or not self._valid_item(data):
                self.c.execute("""UPDATE source_batch_items SET status='QUARANTINED',error_category='FICTIONAL_INVALID',
                    completed_ms=? WHERE batch_id=? AND item_index=?""", (self.now(),batch,index))
                self.c.execute("UPDATE source_batches SET reconciliation_allowed=0 WHERE batch_id=?", (batch,))
                self.c.execute("""UPDATE source_reconciliation_work SET status='HELD',completed_ms=?,reason='QUARANTINE'
                    WHERE batch_id=? AND status='PENDING'""", (self.now(),batch))
                event=None
            else:
                event=self._transition(batch,index,item["trusted_company_id"],data,origin="FETCH",fault=fault)
                company=self._one("SELECT company_key FROM opportunity_state WHERE legacy_company_id=?", (item["trusted_company_id"],))
                self.c.execute("""UPDATE source_batch_items SET status=?,event_id=?,company_key=?,completed_ms=?
                    WHERE batch_id=? AND item_index=? AND status='PENDING'""",
                    ("APPLIED" if event else "UNCHANGED",event,company["company_key"],self.now(),batch,index))
            self.c.execute("""UPDATE source_batches SET positive_cursor=
                (SELECT count(*) FROM source_batch_items WHERE batch_id=? AND status<>'PENDING'),
                reconcile_cursor=(SELECT count(*) FROM source_reconciliation_work WHERE batch_id=? AND status<>'PENDING')
                WHERE batch_id=?""", (batch,batch,batch))
            if fault: fault("checkpoint")
            self._intake(h,batch)
        if lose_ack: raise CommitUnknown("T2", (batch,index))
        return event

    def reconcile(self, h, batch, company_key):
        from app.database.protected_payloads import require_binding
        if require_binding(self.c):
            head=self._one('SELECT * FROM opportunity_state WHERE company_key=?',(company_key,))
            if head:
                record=self._snapshot_record(self._load(head['snapshot_payload_id']))
                if self.record_mode:
                    if record is None:raise PolicyDenied('absence cannot reconstruct unsupported legacy snapshot')
                    absent=dict(id=record.id,record=record.model_dump(),state='ABSENT',verified_deadline=False,deadline_ms=None,timezone_evidence=None)
                else:absent={'state':'ABSENT','branch':None}
                self._prepare_transition(absent,head['legacy_company_id'])
        with atomic(self.c):
            b=self._batch(h,batch)
            work=self._one("SELECT * FROM source_reconciliation_work WHERE batch_id=? AND company_key=?", (batch,company_key))
            if not work or work["status"]=="HELD": raise PolicyDenied("no authorized absence work")
            if work["status"] != "PENDING": return work["event_id"]
            if (b["completeness"]!="VERIFIED_COMPLETE" or not b["reconciliation_allowed"]
                    or self.c.execute("SELECT count(*) FROM source_batch_items WHERE batch_id=? AND status IN('PENDING','QUARANTINED')",(batch,)).fetchone()[0]
                    or self.c.execute("SELECT count(*) FROM source_batch_items WHERE batch_id=?",(batch,)).fetchone()[0]!=b["record_count"]):
                raise PolicyDenied("absence requires trustworthy complete positive accounting")
            old=self._one("SELECT legacy_company_id FROM opportunity_state WHERE company_key=?", (company_key,))
            absent={"state":"ABSENT","branch":None}
            if self.record_mode:
                head=self._one("SELECT snapshot_payload_id FROM opportunity_state WHERE company_key=?",(company_key,))
                snapshot=self._load(head["snapshot_payload_id"])
                record=self._snapshot_record(snapshot)
                if record is None:raise PolicyDenied("absence cannot reconstruct unsupported legacy snapshot")
                absent=dict(id=record.id,record=record.model_dump(),state="ABSENT",verified_deadline=False,deadline_ms=None,timezone_evidence=None)
            event=self._transition(batch,None,old["legacy_company_id"],absent,origin="RECONCILE")
            self.c.execute("""UPDATE source_reconciliation_work SET status=?,event_id=?,completed_ms=?
                WHERE batch_id=? AND company_key=?""", ("APPLIED" if event else "UNCHANGED",event,self.now(),batch,company_key))
            self.c.execute("UPDATE source_batches SET reconcile_cursor=reconcile_cursor+1 WHERE batch_id=?", (batch,))
            self._intake(h,batch)
            return event

    def finalize_batch(self, h, batch):
        with atomic(self.c):
            b=self._one("SELECT * FROM source_batches WHERE batch_id=?", (batch,))
            if b and b["status"] in ("COMPLETE","COMPLETE_WITH_HOLDS"): return b["status"]
            self._batch(h,batch)
            pending=self.c.execute("SELECT count(*) FROM source_batch_items WHERE batch_id=? AND status='PENDING'", (batch,)).fetchone()[0]
            work=self.c.execute("SELECT count(*) FROM source_reconciliation_work WHERE batch_id=? AND status='PENDING'", (batch,)).fetchone()[0]
            if pending or work: raise PolicyDenied("unfinished batch")
            held=(self.c.execute("SELECT count(*) FROM source_batch_items WHERE batch_id=? AND status='QUARANTINED'", (batch,)).fetchone()[0]
                  +self.c.execute("SELECT count(*) FROM source_reconciliation_work WHERE batch_id=? AND status='HELD'", (batch,)).fetchone()[0])
            status="COMPLETE_WITH_HOLDS" if held else "COMPLETE"
            self.c.execute("UPDATE source_batches SET status=?,completed_ms=? WHERE batch_id=?", (status,self.now(),batch))
            self._change("""UPDATE notification_runtime SET active_batch_id=NULL,intake_token=NULL,intake_lease_ms=NULL
                WHERE singleton=1 AND active_batch_id=? AND intake_token=? AND intake_generation=?""",(batch,h.token,h.generation))
            return status

    @staticmethod
    def _operator(approval, action, target):
        if not isinstance(approval,OperatorApproval) or approval.action!=action or approval.target!=str(target) or not approval.reference:
            raise PolicyDenied("scoped fictional operator approval required")

    def recover_batch(self,h,batch,approval):
        self._operator(approval,"RECOVER_BATCH",batch)
        with atomic(self.c):
            self._intake(h,batch)
            if self.c.execute("SELECT 1 FROM source_batches WHERE resolution_authorization=?", (approval.reference,)).fetchone():
                raise PolicyDenied("one-use recovery reference")
            self._change("""UPDATE source_batches SET status='INGESTING',resolution_authorization=?
                WHERE batch_id=? AND status='HELD_GLOBAL'""", (approval.reference,batch))

    def add_user(self, *, branch="CS", verified=1, active=1):
        with atomic(self.c):
            uid=self.c.execute("""INSERT INTO users(email,graduation_year,branch_canonical,is_verified,is_active,created_at)
                VALUES(?,2028,?,?,?,?)""",(self.new_key()+"@example.invalid",branch,verified,active,str(self.now()))).lastrowid
            self.c.execute("UPDATE subscriber_versions SET updated_ms=? WHERE user_id=?", (self.now(),uid))
            self.c.execute("INSERT INTO user_preferences VALUES(?,1,1,1,?)", (uid,str(self.now())))
            handle=self.new_key()
            self.c.execute("INSERT INTO recipient_handles VALUES(?,'SUBSCRIBER',?)",(handle,self.now()))
            self.c.execute("INSERT INTO recipient_associations(recipient_key,user_id) VALUES(?,?)",(handle,uid))
            return handle

    def add_admin(self):
        from app.database.protected_payloads import require_binding
        from app.database.key_provider import PayloadContext
        codec=require_binding(self.c)
        handle=self.new_key()
        if codec:
            context=PayloadContext(codec.namespace,handle,'RECIPIENT',hashlib.sha256(b'ADMIN:'+handle.encode()).hexdigest())
            sealed=codec.encrypt(context,{'destination':'fictional-admin@example.invalid'})
        with atomic(self.c):
            self.c.execute("INSERT INTO recipient_handles VALUES(?,'ADMIN',?)", (handle,self.now()))
            self.c.execute("INSERT INTO recipient_associations(recipient_key,protected_destination,key_reference) VALUES(?,?,?)", (handle,sealed.blob if codec else b'opaque-fictional-destination',sealed.reference if codec else 'fictional-key'))
            return handle

    def _outbox(self,h):
        runtime=self._mode("fanout")
        row=self._one("SELECT * FROM event_outbox WHERE intent_id=? AND channel=?", h.key)
        if (h.kind!="OUTBOX" or not row or row["status"]!="PROCESSING" or row["claim_token"]!=h.token
                or row["generation"]!=h.generation or row["runtime_epoch"]!=h.epoch or runtime["epoch"]!=h.epoch
                or row["lease_ms"] is None or row["lease_ms"]<=self.now()):
            raise Fenced("stale outbox ownership")
        return row

    def claim_outbox(self,intent_id,channel,*,lease_ms=1000):
        with atomic(self.c):
            runtime=self._mode("fanout")
            row=self._one("SELECT * FROM event_outbox WHERE intent_id=? AND channel=?", (intent_id,channel))
            if not row or row["status"]=="HELD": raise PolicyDenied("outbox held or missing")
            if row["status"]=="COMPLETE": return None
            if row["status"]=="PROCESSING" and row["lease_ms"]>self.now(): raise Fenced("outbox owned")
            failures=row["failure_count"]+int(row["status"]=="PROCESSING")
            if row["status"]=="PROCESSING" and failures>=row["max_failures"]:
                self.c.execute("""UPDATE event_outbox SET status='HELD',claim_token=NULL,lease_ms=NULL,
                    failure_count=?,hold_reason='PREPARATION_CAP' WHERE intent_id=? AND channel=?""",(failures,intent_id,channel))
                return None
            token,gen,lease=self.new_key(),row["generation"]+1,self._lease(lease_ms)
            self._change("""UPDATE event_outbox SET status='PROCESSING',claim_token=?,generation=?,lease_ms=?,runtime_epoch=?
                WHERE intent_id=? AND channel=? AND generation=? AND status=?""",
                (token,gen,lease,runtime["epoch"],intent_id,channel,row["generation"],row["status"]))
            self.c.execute("UPDATE event_outbox SET failure_count=? WHERE intent_id=? AND channel=?",(failures,intent_id,channel))
            return Handle("OUTBOX",(intent_id,channel),token,gen,runtime["epoch"],lease)

    def _context(self,intent_id):
        i=self._one("SELECT * FROM notification_intents WHERE intent_id=?", (intent_id,))
        if not i: raise PolicyDenied("missing intent")
        if i["policy_version"]!="F-S0" or i["config_version"]!="F-S0":
            raise PolicyDenied("intent policy/config version mismatch")
        event=self._one("SELECT * FROM opportunity_events WHERE event_id=?", (i["source_event_id"],)) if i["source_event_id"] else None
        if event and (event["normalizer_version"]!="F-S0" or event["matcher_version"]!="F-S0"):
            raise PolicyDenied("event representation version mismatch")
        return i,event

    def seal(self,h,*,fault=None,budget_ms=500):
        try:
            return self._seal_owned(h,fault=fault,budget_ms=budget_ms)
        except SealBudgetExceeded:
            self._hold_seal(h,"SEAL_BUDGET")
            raise
        except sqlite3.OperationalError as exc:
            if getattr(exc,"sqlite_errorcode",None) != sqlite3.SQLITE_INTERRUPT:
                raise
            # The interrupted unit has rolled back. Remove its progress callback
            # before recording the explicit hold; it must not interrupt recovery.
            self.c.set_progress_handler(None,0)
            self._hold_seal(h,"SQLITE_INTERRUPT")
            raise

    def _hold_seal(self,h,reason):
        with atomic(self.c):
            self._outbox(h)
            self.c.execute("""UPDATE event_outbox SET status='HELD',claim_token=NULL,lease_ms=NULL,
                failure_count=failure_count+1,hold_reason=? WHERE intent_id=? AND channel=?""",(reason,*h.key))

    def _seal_owned(self,h,*,fault=None,budget_ms=500):
        if not 0<=budget_ms<=5000: raise PolicyDenied("bounded seal budget required")
        start=perf_counter()
        with atomic(self.c):
            box=self._outbox(h)
            if box["sealed_ms"] is not None: return box["audience_count"]
            i,e=self._context(h.key[0])
            if not e: raise PolicyDenied("legacy catchup audience requires separate reviewed fixture")
            recipients=[]
            if h.key[1]=="ADMIN":
                recipients=[(r[0],"INFO",None,None,None) for r in self.c.execute("""SELECT h.recipient_key FROM recipient_handles h
                    JOIN recipient_associations a ON a.recipient_key=h.recipient_key WHERE h.role='ADMIN'""")]
            else:
                routes={side:{r[0] for r in self.c.execute("SELECT branch FROM event_routes WHERE event_id=? AND side=?",(e["event_id"],side))}
                        for side in ("BEFORE","AFTER")}
                users=self.c.execute("""SELECT a.recipient_key,u.branch_canonical,v.consent_epoch,v.profile_epoch,
                    p.pref_internship,p.pref_placement,p.pref_ppo FROM users u
                    JOIN subscriber_versions v ON v.user_id=u.id
                    JOIN user_preferences p ON p.user_id=u.id
                    JOIN recipient_associations a ON a.user_id=u.id
                    WHERE u.is_verified=1 AND u.is_active=1 AND u.graduation_year=2028""").fetchall()
                if self.record_mode:
                    from app.subscribers.matcher import SubscriptionMatcher
                    # Immutable event context is constant across this one coherent
                    # cohort; parse/qualify once without caching current user state.
                    after_snapshot=self._load(e["after_payload_id"])
                    side="BEFORE" if after_snapshot.get("state")=="ABSENT" else "AFTER"
                    representation=self._snapshot_record(self._load(e["before_payload_id"]) if side=="BEFORE" else after_snapshot)
                    eligible=self._branches(representation) if representation is not None else set()
                    action=(representation is not None and side=="AFTER" and str(representation.isactive).strip().casefold()=="true"
                        and e["window_quality"]=="VERIFIED_INSTANT" and e["deadline_ms"]>self.now())
                for u in users:
                    if self.record_mode:
                        if representation is None or not SubscriptionMatcher.matches_subscriber(representation,dict(u),eligible):
                            continue
                        if not action and self.info_ttl_ms is None:raise PolicyDenied("explicit fictional INFO lifetime required")
                        recipients.append((u["recipient_key"],"ACTION" if action else "INFO",u["branch_canonical"],u["consent_epoch"],u["profile_epoch"]))
                    elif u["branch_canonical"] in routes["AFTER"] and u["pref_internship"]:
                        recipients.append((u["recipient_key"],"ACTION",u["branch_canonical"],u["consent_epoch"],u["profile_epoch"]))
                    elif u["branch_canonical"] in routes["BEFORE"] and u["pref_internship"]:
                        recipients.append((u["recipient_key"],"INFO",u["branch_canonical"],u["consent_epoch"],u["profile_epoch"]))
            if i["kind"]=="MANUAL_REDRIVE":
                original=self._one("SELECT recipient_key,channel FROM notification_deliveries_v2 WHERE delivery_id=?", (i["redrive_delivery_id"],))
                recipients=[r for r in recipients if r[0]==original["recipient_key"] and h.key[1]==original["channel"]]
            for key,role,branch,consent,profile in sorted(set(recipients)):
                self.c.execute("""INSERT INTO event_audience
                    (intent_id,channel,recipient_key,role,selected_branch,consent_epoch,profile_epoch,sealed_policy_version,created_ms)
                    VALUES(?,?,?,?,?,?,?,'F-S0',?)""",(*h.key,key,role,branch,consent,profile,self.now()))
                if fault:fault("audience")
                if (perf_counter()-start)*1000>budget_ms: raise SealBudgetExceeded("coherent seal budget exceeded; transaction rolled back")
            self._outbox(h)
            count=len(set(recipients))
            self.c.execute("""UPDATE event_outbox SET sealed_ms=?,audience_count=?,status=?,
                claim_token=?,lease_ms=?,completed_ms=? WHERE intent_id=? AND channel=?""",
                (self.now(),count,"PROCESSING" if count else "COMPLETE",h.token if count else None,
                 h.lease_ms if count else None,None if count else self.now(),*h.key))
            return count

    def _authorized(self,audience):
        if audience["sealed_policy_version"]!=self._runtime()["policy_version"]:
            return False
        i,e=self._context(audience["intent_id"])
        if not e or not self._one("SELECT payload_id FROM protected_payloads WHERE payload_id=?",(i["snapshot_payload_id"],)):
            return False
        if not self._current_source(e):return False
        if not all(self._one("SELECT payload_id FROM protected_payloads WHERE payload_id=?",(e[k],)) for k in ("before_payload_id","after_payload_id")):
            return False
        mapping=self._one("SELECT * FROM recipient_associations WHERE recipient_key=?", (audience["recipient_key"],))
        if not mapping:return False
        if audience["channel"]=="ADMIN":return mapping["user_id"] is None
        user=self._one("""SELECT u.*,v.consent_epoch,v.profile_epoch,p.pref_internship,p.pref_placement,p.pref_ppo FROM users u
            JOIN subscriber_versions v ON v.user_id=u.id JOIN user_preferences p ON p.user_id=u.id WHERE u.id=?""",(mapping["user_id"],))
        if (not user or not user["is_active"] or not user["is_verified"] or user["graduation_year"]!=2028
                or user["consent_epoch"]!=audience["consent_epoch"] or (not self.record_mode and not user["pref_internship"])
                or user["branch_canonical"]!=audience["selected_branch"]):
            return False
        side="AFTER" if audience["role"]=="ACTION" else "BEFORE"
        if self.record_mode:
            from app.subscribers.matcher import SubscriptionMatcher
            after=self._load(e["after_payload_id"])
            side="BEFORE" if after.get("state")=="ABSENT" else "AFTER"
            representation=self._snapshot_record(self._load(e["before_payload_id"]) if side=="BEFORE" else after)
            if representation is None or not SubscriptionMatcher.matches_subscriber(representation,user,self._branches(representation)):
                return False
        return self._one("SELECT 1 FROM event_routes WHERE event_id=? AND side=? AND branch=?", (e["event_id"],side,user["branch_canonical"])) is not None

    def _materialize_delivery(self,a,*,account_key,epoch):
        i,e=self._context(a["intent_id"])
        authorized=self._authorized(a)
        # A terminal suppression is never sendable and needs no newly chosen
        # INFO send lifetime at the API consent-only boundary.
        if not authorized and a["role"]=="INFO" and self.info_ttl_ms is None:
            expiry=self.now()
        else:
            expiry=e["deadline_ms"] if a["role"]=="ACTION" else e["observed_ms"]+self.info_ttl_ms if self.record_mode else self.now()+100000
        self.c.execute("""INSERT INTO notification_deliveries_v2
            (company_key,audience_id,intent_id,channel,recipient_key,account_key,status,max_attempts,
             runtime_epoch,next_ms,expires_ms,priority,created_ms,finished_ms,reason_code)
            VALUES(?,?,?,?,?,?,?,5,?,?,?,0,?,?,?)
            ON CONFLICT(intent_id,channel,recipient_key) DO NOTHING""",
            (i["company_key"],a["audience_id"],a["intent_id"],a["channel"],a["recipient_key"],account_key,
             "PENDING" if authorized else "SUPPRESSED",epoch,self.now(),
             expiry,
             self.now(),None if authorized else self.now(),None if authorized else "CURRENT_AUTH"))
        persisted=self._one("""SELECT * FROM notification_deliveries_v2
            WHERE intent_id=? AND channel=? AND recipient_key=?""",(a["intent_id"],a["channel"],a["recipient_key"]))
        if persisted["audience_id"]!=a["audience_id"] or persisted["company_key"]!=i["company_key"]:
            raise PolicyDenied("targeted conflict content mismatch")

    def fanout_page(self,h,*,fault=None,account_key="account-1"):
        with atomic(self.c):
            if not self._one("SELECT account_key FROM smtp_account_gate WHERE account_key=?",(account_key,)):
                raise PolicyDenied("fictional account must be explicitly seeded")
            existing=self._one("SELECT * FROM event_outbox WHERE intent_id=? AND channel=?",h.key)
            if existing and existing["status"]=="COMPLETE":return 0
            box=self._outbox(h)
            if box["sealed_ms"] is None:raise PolicyDenied("unsealed audience")
            rows=self.c.execute("""SELECT * FROM event_audience WHERE intent_id=? AND channel=?
                AND audience_id>? ORDER BY audience_id LIMIT 100""",(*h.key,box["last_audience_id"])).fetchall()
            i,e=self._context(h.key[0])
            for row in rows:
                a=dict(row)
                self._materialize_delivery(a,account_key=account_key,epoch=h.epoch)
                if fault:fault("delivery")
            self._outbox(h)
            count=box["processed_count"]+len(rows)
            if count>box["audience_count"]:raise PolicyDenied("page exceeds seal")
            complete=count==box["audience_count"]
            self._change("""UPDATE event_outbox SET processed_count=?,last_audience_id=?,status=?,claim_token=?,lease_ms=?,completed_ms=?
                WHERE intent_id=? AND channel=? AND generation=? AND claim_token=?""",
                (count,rows[-1]["audience_id"] if rows else box["last_audience_id"],"COMPLETE" if complete else "PROCESSING",
                 None if complete else h.token,None if complete else h.lease_ms,self.now() if complete else None,*h.key,h.generation,h.token))
            return len(rows)

    def _delivery(self,h,states=("PROCESSING","SENDING"),*,send_mode=False):
        runtime=self._mode("send") if send_mode else self._runtime()
        row=self._one("SELECT * FROM notification_deliveries_v2 WHERE delivery_id=?", (h.key,))
        if (h.kind!="DELIVERY" or not row or row["status"] not in states or row["claim_token"]!=h.token
                or row["generation"]!=h.generation or row["runtime_epoch"]!=h.epoch or runtime["epoch"]!=h.epoch
                or row["lease_ms"] is None or row["lease_ms"]<=self.now()):
            raise Fenced("stale delivery ownership")
        return row

    def _audience(self,delivery):
        row=self._one("SELECT * FROM event_audience WHERE audience_id=?", (delivery["audience_id"],))
        if not row:raise PolicyDenied("missing sealed audience")
        return row

    def _reserve(self,account,*,token,epoch,lease,delivery=None):
        gate=self._one("SELECT * FROM smtp_account_gate WHERE account_key=?", (account,))
        if not gate or gate["state"]!="FREE" or gate["next_allowed_ms"]>self.now():return None
        submission,gen=self.new_key(),gate["generation"]+1
        self._change("""UPDATE smtp_account_gate SET state='RESERVED',generation=?,owner_token=?,runtime_epoch=?,
            submission_key=?,delivery_id=?,lease_ms=?,updated_ms=?
            WHERE account_key=? AND state='FREE' AND generation=?""",
            (gen,token,epoch,submission,delivery,lease,self.now(),account,gate["generation"]))
        return gen,submission

    def claim_delivery(self,*,account_key="account-1",lease_ms=100,lose_ack=False,channel=None,fault=None,company_key=None):
        if channel not in (None,"ADMIN","SUBSCRIBER"):raise PolicyDenied("supported delivery channel required")
        result=None
        with atomic(self.c):
            runtime=self._mode("send")
            if self.transport_authority:self.check_transport_slot(account_key,channel)
            gate=self._one("SELECT * FROM smtp_account_gate WHERE account_key=?",(account_key,))
            if not gate or gate["state"]!="FREE" or gate["next_allowed_ms"]>self.now():return None
            rows=self.c.execute("""SELECT * FROM notification_deliveries_v2
                WHERE account_key=? AND status='PENDING' AND next_ms<=? AND attempt_count<max_attempts
                AND (? IS NULL OR channel=?)
                ORDER BY CASE WHEN reason_code='SOURCE_FRESHNESS_HOLD' THEN 1 ELSE 0 END,
                    priority DESC,created_ms,delivery_id LIMIT 25""",(account_key,self.now(),channel,channel)).fetchall()
            for row in rows:
                d=dict(row)
                if company_key is not None:
                    intent,_=self._context(d["intent_id"])
                    if intent["company_key"]!=company_key:continue
                if d["cancel_requested_ms"] is not None or (d["expires_ms"] is not None and d["expires_ms"]<=self.now()) or not self._authorized(self._audience(d)):
                    self._suppress(d,"CURRENT_AUTH_OR_EXPIRY")
                    continue
                if not self._source_recent(self._context(d["intent_id"])[1]):
                    # Keep the obligation and attempt count intact, but move the
                    # bounded stale page out of the way of fresh companies.
                    # A new positive observation wakes these holds immediately.
                    self._change("""UPDATE notification_deliveries_v2 SET next_ms=?,reason_code='SOURCE_FRESHNESS_HOLD'
                        WHERE delivery_id=? AND status='PENDING' AND generation=?""",
                        (self.now()+1000,d["delivery_id"],d["generation"]))
                    continue
                token,gen,lease=self.new_key(),d["generation"]+1,self._lease(lease_ms)
                reservation=self._reserve(account_key,token=token,epoch=runtime["epoch"],lease=lease,delivery=d["delivery_id"])
                if reservation is None:return None
                if fault:fault("reservation")
                gate_gen,submission=reservation
                self._change("""UPDATE notification_deliveries_v2 SET status='PROCESSING',claim_token=?,generation=?,
                    lease_ms=?,runtime_epoch=?,attempt_count=attempt_count+1 WHERE delivery_id=? AND status='PENDING'
                    AND generation=? AND attempt_count=? AND attempt_count<max_attempts""",
                    (token,gen,lease,runtime["epoch"],d["delivery_id"],d["generation"],d["attempt_count"]))
                self.c.execute("""INSERT INTO delivery_attempts
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (d["delivery_id"],gen,d["attempt_count"]+1,token,runtime["epoch"],self.transport_authority or "fictional-owner",self.now(),lease,account_key,gate_gen,submission))
                result=Handle("DELIVERY",d["delivery_id"],token,gen,runtime["epoch"],lease,gate_gen,submission)
                if fault:fault("attempt")
                break
        if lose_ack and result:raise CommitUnknown("T5",result.submission_key)
        return result

    def inspect_claim(self,submission_key):
        row=self._one("""SELECT a.*,d.status,d.lease_ms AS current_lease,d.claim_token AS current_token,
            d.generation AS current_gen FROM delivery_attempts a
            JOIN notification_deliveries_v2 d ON d.delivery_id=a.delivery_id WHERE a.submission_key=?""",(submission_key,))
        if not row or row["status"]!="PROCESSING" or row["current_gen"]!=row["generation"] or row["current_token"]!=row["claim_token"]:
            return None
        h=Handle("DELIVERY",row["delivery_id"],row["claim_token"],row["generation"],row["runtime_epoch"],row["current_lease"],
                 row["gate_generation"],row["submission_key"])
        self._delivery(h,("PROCESSING",),send_mode=True)
        return h

    def _matching_reservation(self,h,account):
        row=self._one("SELECT * FROM smtp_account_gate WHERE account_key=?",(account,))
        if (not row or row["state"]!="RESERVED" or row["generation"]!=h.gate_generation
                or row["submission_key"]!=h.submission_key or row["owner_token"]!=h.token
                or row["runtime_epoch"]!=h.epoch or row["lease_ms"]<=self.now()):
            raise Fenced("stale exact gate reservation")
        return row

    def _invocation(self,h,account,message_class,delivery=None):
        self._matching_reservation(h,account)
        invocation=self.new_key()
        self.c.execute("INSERT INTO transport_invocations VALUES(?,?,?,?,?,?,?,?,?,?)",
            (invocation,account,h.gate_generation,h.submission_key,self.transport_authority or "fictional-owner",h.epoch,
             delivery,h.generation if delivery is not None else None,message_class,self.now()))
        self._change("""UPDATE smtp_account_gate SET state='INFLIGHT',invocation_id=?,updated_ms=?
            WHERE account_key=? AND state='RESERVED' AND generation=? AND submission_key=?""",
            (invocation,self.now(),account,h.gate_generation,h.submission_key))
        return invocation

    def authorize_send(self,h,*,fault=None,lose_ack=False):
        with atomic(self.c):
            d=self._delivery(h,("PROCESSING",),send_mode=True)
            if self.transport_authority:self.check_transport_authority()
            a=self._audience(d)
            i,e=self._context(d["intent_id"])
            if not self._source_recent(e):
                raise Fenced("source evidence is stale; preparation remains recoverable")
            fresh=(d["expires_ms"] is None or d["expires_ms"]>self.now())
            if a["role"]=="ACTION":
                fresh=fresh and e is not None and e["window_quality"]=="VERIFIED_INSTANT" and e["deadline_ms"]-self.now()>=30
            if d["cancel_requested_ms"] is not None or not self._authorized(a) or not fresh:
                self._suppress(d,"PRE_SEND_AUTH_OR_FRESHNESS")
                return None
            self._matching_reservation(h,d["account_key"])
            invocation=self.new_key()
            self.c.execute("INSERT INTO transport_invocations VALUES(?,?,?,?,?,?,?,?,?,?)",
                (invocation,d["account_key"],h.gate_generation,h.submission_key,self.transport_authority or "fictional-owner",h.epoch,h.key,h.generation,d["channel"],self.now()))
            if fault:fault("invocation")
            self._delivery(h,("PROCESSING",),send_mode=True)
            self._change("""UPDATE notification_deliveries_v2 SET status='SENDING',send_started_ms=?,invocation_id=?
                WHERE delivery_id=? AND status='PROCESSING' AND claim_token=? AND generation=?""",
                (self.now(),invocation,h.key,h.token,h.generation))
            self._change("""UPDATE smtp_account_gate SET state='INFLIGHT',invocation_id=?,updated_ms=?
                WHERE account_key=? AND state='RESERVED' AND generation=? AND submission_key=?""",
                (invocation,self.now(),d["account_key"],h.gate_generation,h.submission_key))
        if lose_ack:raise CommitUnknown("T6",invocation)
        return self._issue_permit(invocation,h)

    def _issue_permit(self,invocation,h):
        with self._permit_lock:
            if (invocation in self._retired_invocations or
                    self._one("SELECT 1 FROM transport_facts WHERE invocation_id=? AND fact_kind IN('ENDED','PROCESS_EXIT_CONFIRMED')",(invocation,))):
                raise PolicyDenied("ended invocation cannot receive a fake permit")
            permit=Permit(invocation,h)
            self._permits[invocation]=permit
            self._issued_permits[invocation]=permit
            return permit

    def submit_fake(self,permit,transport,*,message=None):
        if get_ident()==self._connection_thread:
            with atomic(self.c):
                self._shared_transport()
                if self._listing_policy() is not None and not self.transport_authority:
                    if not isinstance(permit,Permit):raise Fenced("issued permit required")
                    if permit.handle.kind!="ACCOUNT":
                        delivery=self._delivery(permit.handle,("SENDING",))
                        event=self._context(delivery["intent_id"])[1]
                        if not self._authorized(self._audience(delivery)) or not self._source_recent(event):
                            raise Fenced("current consent and fresh source required at physical consumption")
        else:
            # Old F-S0 fake callers may run in another thread. Never borrow
            # their creator's SQLite connection; inspect durable enrollment
            # through a fresh, guarded, thread-owned disposable connection.
            from app.database.notification_migrator import connect_disposable
            with connect_disposable(self._database_path) as connection:
                if connection.execute("SELECT 1 FROM system_state WHERE key='r2a:controller'").fetchone():
                    raise Fenced("shared producer requires its own bound thread connection")
                if connection.execute("SELECT 1 FROM system_state WHERE key='notification:listing-contract'").fetchone():
                    raise Fenced("current-source fixture requires its owning thread")
        if self.transport_authority:
            from app.notifications.transport_arbiter import AuthorityGateway
            if not isinstance(transport,AuthorityGateway) or transport.repository is not self or transport.authority!=self.transport_authority:
                raise PolicyDenied("bound authority required; no local fake/SMTP fallback")
        with self._permit_lock:
            if (not isinstance(permit,Permit) or not isinstance(transport,FakeTransport)
                    or self._permits.get(permit.invocation_id) is not permit or permit.consumed or permit.retired):
                raise PolicyDenied("single live issued fake permit required")
            permit.consumed=True
        # Consumption/retirement serialize; the bounded fake invocation itself
        # runs outside the lock so positive end recording cannot block on it.
        if message is not None:
            if not callable(getattr(transport,"submit_message",None)):
                raise PolicyDenied("explicit fictional message transport required")
            return transport.submit_message(permit.invocation_id,message)
        return transport.submit(permit.invocation_id)

    def reserve_account(self,*,account_key="account-1",lease_ms=100):
        with atomic(self.c):
            r=self._mode("send")
            if self.transport_authority:self.check_transport_slot(account_key,"ACCOUNT")
            token,lease=self.new_key(),self._lease(lease_ms)
            reserved=self._reserve(account_key,token=token,epoch=r["epoch"],lease=lease)
            if not reserved:return None
            gen,key=reserved
            if self.transport_authority:
                preparation=dict(authority=self.transport_authority,account=account_key,gate_generation=gen,submission=key,epoch=r["epoch"])
                self.c.execute("INSERT INTO system_state(key,value,updated_at) VALUES(?,?,?)",
                    ("r2a:account-preparation:"+key,json.dumps(preparation,sort_keys=True),str(self.now())))
            return Handle("ACCOUNT",account_key,token,gen,r["epoch"],lease,gen,key)

    def authorize_account(self,h):
        with atomic(self.c):
            r=self._mode("send")
            if self.transport_authority:self.check_transport_authority()
            if h.kind!="ACCOUNT" or h.epoch!=r["epoch"]:raise Fenced("account epoch")
            invocation=self._invocation(h,h.key,"ACCOUNT")
        return self._issue_permit(invocation,h)

    def recover_account(self,h):
        """Exact ACCOUNT preparation only; never a lease release after invocation."""
        if not isinstance(h,Handle) or h.kind!="ACCOUNT":raise Fenced("exact ACCOUNT handle required")
        with atomic(self.c):
            gate=self._one("SELECT * FROM smtp_account_gate WHERE account_key=?",(h.key,))
            if (not gate or gate["state"]!="RESERVED" or gate["delivery_id"] is not None
                    or gate["generation"]!=h.gate_generation or gate["owner_token"]!=h.token
                    or gate["submission_key"]!=h.submission_key or gate["runtime_epoch"]!=h.epoch):
                raise Fenced("stale ACCOUNT preparation")
            if gate["lease_ms"]>self.now() and self._runtime()["epoch"]==h.epoch:raise Fenced("live ACCOUNT preparation")
            preparation=self._one("SELECT value FROM system_state WHERE key=?",("r2a:account-preparation:"+h.submission_key,))
            if preparation:
                p=json.loads(preparation["value"])
                owner=self._one("SELECT value FROM system_state WHERE key=?",("r2a:authority:"+h.key,))
                if (not owner or json.loads(owner["value"])["identity"]!=p["authority"] or
                    p["account"]!=h.key or p["gate_generation"]!=h.gate_generation or p["submission"]!=h.submission_key or p["epoch"]!=h.epoch or
                    (self.transport_authority is not None and self.transport_authority!=p["authority"])):
                    raise Fenced("ACCOUNT preparation authority identity mismatch")
            if self._one("SELECT 1 FROM transport_invocations WHERE submission_key=?",(h.submission_key,)):
                raise PolicyDenied("physical invocation cannot be timer-recovered")
            self._change("""UPDATE smtp_account_gate SET state='FREE',owner_token=NULL,runtime_epoch=NULL,
                submission_key=NULL,lease_ms=NULL,updated_ms=? WHERE account_key=? AND state='RESERVED'
                AND generation=? AND submission_key=?""",(self.now(),h.key,h.gate_generation,h.submission_key))
            return True

    def check_transport_authority(self,account="account-1"):
        row=self._one("SELECT value FROM system_state WHERE key=?",("r2a:authority:"+account,))
        controller=self._one("SELECT value FROM system_state WHERE key='r2a:controller'")
        if not row or not controller:raise Fenced("missing authority/controller")
        if self._one("SELECT 1 FROM system_state WHERE key='r2a:safety-hold'"):
            raise Fenced("transport safety evidence requires controller review")
        owner,c=json.loads(row["value"]),json.loads(controller["value"])
        if owner["identity"]!=self.transport_authority or owner["controller_generation"]!=c["generation"] or c["lease_ms"]<=self.now():
            raise Fenced("stale authority/controller")
        return owner

    def check_transport_slot(self,account,message_class):
        self.check_transport_authority(account)
        row=self._one("SELECT value FROM system_state WHERE key=?",("r2a:slot:"+account,))
        if not row:raise Fenced("missing authority admission slot")
        slot=json.loads(row["value"])
        gate=self._one("SELECT state,generation FROM smtp_account_gate WHERE account_key=?",(account,))
        if not gate or gate["state"]!="FREE" or gate["generation"]!=slot["prior_generation"]:
            raise Fenced("single-use account admission slot required")
        if slot["token"]!=self.transport_slot or slot["authority"]!=self.transport_authority or slot["epoch"]!=self._runtime()["epoch"] or slot["expires_ms"]<=self.now() or slot["message_class"]!=message_class:
            raise Fenced("expired or foreign authority slot")
        return slot

    def add_account(self,key):
        with atomic(self.c):
            self.c.execute("""INSERT INTO smtp_account_gate(account_key,state,generation,next_allowed_ms,updated_ms)
                VALUES(?,'FREE',0,0,?)""",(key,self.now()))

    def _release_reservation(self,d):
        attempt=self._one("SELECT * FROM delivery_attempts WHERE delivery_id=? AND generation=?",(d["delivery_id"],d["generation"]))
        if not attempt:return False
        row=self._one("SELECT * FROM smtp_account_gate WHERE account_key=?", (d["account_key"],))
        if (not row or row["state"]!="RESERVED" or row["generation"]!=attempt["gate_generation"]
                or row["submission_key"]!=attempt["submission_key"]):return False
        if self._one("SELECT 1 FROM transport_invocations WHERE submission_key=?",(attempt["submission_key"],)):
            raise PolicyDenied("no timer release once invocation exists")
        self.c.execute("""UPDATE smtp_account_gate SET state='FREE',owner_token=NULL,runtime_epoch=NULL,
            submission_key=NULL,delivery_id=NULL,invocation_id=NULL,lease_ms=NULL,updated_ms=?
            WHERE account_key=? AND generation=? AND state='RESERVED'""",(self.now(),d["account_key"],attempt["gate_generation"]))
        return True

    def _release_ended(self,invocation):
        v=self._one("SELECT * FROM transport_invocations WHERE invocation_id=?",(invocation,))
        if not v:raise PolicyDenied("unknown original invocation")
        if not self._one("SELECT 1 FROM transport_facts WHERE invocation_id=? AND fact_kind IN('ENDED','PROCESS_EXIT_CONFIRMED')",(invocation,)):
            return False
        with self._permit_lock:
            # Record even before issuance, closing the T6-commit→permit window.
            self._retired_invocations.add(invocation)
            permit=self._issued_permits.get(invocation)
            if permit is not None:permit.retired=True
        # Retire before the account can be reused. Rollback may conservatively
        # leave a fake permit retired; recovery never creates a resend permit.
        cursor=self.c.execute("""UPDATE smtp_account_gate SET state='FREE',owner_token=NULL,runtime_epoch=NULL,
            submission_key=NULL,delivery_id=NULL,invocation_id=NULL,lease_ms=NULL,next_allowed_ms=?,updated_ms=?
            WHERE account_key=? AND generation=? AND submission_key=? AND invocation_id=? AND state IN('INFLIGHT','BLOCKED')""",
            (self.now(),self.now(),v["account_key"],v["gate_generation"],v["submission_key"],invocation))
        return cursor.rowcount==1

    def release_ended(self,invocation):
        with atomic(self.c):
            return self._release_ended(invocation)

    def _block(self,invocation):
        self.c.execute("""UPDATE smtp_account_gate SET state='BLOCKED',updated_ms=?
            WHERE invocation_id=? AND state='INFLIGHT'""",(self.now(),invocation))

    def record_fact(self,invocation,kind,*,fact_key,observed_ms=None,evidence_reference=None):
        with atomic(self.c):
            v=self._one("SELECT * FROM transport_invocations WHERE invocation_id=?",(invocation,))
            if not v:raise PolicyDenied("fact requires original durable invocation")
            reference=evidence_reference or "fictional:"+kind+":"+fact_key
            existing=self._one("SELECT * FROM transport_facts WHERE invocation_id=? AND fact_kind=? AND fact_key=?", (invocation,kind,fact_key))
            if existing:
                if existing["evidence_reference"]!=reference or (observed_ms is not None and existing["observed_ms"]!=observed_ms):
                    raise PolicyDenied("fact key/content mismatch")
            else:
                self.c.execute("""INSERT INTO transport_facts
                    (invocation_id,fact_kind,fact_key,observed_ms,evidence_type,evidence_reference)
                    VALUES(?,?,?,?, 'FICTIONAL_TRUSTED_ADAPTER',?)""",
                    (invocation,kind,fact_key,self.now() if observed_ms is None else observed_ms,reference))
            if kind in ("ENDED","PROCESS_EXIT_CONFIRMED"):self._release_ended(invocation)
            if v["delivery_id"] is not None:
                d=self._one("SELECT * FROM notification_deliveries_v2 WHERE delivery_id=?",(v["delivery_id"],))
                kinds={r[0] for r in self.c.execute("SELECT fact_kind FROM transport_facts WHERE invocation_id=?",(invocation,))}
                authoritative_conflict="ACCEPTED" in kinds and "NOT_ACCEPTED" in kinds
                if authoritative_conflict and (d["status"]!="SENDING" or d["generation"]!=v["delivery_generation"]):
                    # Preserve terminal history; freeze new permits, never invent a correction ACK.
                    if self._one("SELECT 1 FROM system_state WHERE key='r2a:controller'"):
                        # Evidence trips admission without granting ordinary
                        # producers controller ownership of global modes/epoch.
                        self.c.execute("INSERT INTO system_state(key,value,updated_at) VALUES('r2a:safety-hold','true',?) ON CONFLICT(key) DO NOTHING",(str(self.now()),))
                    else:
                        self.c.execute("UPDATE notification_runtime SET send_mode='OFF',epoch=epoch+1 WHERE send_mode<>'OFF'")
                    affected=self.c.execute("""SELECT * FROM notification_deliveries_v2 WHERE company_key=?
                        AND status IN('PENDING','PROCESSING','SENDING')""",(d["company_key"],)).fetchall()
                    for other in affected:self._suppress(dict(other),"TRANSPORT_INTEGRITY")

    def _finish(self,d,status,*,reason,accepted=None,invocation=None,resolution=None):
        pending=status=="PENDING"
        self._change("""UPDATE notification_deliveries_v2 SET status=?,claim_token=NULL,lease_ms=NULL,
            generation=?,next_ms=?,send_started_ms=?,invocation_id=?,accepted_ms=?,ack_ms=?,finished_ms=?,
            resolution_id=?,reason_code=? WHERE delivery_id=? AND status=? AND generation=?""",
            (status,d["generation"]+1 if pending else d["generation"],self.now()+10 if pending else d["next_ms"],
             None if pending else d["send_started_ms"],None if pending else (invocation or d["invocation_id"]),
             accepted,self.now() if status=="SENT" else None,None if pending else self.now(),
             resolution,reason,d["delivery_id"],d["status"],d["generation"]))

    def acknowledge(self,h,*,lose_ack=False):
        with atomic(self.c):
            d=self._delivery(h,("SENDING",))
            kinds={r[0] for r in self.c.execute("SELECT fact_kind FROM transport_facts WHERE invocation_id=?",(d["invocation_id"],))}
            if "ACCEPTED" in kinds and not kinds.intersection({"UNKNOWN","NOT_ACCEPTED"}):
                accepted=self.c.execute("SELECT min(observed_ms) FROM transport_facts WHERE invocation_id=? AND fact_kind='ACCEPTED'",(d["invocation_id"],)).fetchone()[0]
                status="SENT"
                self._finish(d,status,reason="PROTOCOL_ACCEPTED",accepted=accepted)
            elif "NOT_ACCEPTED" in kinds and kinds.intersection({"ENDED","PROCESS_EXIT_CONFIRMED"}) and not kinds.intersection({"ACCEPTED","UNKNOWN"}):
                status=("SUPPRESSED" if d["cancel_requested_ms"] is not None or not self._authorized(self._audience(d))
                        else "FAILED" if d["attempt_count"]>=d["max_attempts"] else "PENDING")
                self._finish(d,status,reason="FINAL_PROTOCOL_NEGATIVE")
            elif "UNKNOWN" in kinds or ("ACCEPTED" in kinds and "NOT_ACCEPTED" in kinds):
                status="UNCERTAIN"
                self._finish(d,status,reason="OUTCOME_UNCERTAIN")
                self._block(d["invocation_id"])
            else:
                raise PolicyDenied("final outcome/end evidence not available")
            self._release_ended(d["invocation_id"])
        if lose_ack:raise CommitUnknown("T7",h.key)
        return status

    def fail_preparation(self,h,reason):
        """No invocation exists: release safely, consume this attempt and back off."""
        with atomic(self.c):
            d=self._delivery(h,("PROCESSING",))
            self._release_reservation(d)
            self.c.execute("INSERT INTO delivery_attempt_facts VALUES(?,?,'NOT_ACCEPTED',?,?,?)",
                (h.key,h.generation,self.new_key(),self.now(),reason))
            status="FAILED" if d["attempt_count"]>=d["max_attempts"] else "PENDING"
            self._finish(d,status,reason=reason)
            return status

    def sync_consent(self,user,*,created=False):
        """Validate identity sidecars and suppress within the caller's write unit."""
        if not self.c.in_transaction:raise PolicyDenied("consent hook requires caller transaction")
        if not self._one("SELECT 1 FROM users WHERE id=?",(user,)) or not self._one("SELECT 1 FROM subscriber_versions WHERE user_id=?",(user,)):
            raise sqlite3.IntegrityError("missing subscriber versions")
        if not self._one("SELECT 1 FROM user_preferences WHERE user_id=?",(user,)):
            raise sqlite3.IntegrityError("missing subscriber preferences")
        associations=self.c.execute("SELECT a.recipient_key,h.role FROM recipient_associations a JOIN recipient_handles h USING(recipient_key) WHERE a.user_id=?",(user,)).fetchall()
        if created and not associations:
            key=self.new_key()
            self.c.execute("INSERT INTO recipient_handles VALUES(?,'SUBSCRIBER',?)",(key,self.now()))
            self.c.execute("INSERT INTO recipient_associations(recipient_key,user_id) VALUES(?,?)",(key,user))
        elif len(associations)!=1 or associations[0][1]!="SUBSCRIBER":
            raise sqlite3.IntegrityError("missing or inconsistent subscriber association")
        self._change("UPDATE subscriber_versions SET updated_ms=? WHERE user_id=?",(self.now(),user))
        self._suppress_current(user)

    def recover_delivery(self,delivery,*,unknown_commit=False):
        with atomic(self.c):
            d=self._one("SELECT * FROM notification_deliveries_v2 WHERE delivery_id=?", (delivery,))
            if not d:raise PolicyDenied("missing delivery")
            if d["status"] not in ("PROCESSING","SENDING"):return d["status"]
            if not unknown_commit and d["lease_ms"]>self.now() and d["runtime_epoch"]==self._runtime()["epoch"]:
                raise Fenced("live owner cannot be recovered")
            if unknown_commit and d["status"]!="SENDING":raise PolicyDenied("unknown send commit requires SENDING")
            if d["status"]=="PROCESSING":
                self._release_reservation(d)
                status="FAILED" if d["attempt_count"]>=d["max_attempts"] else "PENDING"
                self.c.execute("INSERT INTO delivery_attempt_facts VALUES(?,?,'PREP_EXPIRED',?,?, 'SAFE_NO_INVOCATION')",
                               (delivery,d["generation"],self.new_key(),self.now()))
                self._finish(d,status,reason="PREP_EXPIRED")
                # FAILED is fenced by immutable terminality; never update it again.
            else:
                status="UNCERTAIN"
                self.c.execute("INSERT INTO delivery_attempt_facts VALUES(?,?,'ACK_FAILED',?,?, 'SEND_RECOVERY')",
                               (delivery,d["generation"],self.new_key(),self.now()))
                self._finish(d,status,reason="COMMIT_OR_ACK_UNCERTAIN")
                self._block(d["invocation_id"])
                self._release_ended(d["invocation_id"])
            return status

    def drain_fake(self,transport,*,quantum=25):
        if type(quantum) is not int or not 1<=quantum<=25:raise PolicyDenied("bounded fictional quantum")
        progress=0
        for _ in range(quantum):
            claim=self.claim_delivery()
            if claim is None:break
            progress+=1
            permit=self.authorize_send(claim)
            if permit is None:continue
            outcome=self.submit_fake(permit,transport)
            self.record_fact(permit.invocation_id,outcome,fact_key="finalized-result")
            self.record_fact(permit.invocation_id,"ENDED",fact_key="positive-fake-end")
            self.acknowledge(claim)
        return progress

    def approve(self,purpose,company_key,target):
        if self._listing_policy() is not None:
            raise PolicyDenied("administrative approval/redrive/resolve disabled for restricted listing pilot")
        from app.database.protected_payloads import require_binding
        codec=require_binding(self.c)
        if codec:
            scope={'purpose':purpose,'target':str(target)}
            if purpose=='RESOLVE':
                delivery=self._one('SELECT invocation_id FROM notification_deliveries_v2 WHERE delivery_id=?',(target,))
                if not delivery:raise PolicyDenied('resolution target missing')
                scope['invocation']=delivery['invocation_id']
            codec.prepare('APPROVAL',scope,{'company':company_key,'purpose':purpose,'target':str(target)})
        with atomic(self.c):
            if self._listing_policy() is not None:
                raise PolicyDenied("administrative approval disabled for restricted listing pilot")
            authorization=self.new_key()
            scope={"purpose":purpose,"target":str(target)}
            if purpose=="RESOLVE":
                delivery=self._one("SELECT invocation_id FROM notification_deliveries_v2 WHERE delivery_id=?", (target,))
                if not delivery:raise PolicyDenied("resolution target missing")
                scope["invocation"]=delivery["invocation_id"]
            protected=payload(self.c,"APPROVAL",scope,self.now(),owner={'company':company_key,'purpose':purpose,'target':str(target)})
            self.c.execute("INSERT INTO notification_authorizations VALUES(?,?,?,?,?,'F-S0',?)",
                           (authorization,company_key,purpose,protected,int(purpose=="REDRIVE"),self.now()))
            return authorization

    def _approval(self,authorization,purpose,company,target,invocation=None):
        a=self._one("SELECT * FROM notification_authorizations WHERE authorization_id=?",(authorization,))
        if not a or a["purpose"]!=purpose or a["company_key"]!=company or a["policy_version"]!="F-S0":
            raise PolicyDenied("authorization scope mismatch")
        scope=self._load(a["scope_payload_id"],owner={'company':company,'purpose':purpose,'target':str(target)})
        if scope.get("target")!=str(target) or (invocation is not None and scope.get("invocation")!=invocation):
            raise PolicyDenied("authorization target mismatch")
        if purpose=="REDRIVE" and not a["duplicate_risk_ack"]:raise PolicyDenied("duplicate-risk acknowledgment required")
        return a

    def redrive(self,delivery,authorization):
        with atomic(self.c):
            d=self._one("SELECT * FROM notification_deliveries_v2 WHERE delivery_id=?",(delivery,))
            if not d:raise PolicyDenied("missing prior delivery")
            self._approval(authorization,"REDRIVE",d["company_key"],delivery)
            old=self._one("SELECT intent_id FROM notification_intents WHERE authorization_id=?",(authorization,))
            if old:return old["intent_id"]
            parent=self._one("SELECT * FROM notification_intents WHERE intent_id=?", (d["intent_id"],))
            if not parent["source_event_id"]:raise PolicyDenied("legacy catchup redrive excluded")
            key=self.new_key()
            self.c.execute("""INSERT INTO notification_intents
                (intent_id,company_key,kind,source_event_id,parent_intent_id,redrive_delivery_id,
                 authorization_id,snapshot_payload_id,policy_version,config_version,created_ms)
                VALUES(?,?,'MANUAL_REDRIVE',?,?,?,?,?,'F-S0','F-S0',?)""",
                (key,d["company_key"],parent["source_event_id"],d["intent_id"],delivery,authorization,parent["snapshot_payload_id"],self.now()))
            self._outboxes(key,held_channel="ADMIN" if d["channel"]=="SUBSCRIBER" else "SUBSCRIBER")
            return key

    def resolve(self,delivery,authorization,*,confirm_accepted):
        with atomic(self.c):
            d=self._one("SELECT * FROM notification_deliveries_v2 WHERE delivery_id=?",(delivery,))
            if not d:raise PolicyDenied("missing resolution delivery")
            self._approval(authorization,"RESOLVE",d["company_key"],delivery,d["invocation_id"])
            existing=self._one("SELECT resolution_id FROM delivery_resolutions WHERE authorization_id=?",(authorization,))
            if existing:return existing["resolution_id"]
            if d["status"]!="UNCERTAIN":raise PolicyDenied("only uncertainty resolution allowed")
            resolution=self.new_key()
            action="CONFIRM_ACCEPTED" if confirm_accepted else "KEEP_UNCERTAIN"
            self.c.execute("INSERT INTO delivery_resolutions VALUES(?,?,?,?,?,?,?,?,?)",
                (resolution,delivery,d["invocation_id"],d["generation"],authorization,action,self.now(),self.now() if confirm_accepted else None,"fictional-operator-proof"))
            if confirm_accepted:self._finish(d,"SENT",reason="AUTHORIZED_CONFIRMATION",accepted=self.now(),resolution=resolution)
            return resolution

    def _suppress(self,d,reason):
        if d["status"]=="SENDING":
            self.c.execute("UPDATE notification_deliveries_v2 SET cancel_requested_ms=?,reason_code=? WHERE delivery_id=? AND status='SENDING'",
                           (self.now(),reason,d["delivery_id"]))
            self._block(d["invocation_id"])
        elif d["status"] in ("PENDING","PROCESSING"):
            if d["status"]=="PROCESSING":
                self._release_reservation(d)
                self.c.execute("INSERT INTO delivery_attempt_facts VALUES(?,?,'SUPPRESSED',?,?,?)",
                    (d["delivery_id"],d["generation"],self.new_key(),self.now(),reason))
            self._finish(d,"SUPPRESSED",reason=reason)

    def _suppress_current(self,user):
        rows=self.c.execute("""SELECT d.* FROM notification_deliveries_v2 d JOIN recipient_associations a
            ON a.recipient_key=d.recipient_key WHERE a.user_id=? AND d.status IN('PENDING','PROCESSING','SENDING')""",(user,)).fetchall()
        for d in rows:
            if not self._authorized(self._audience(dict(d))):self._suppress(dict(d),"CONSENT_PROFILE")
        # A sealed audience is already an unsent obligation even before T4.
        # Materialize only disallowed rows as permanent SUPPRESSED identities in
        # this same mutation unit; T4 later accounts them without resurrection.
        unmaterialized=self.c.execute("""SELECT a.* FROM event_audience a
            JOIN recipient_associations r ON r.recipient_key=a.recipient_key
            LEFT JOIN notification_deliveries_v2 d ON d.intent_id=a.intent_id
                AND d.channel=a.channel AND d.recipient_key=a.recipient_key
            WHERE r.user_id=? AND d.delivery_id IS NULL""",(user,)).fetchall()
        for audience in unmaterialized:
            a=dict(audience)
            if not self._authorized(a):
                self._materialize_delivery(a,account_key="account-1",epoch=self._runtime()["epoch"])

    def change_user(self,user,**updates):
        permitted={"is_active","is_verified","email","branch_canonical","graduation_year"}
        if not updates or not set(updates)<=permitted:raise PolicyDenied("fictional profile field scope")
        with atomic(self.c):
            self._change("UPDATE users SET "+",".join(k+"=?" for k in updates)+" WHERE id=?", (*updates.values(),user))
            self._change("UPDATE subscriber_versions SET updated_ms=? WHERE user_id=?",(self.now(),user))
            self._suppress_current(user)

    def set_preferences(self,user,**updates):
        permitted={"pref_internship","pref_placement","pref_ppo"}
        if not updates or not set(updates)<=permitted or any(v not in (0,1) for v in updates.values()):
            raise PolicyDenied("fictional boolean preference scope")
        with atomic(self.c):
            self._change("UPDATE user_preferences SET "+",".join(k+"=?" for k in updates)+",updated_at=? WHERE user_id=?",
                         (*updates.values(),str(self.now()),user))
            self._change("UPDATE subscriber_versions SET updated_ms=? WHERE user_id=?",(self.now(),user))
            self._suppress_current(user)

    def delete_preferences(self,user):
        with atomic(self.c):
            self._change("DELETE FROM user_preferences WHERE user_id=?",(user,))
            self._change("UPDATE subscriber_versions SET updated_ms=? WHERE user_id=?",(self.now(),user))
            self._suppress_current(user)

    def _erase(self,target,approval,*,recipient,fault=None):
        self._operator(approval,'DEIDENTIFY' if recipient else 'ERASE',target)
        from app.database.protected_payloads import require_binding
        from app.database.key_provider import KeyUnavailable
        codec=require_binding(self.c)
        if codec is None:return self._erase_database(target,approval,recipient=recipient)
        if self.c.in_transaction:raise PolicyDenied('erasure owns transaction')
        def checkpoint(stage):
            if fault:fault(stage)
        checkpoint('before_prepare')
        table,column=('recipient_associations','recipient_key') if recipient else ('protected_payloads','payload_id')
        row=self._one('SELECT * FROM '+table+' WHERE '+column+'=?',(target,))
        prior=self._one('SELECT * FROM privacy_actions WHERE authorization_id=?',(approval.reference,))
        reference=row['key_reference'] if row else None
        if reference and reference!='fictional-key':
            context=codec.provider.expected(reference)
            if context.namespace!=codec.namespace or context.payload_id!=target or (recipient and context.purpose!='RECIPIENT'):
                raise KeyUnavailable('erasure context rejected')
            if recipient and context.owner_digest!=hashlib.sha256(b'ADMIN:'+target.encode()).hexdigest():
                raise KeyUnavailable('erasure recipient owner rejected')
            if recipient:codec.decrypt(context,row['protected_destination'],reference)
            else:codec.load(self.c,target)
            codec.provider.prepare_erasure(approval.reference,context,reference)
        elif prior:
            # Resume via durable external action after local references disappeared.
            try:reference=codec.provider.erasure(approval.reference)['reference']
            except KeyUnavailable:
                handle=self._one('SELECT role FROM recipient_handles WHERE recipient_key=?',(target,)) if recipient else None
                if not recipient or not handle or handle['role']=='ADMIN':raise
        elif row is None:raise KeyUnavailable('erasure target unavailable')
        checkpoint('prepared')
        checkpoint('before_database')
        self._erase_database(target,approval,recipient=recipient)
        checkpoint('database_committed')
        committed=self._one('SELECT * FROM privacy_actions WHERE authorization_id=?',(approval.reference,))
        if not committed or committed['target_key']!=target or committed['target_kind']!=('RECIPIENT' if recipient else 'PAYLOAD'):
            raise KeyUnavailable('erasure commit unresolved')
        if reference and reference!='fictional-key':
            action=codec.provider.erasure(approval.reference)
            context=action['context']
            if context['namespace']!=codec.namespace or context['payload_id']!=target or action['reference']!=reference:
                raise KeyUnavailable('erasure action conflict')
            checkpoint('before_revocation')
            try:acknowledged=codec.provider.revoke(approval.reference)
            except KeyUnavailable:return 'REVOCATION_PENDING'
            codec._grants.pop(reference,None)
            if not acknowledged or not codec.provider.erasure(approval.reference)['acknowledged']:return 'REVOCATION_PENDING'
            checkpoint('revoked')
        return 'COMPLETE'

    def _erase_database(self,target,approval,*,recipient):
        action="DEIDENTIFY" if recipient else "ERASE"
        self._operator(approval,action,target)
        kind="RECIPIENT" if recipient else "PAYLOAD"
        with atomic(self.c):
            prior=self._one("SELECT * FROM privacy_actions WHERE authorization_id=?",(approval.reference,))
            if prior:
                if prior["target_kind"]!=kind or prior["target_key"]!=target or prior["action"]!=action:raise PolicyDenied("privacy approval reuse")
                return
            self.c.execute("INSERT INTO privacy_actions VALUES(?,?,?,?,?,?,'F-S0')",
                (self.new_key(),approval.reference,kind,target,action,self.now()))
            if recipient:
                rows=self.c.execute("SELECT * FROM notification_deliveries_v2 WHERE recipient_key=? AND status IN('PENDING','PROCESSING','SENDING')",(target,)).fetchall()
            else:
                rows=self.c.execute("""SELECT DISTINCT d.* FROM notification_deliveries_v2 d
                    JOIN notification_intents i ON i.intent_id=d.intent_id
                    LEFT JOIN opportunity_events e ON e.event_id=i.source_event_id
                    WHERE (i.snapshot_payload_id=? OR e.before_payload_id=? OR e.after_payload_id=?)
                    AND d.status IN('PENDING','PROCESSING','SENDING')""",(target,target,target)).fetchall()
            for d in rows:self._suppress(dict(d),"PRIVACY_ERASURE")
            table,column=("recipient_associations","recipient_key") if recipient else ("protected_payloads","payload_id")
            self.c.execute("DELETE FROM "+table+" WHERE "+column+"=?", (target,))

    def erase_payload(self,target,approval,*,fault=None):
        return self._erase(target,approval,recipient=False,fault=fault)

    def erase_recipient(self,target,approval,*,fault=None):
        return self._erase(target,approval,recipient=True,fault=fault)

    def release_outbox(self,intent_id,channel,approval):
        target=intent_id+":"+channel
        self._operator(approval,"RELEASE_OUTBOX",target)
        with atomic(self.c):
            self._mode("fanout")
            box=self._one("SELECT * FROM event_outbox WHERE intent_id=? AND channel=?",(intent_id,channel))
            if not box or box["status"]!="HELD":raise PolicyDenied("only held outbox can be released")
            # The approval is supplied by a fictional external one-use operator adapter.
            marker="|AUTH:"+approval.reference
            if any(marker in (r[0] or "") for r in self.c.execute("SELECT hold_reason FROM event_outbox")):
                raise PolicyDenied("reused outbox release authorization")
            self._change("""UPDATE event_outbox SET status='PENDING',hold_reason=?,next_ms=?
                WHERE intent_id=? AND channel=? AND status='HELD'""",
                ((box["hold_reason"] or "")+marker,self.now(),intent_id,channel))
            # Preserve cumulative failure count/cap. Approval grants one resolved retry;
            # another expired preparation goes HELD again without a silent budget reset.

    def insert_preferences(self,user,*,pref_internship=1,pref_placement=1,pref_ppo=1):
        values=(pref_internship,pref_placement,pref_ppo)
        if any(type(v) is not int or v not in (0,1) for v in values):raise PolicyDenied("fictional boolean preferences")
        with atomic(self.c):
            self.c.execute("INSERT INTO user_preferences VALUES(?,?,?,?,?)",(user,*values,str(self.now())))
            self._change("UPDATE subscriber_versions SET updated_ms=? WHERE user_id=?",(self.now(),user))
            self._suppress_current(user)

    def supersede(self,earlier,later,approval):
        self._operator(approval,"SUPERSEDE",earlier+":"+later)
        with atomic(self.c):
            old=self._one("SELECT * FROM opportunity_events WHERE event_id=?",(earlier,))
            new=self._one("SELECT * FROM opportunity_events WHERE event_id=?",(later,))
            if not old or not new or old["company_key"]!=new["company_key"] or old["revision"]>=new["revision"]:
                raise PolicyDenied("forward same-company supersession required")
            self.c.execute("""INSERT INTO event_relations VALUES(?,?,?,'SUPERSEDES','F-S0',?)
                ON CONFLICT(earlier_event_id,later_event_id,relation) DO NOTHING""",(old["company_key"],earlier,later,self.now()))
            rows=self.c.execute("""SELECT d.* FROM notification_deliveries_v2 d JOIN notification_intents i
                ON i.intent_id=d.intent_id WHERE i.source_event_id=? AND d.status IN('PENDING','PROCESSING')""",(earlier,)).fetchall()
            for d in rows:self._suppress(dict(d),"AUDITED_SUPERSESSION")

    def catchup(self,disposition,authorization):
        with atomic(self.c):
            d=self._one("SELECT * FROM legacy_notification_dispositions WHERE disposition_id=?",(disposition,))
            if not d:raise PolicyDenied("missing historical disposition")
            self._approval(authorization,"LEGACY_CATCHUP",d["company_key"],disposition)
            existing=self._one("SELECT intent_id FROM notification_intents WHERE authorization_id=?",(authorization,))
            if existing:return existing["intent_id"]
            baseline=self._one("SELECT snapshot_payload_id FROM opportunity_state WHERE company_key=?",(d["company_key"],))
            key=self.new_key()
            self.c.execute("""INSERT INTO notification_intents
                (intent_id,company_key,kind,authorization_id,legacy_disposition_id,snapshot_payload_id,
                 policy_version,config_version,created_ms) VALUES(?,?,'LEGACY_CATCHUP',?,?,?,'F-S0','F-S0',?)""",
                (key,d["company_key"],authorization,disposition,baseline["snapshot_payload_id"],self.now()))
            self._outboxes(key,held_channel="ADMIN")
            self.c.execute("UPDATE event_outbox SET status='HELD',hold_reason='LEGACY_PROVENANCE_HOLD' WHERE intent_id=? AND channel='SUBSCRIBER'",(key,))
            return key
