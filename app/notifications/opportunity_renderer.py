"""Pure immutable-opportunity rendering. Caller supplies every clock and link."""
from dataclasses import dataclass
from datetime import datetime, timezone
from app.notifications.utils import escape_html, render_safe_link


@dataclass(frozen=True)
class RenderedOpportunity:
    subject: str
    html: str
    observed_ms: int
    persisted_ms: int
    rendered_ms: int


def _time(milliseconds):
    if type(milliseconds) is not int or milliseconds<0:
        raise ValueError("nonnegative supplied clock required")
    return datetime.fromtimestamp(milliseconds/1000,timezone.utc).isoformat()


def render_opportunity(*,snapshot,reasons,role,channel,branch,observed_ms,persisted_ms,rendered_ms,links=None):
    record=snapshot.get("record")
    if not isinstance(record,dict):
        raise ValueError("immutable record snapshot required")
    esc=escape_html
    name=record.get("company") or "Source opportunity"
    title="NEW opportunity" if "NEW" in reasons else "Opportunity UPDATE"
    state=snapshot.get("state")
    if state=="ABSENT":
        lifecycle="Source record no longer observed in an explicitly verified complete feed. This does not establish employer registration closure."
    else:
        lifecycle="Source record observed. Visibility does not establish that employer registration is open or reopened."
    raw_times=[]
    for label,date_field,time_field,alternate in (
        ("Registration start","regStartdate","regStarttime","regStartdatenew"),
        ("Registration end","regEnddate","regEndtime","regEnddatenew")):
        raw=" ".join(str(record.get(k) or "not reported") for k in (date_field,time_field))
        raw_times.append("<p>"+esc(label)+": "+esc(raw)+" (source-reported; "+
            ("timezone verified by explicit source evidence" if snapshot.get("verified_deadline") is True and snapshot.get("timezone_evidence") else "timezone unverified")+")</p>")
        if record.get(alternate):
            raw_times.append("<p>Alternate source field: "+esc(record[alternate])+"</p>")
    compensation=[]
    for label,key in (("Maximum compensation","maxPackage"),("Minimum compensation","minPackage")):
        if record.get(key):
            unit=record.get("compensation_unit")
            provenance=record.get("compensation_unit_evidence")
            qualified=unit if provenance and unit in ("LPA","monthly stipend","annual salary") else "unit unknown"
            compensation.append("<p>"+esc(label)+": "+esc(record[key])+" ("+esc(qualified)+")</p>")
    eligibility=("Your programme appears to match the source listing. This does not confirm full eligibility. "
        "Verify academic, batch, employer, CGPA and all other eligibility criteria on the official TPO portal.")
    details=[]
    for key,label in (("job_title","Role"),("job_description","Role details"),
        ("placementtype","Placement classification"),("internshiptype","Internship classification"),
        ("organization","Source institutions"),("academicyear","Source academic year"),
        ("graduation_year","Source graduation year"),("cgpa","Source CGPA requirement"),
        ("backlog_restrictions","Source backlog restrictions"),("eligibility_conditions","Employer conditions"),
        ("remarks","Source remarks")):
        if record.get(key) is not None:details.append("<p>"+esc(label)+": "+esc(record[key])+"</p>")
    content=[
        "<h1>"+esc(title)+"</h1>","<h2>"+esc(name)+"</h2>","<p>"+esc(lifecycle)+"</p>",
        "<p>Source-reported active flag: "+esc(record.get("isactive") or "unknown")+
        "; official registration status requires verification.</p>",
        *raw_times,*compensation,*details,
        "<p>Primary programmes: "+esc(record.get("tpoprogram") or "not reported")+"</p>",
        "<p>Secondary programmes: "+esc(record.get("programnew") or "not reported")+"</p>",
        "<p>Matched branch: "+esc(branch or "administrative notice")+"</p>",
        "<p>"+esc(eligibility)+"</p>",
        "<p>Notice role: "+esc(role)+". Verify deadline and eligibility before acting.</p>",
        "<p>Changes: "+esc("; ".join(str(x) for x in reasons))+"</p>",
        "<p>Source observed at: "+esc(_time(observed_ms))+"</p>",
        "<p>Detected/persisted at: "+esc(_time(persisted_ms))+"</p>",
        "<p>Rendered at: "+esc(_time(rendered_ms))+"</p>",
        render_safe_link("https://tpo.vierp.in/company-dashboard","Verify on the official TPO portal"),
    ]
    for key,label in (("preferences","Manage preferences"),("unsubscribe","Unsubscribe")):
        if links and links.get(key):content.append(render_safe_link(links[key],label))
    # Header content is plain text; remove CR/LF even though no real transport is allowed.
    subject=" ".join((title+": "+str(name)).split())
    return RenderedOpportunity(subject,"<!doctype html><html><body>"+"".join(content)+"</body></html>",
                               observed_ms,persisted_ms,rendered_ms)

