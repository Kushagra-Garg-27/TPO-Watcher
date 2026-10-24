"""Read-only public projection; construction never installs a schema."""
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3


@contextmanager
def read_projection(path):
    connection=sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def listing_projection(path,*,now_ms,limit=5):
    from app.tpo.listing import ListingContract
    from app.database.protected_payloads import require_binding
    from app.subscribers.canonical import extract_eligible_canonical_branches
    if type(now_ms) is not int or now_ms<0 or type(limit) is not int or not 1<=limit<=100:
        raise ValueError("bounded projection clock/limit required")
    with read_projection(path) as c:
        row=c.execute("SELECT value FROM system_state WHERE key='notification:listing-contract'").fetchone()
        if row is None:
            return dict(items=[],total=0,shown=0,status="UNVERIFIED",observed_ms=None)
        policy=ListingContract(**json.loads(row[0]))
        codec=require_binding(c)
        if codec:
            # Authenticate this read snapshot without starting a mutation unit,
            # warming unrelated private payloads, or changing an external journal.
            codec.provider.check_database(codec.namespace,codec.fingerprint(c))
        rows=c.execute("""SELECT c.*,s.company_key FROM companies c JOIN opportunity_state s
            ON s.legacy_company_id=c.id ORDER BY c.first_seen_at DESC,c.id DESC""").fetchall()
        items=[]
        latest=None
        recent=False
        for row in rows:
            evidence=c.execute("SELECT value FROM system_state WHERE key=?",
                ("notification:listing:"+row["company_key"],)).fetchone()
            if evidence is None:continue
            evidence=json.loads(evidence[0])
            observed=evidence["observed_ms"]
            latest=max(latest or 0,observed)
            fresh=type(observed) is int and 0<=now_ms-observed<=policy.freshness_ms
            recent=recent or fresh
            if not fresh or evidence["state"]!="OBSERVED" or str(row["is_active"]).strip().casefold()!="true":continue
            raw=json.loads(row["raw_data_json"])
            if not extract_eligible_canonical_branches(raw.get("tpoprogram"),raw.get("programnew"),raw.get("organization")):
                continue
            item={key:row[key] for key in ("id","company","max_package","min_package","placement_type",
                "registration_end","company_type","is_active","first_seen_at")}
            unit=raw.get("compensation_unit")
            if unit not in ("LPA","monthly stipend","annual salary") or not raw.get("compensation_unit_evidence"):unit=None
            item.update(eligible_programs=", ".join(dict.fromkeys(filter(None,(raw.get("tpoprogram"),raw.get("programnew"))))),
                organizations=raw.get("organization"),registration_start=raw.get("regStartdate"),
                registration_start_time=raw.get("regStarttime"),registration_end_time=raw.get("regEndtime"),
                registration_start_alternate=raw.get("regStartdatenew"),registration_end_alternate=raw.get("regEnddatenew"),
                deadline_timezone=policy.source_timezone,compensation_unit=unit,
                job_title=raw.get("job_title"),job_description=raw.get("job_description"),
                academic_year=raw.get("academicyear"),listing_status="CURRENT",
                observed_ms=observed,valid_until_ms=observed+policy.freshness_ms,
                eligibility_status="PROGRAMME_MATCH_ONLY")
            items.append(item)
        return dict(items=items[:limit],total=len(items),shown=min(limit,len(items)),
            status="VERIFIED" if recent else "STALE" if latest is not None else "UNVERIFIED",
            observed_ms=latest)


def legacy_projection(path,*,limit=5,now_ms=None):
    # Installed current-source policy also governs the compatibility endpoint.
    with read_projection(path) as c:
        bound=c.execute("SELECT 1 FROM system_state WHERE key='notification:listing-contract'").fetchone()
        if not bound:return [dict(row) for row in c.execute("""SELECT id,company,max_package,min_package,
            placement_type,registration_end,eligible_programs,company_type,is_active,first_seen_at
            FROM companies WHERE is_active='True' ORDER BY first_seen_at DESC,id DESC LIMIT ?""",(limit,))]
    return listing_projection(path,now_ms=now_ms,limit=limit)["items"]
