"""
Production email presentation and data formatting for TPO-WATCHER.
Guarantees:
- Clean, emoji-free subjects with RFC 2047 UTF-8 safety and no CRLF header injection.
- Truthful data rendering: no fabricated units or dates.
- Clean handling of registration dates, eliminating 'N/A to N/A'.
- Clear compensation representation without repeating identical min/max values.
- Clean internship type presentation, omitting redundant rows when unstated.
- Fully accessible, responsive, table-based HTML email design.
- Complete text/plain MIME fallback alternative.
"""

from datetime import datetime, timezone, timedelta, time
import re
from typing import Any, Dict, List, Optional, Tuple

from app.notifications.utils import escape_html, sanitize_url, render_safe_link

IST = timezone(timedelta(hours=5, minutes=30))
_MONTHS = {
    m: i for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


def format_opportunity_subject(notif_type: str, company_name: Optional[str]) -> str:
    """
    Format clean, professional email subject without emojis.
    Strips carriage returns/newlines to prevent header injection.
    """
    name = " ".join(str(company_name or "TPO Opportunity").split())
    if str(notif_type).upper() == "NEW":
        return f"New TPO Opportunity: {name} | TPO-WATCHER"
    return f"TPO Opportunity Updated: {name} | TPO-WATCHER"


def clean_amount(val: Any) -> Optional[str]:
    """
    Converts raw numeric value (e.g. 25000.0 or '25000') into thousands-formatted string '25,000'.
    Returns None if absent, zero, or N/A.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.upper() in ("N/A", "NONE", "NULL"):
        return None
    try:
        f = float(s)
        if f <= 0:
            return None
        if f.is_integer():
            return f"{int(f):,}"
        return f"{f:,.2f}"
    except (ValueError, TypeError):
        # Return string if not standard float, preserving non-numeric descriptions
        return s


def format_compensation(
    max_pkg: Any,
    min_pkg: Any,
    unit: Optional[str] = None,
    unit_evidence: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Format compensation cleanly.
    - If min == max, displays a single number (e.g. '25,000') instead of repeating.
    - If unit is verified (with explicit evidence), shows human-readable unit.
    - If unit is unverified (standard TPO portal), explicitly notes that the unit is not specified.
    - If absent/zero, clearly indicates details are not available.
    Returns (display_value, detail_note).
    """
    max_clean = clean_amount(max_pkg)
    min_clean = clean_amount(min_pkg)

    if max_clean and min_clean:
        if max_clean == min_clean:
            amount_str = max_clean
        else:
            amount_str = f"{min_clean} - {max_clean}"
    elif max_clean:
        amount_str = max_clean
    elif min_clean:
        amount_str = min_clean
    else:
        return ("Not disclosed by company on portal", "Check official portal for updates.")

    # Check for verified unit metadata
    has_verified_unit = (
        unit_evidence
        and unit
        and str(unit).strip().lower() in ("lpa", "monthly stipend", "annual salary", "per month", "stipend")
    )

    if has_verified_unit:
        u_lower = str(unit).strip().lower()
        if u_lower == "lpa":
            return (f"Rs. {amount_str} LPA", "Annual package (verified)")
        elif "month" in u_lower or "stipend" in u_lower:
            return (f"Rs. {amount_str} per month", "Stipend (verified)")
        else:
            return (f"Rs. {amount_str} ({unit})", "Verified unit")

    # Standard unverified portal data
    return (
        f"{amount_str}",
        "Unit not specified by TPO - please verify on official portal"
    )


def _parse_date_candidate(
    date_val: Optional[str],
    time_val: Optional[str],
    iso_val: Optional[str],
) -> Tuple[Optional[str], Optional[datetime]]:
    """Helper to parse candidate date/time strings and return (display_str, ist_datetime)."""
    if date_val and str(date_val).strip() and str(date_val).strip().upper() not in ("N/A", "NONE", "NULL"):
        d_str = str(date_val).strip()
        t_str = str(time_val or "").strip()
        display = f"{d_str} {t_str}".strip()
        dt_obj = None
        m = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", d_str)
        if m and m[2].lower() in _MONTHS:
            try:
                day, month, year = int(m[1]), _MONTHS[m[2].lower()], int(m[3])
                tm = time(23, 59, 59)
                if t_str and re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", t_str):
                    parts = t_str.split(":")
                    tm = time(int(parts[0]), int(parts[1]))
                dt_obj = datetime(year, month, day, tm.hour, tm.minute, tm.second, tzinfo=IST)
            except Exception:
                pass
        return display, dt_obj

    if iso_val and str(iso_val).strip():
        try:
            iso_str = str(iso_val).strip().upper().replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ist_dt = dt.astimezone(IST)
            display = ist_dt.strftime("%d-%b-%Y %H:%M")
            return display, ist_dt
        except Exception:
            pass

    return None, None


def format_registration_window(record: Any) -> Dict[str, Any]:
    """
    Safely inspect all available registration date fields.
    Checks regStartdate/regStarttime, regEnddate/regEndtime,
    and fallback to regStartdatenew/regEnddatenew.
    Returns:
      {
        'display_text': str,
        'is_closed': bool,
        'badge': Optional[str],
        'note': Optional[str]
      }
    """
    def _get(attr: str) -> Optional[str]:
        if isinstance(record, dict):
            return record.get(attr)
        return getattr(record, attr, None)

    start_str, start_dt = _parse_date_candidate(
        _get("regStartdate"), _get("regStarttime"), _get("regStartdatenew")
    )
    end_str, end_dt = _parse_date_candidate(
        _get("regEnddate"), _get("regEndtime"), _get("regEnddatenew")
    )

    now_ist = datetime.now(IST)
    is_closed = False
    badge = None
    if end_dt is not None and end_dt < now_ist:
        is_closed = True
        badge = "Registration Closed"

    if start_str and end_str:
        return {
            "display_text": f"{start_str} to {end_str}",
            "is_closed": is_closed,
            "badge": badge,
            "note": "Official schedule reported by TPO",
        }
    elif end_str:
        return {
            "display_text": f"Closes: {end_str}",
            "is_closed": is_closed,
            "badge": badge,
            "note": "Opening date not specified by TPO",
        }
    elif start_str:
        return {
            "display_text": f"Opens: {start_str}",
            "is_closed": False,
            "badge": None,
            "note": "Deadline not specified by TPO — verify on portal",
        }
    else:
        return {
            "display_text": "Registration dates not provided by TPO",
            "is_closed": False,
            "badge": None,
            "note": "Please check the official TPO portal for schedule updates.",
        }


def format_internship_type(internshiptype: Optional[str]) -> Optional[str]:
    """
    Returns sanitized internship type string if meaningful, or None if unstated/redundant.
    Prevents displaying 'N/A' or 'Not mentioned.' directly below placement type.
    """
    if not internshiptype:
        return None
    val = str(internshiptype).strip()
    if val.lower() in ("none", "n/a", "not mentioned.", "null", "not specified"):
        return None
    return val


def clean_program_name(p: str) -> Optional[str]:
    """
    Cleans an individual program token for scannable display.
    Returns None if the token represents an invalid ERP artifact (e.g. 'VIT-null').
    Preserves distinct institutional identifiers (VIIT, VU) when present, while stripping
    redundant VIT- prefix and degree prefixes (B.Tech-, etc.) for clear readability.
    """
    raw = str(p or "").strip()
    if not raw:
        return None
    # Filter out demonstrably invalid source artifacts:
    # Matches 'null', 'none', 'undefined', 'n/a' either alone or with prefixes/suffixes like 'VIT-null'
    if re.search(r"(?:^|[-_\s/])(?:null|none|undefined|n/?a)(?:$|[-_\s/])", raw, re.IGNORECASE):
        return None

    # Preserve distinct sister institutional prefixes (VIIT, VU)
    inst_match = re.match(r"^(VIIT|VU)\s*[-–\.]\s*", raw, flags=re.IGNORECASE)
    inst_prefix = ""
    if inst_match:
        inst_prefix = f"{inst_match.group(1).upper()} - "
        s = raw[inst_match.end():].strip()
    else:
        # Strip default VIT- prefix
        s = re.sub(r"^VIT\s*[-–\.]\s*", "", raw, flags=re.IGNORECASE).strip()

    # Strip redundant degree prefixes (B.Tech-, BTech-, B.Tech. , Bachelor of Technology -)
    s = re.sub(r"^(?:B\.?Tech\.?|Bachelor of Technology)\s*[-–\.]?\s*", "", s, flags=re.IGNORECASE).strip()
    clean_title = " ".join(s.split()) or raw
    return f"{inst_prefix}{clean_title}" if inst_prefix else clean_title


def format_eligible_programs(programs_raw: Any) -> Tuple[List[str], str]:
    """
    Parses, cleans, deduplicates, and formats eligible programs for display.
    - Strips invalid ERP artifacts like 'VIT-null'.
    - Preserves distinct institutional branches (e.g. VIIT vs VIT).
    - Deduplicates redundant program listings.
    - Provides a concise guidance note directing students to the official portal.
    Returns (cleaned_programs_list, reminder_note).
    """
    if not programs_raw:
        return ([], "Please check the official VIT TPO portal for eligible branches.")

    if isinstance(programs_raw, (list, tuple)):
        raw_items = list(programs_raw)
    else:
        # Split on commas or semicolons
        raw_items = re.split(r"[,;]+", str(programs_raw))

    def _dedup_key(name: str) -> str:
        k = name.lower().replace("&", "and")
        k = re.sub(r"[()\-_–\.,]+", " ", k)
        k = re.sub(r"\b(?:engineering|engg|and)\b", " ", k)
        return " ".join(k.split())

    seen = set()
    cleaned_list = []
    for it in raw_items:
        clean = clean_program_name(it)
        if not clean:
            continue
        key = _dedup_key(clean)
        if key not in seen:
            seen.add(key)
            cleaned_list.append(clean)

    note = "Branch match indicates programme relevance only; full eligibility must be verified on the official TPO portal."
    return (cleaned_list, note)


def render_opportunity_html(
    *,
    company_name: str,
    company_code: str,
    placement_type: str,
    internship_type: Optional[str],
    compensation_main: str,
    compensation_note: str,
    reg_window_text: str,
    reg_badge: Optional[str],
    reg_note: Optional[str],
    academic_year: str,
    organization: str,
    user_branch: str,
    programs: str,
    notif_type: str,
    now_ist_str: str,
    portal_url: str,
    pref_url: str,
    unsub_url: str,
    changes: Optional[List[str]] = None,
    is_active_raw: Optional[str] = None,
    channel: str = "subscriber",
) -> str:
    """
    Renders professional, responsive HTML email compatible with Gmail, Apple Mail, Outlook.
    Escapes all dynamic values to prevent XSS.
    """
    esc = escape_html
    safe_pref = sanitize_url(pref_url) or "#"
    safe_unsub = sanitize_url(unsub_url) or "#"
    safe_portal = sanitize_url(portal_url) or "https://tpo.vierp.in/company-dashboard"

    is_new = str(notif_type).upper() == "NEW"
    badge_label = "NEW OPPORTUNITY" if is_new else "OPPORTUNITY UPDATED"
    badge_bg = "#064e3b" if is_new else "#78350f"
    badge_color = "#34d399" if is_new else "#fbbf24"
    badge_border = "#059669" if is_new else "#d97706"

    # Internship row (omitted if None)
    internship_row_html = ""
    if internship_type:
        internship_row_html = f"""
                  <tr style="border-bottom: 1px solid #f1f5f9;">
                    <td style="padding: 10px 0; color: #64748b; font-size: 13px; font-weight: 600;">Internship Duration</td>
                    <td style="padding: 10px 0; font-size: 14px; color: #0f172a; text-align: right; font-weight: 600;">{esc(internship_type)}</td>
                  </tr>
        """

    # Compensation note
    comp_note_html = ""
    if compensation_note:
        comp_note_html = f'<div style="font-size: 11px; color: #64748b; margin-top: 2px;">{esc(compensation_note)}</div>'

    # Registration badge
    reg_badge_html = ""
    if reg_badge:
        reg_badge_html = f'<div style="display: inline-block; background-color: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 4px; margin-top: 4px;">{esc(reg_badge)}</div>'

    reg_note_html = ""
    if reg_note:
        reg_note_html = f'<div style="font-size: 11px; color: #64748b; margin-top: 2px;">{esc(reg_note)}</div>'

    # Changes box (for UPDATE notifications)
    changes_box_html = ""
    if not is_new:
        items = []
        if is_active_raw is not None:
            raw_str = str(is_active_raw).strip().lower()
            if raw_str in ("true", "1", "yes"):
                items.append("<strong>Active Status: True</strong> (Company Active)")
            elif raw_str in ("false", "0", "no"):
                items.append("<strong>Active Status: False</strong> (Registration Closed / Company Inactive)")
        if changes:
            for c in changes:
                if "Active Status" not in c:
                    items.append(esc(c))
        if items:
            items_li = "".join(f"<li style='margin: 4px 0;'>{it}</li>" for it in items)
            changes_box_html = f"""
            <tr>
              <td style="padding: 0 24px 16px 24px;">
                <div style="background-color: #fffbeb; border: 1px solid #fde68a; border-left: 4px solid #f59e0b; border-radius: 6px; padding: 12px 16px; font-size: 13px;">
                  <strong style="color: #92400e;">Update Details Detected:</strong>
                  <ul style="margin: 6px 0 0 0; padding-left: 20px; color: #78350f;">
                    {items_li}
                  </ul>
                </div>
              </td>
            </tr>
            """

    # Portal CTA Button
    portal_button = render_safe_link(
        safe_portal,
        "Open Official TPO Portal",
        style="background-color: #1e40af; color: #ffffff !important; border: 2px solid #1e40af; text-decoration: none; padding: 13px 28px; border-radius: 6px; font-weight: 700; font-size: 14px; display: inline-block; letter-spacing: 0.02em;"
    )

    detected_label = "Detected:" if channel == "admin" else "Detected at:"

    # Personalization block (only show if branch is provided)
    branch_block_html = ""
    if user_branch and channel == "subscriber":
        branch_block_html = f"""
            <tr>
              <td style="padding: 0 24px 16px 24px;">
                <div style="background-color: #eff6ff; border: 1px solid #bfdbfe; border-left: 4px solid #2563eb; border-radius: 6px; padding: 12px 16px;">
                  <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: #1e40af; letter-spacing: 0.04em;">Your Matched Branch</div>
                  <div style="font-size: 14px; font-weight: 700; color: #1e3a8a; margin-top: 2px;">{esc(user_branch)}</div>
                </div>
              </td>
            </tr>
        """

    # Format eligible programs cleanly, dropping invalid artifacts like VIT-null
    cleaned_progs, progs_note = format_eligible_programs(programs)
    if cleaned_progs:
        badge_spans = "".join(
            f'<span style="display: inline-block; background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 4px; padding: 3px 8px; font-size: 12px; color: #1e293b; margin: 2px 4px 3px 0; font-weight: 500;">{esc(p)}</span>'
            for p in cleaned_progs
        )
        programs_box_html = f"""
          <tr>
            <td style="padding: 0 24px 16px 24px;">
              <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px 16px;">
                <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: #475569; letter-spacing: 0.04em; margin-bottom: 6px;">Eligible Programs:</div>
                <div style="line-height: 1.8;">
                  {badge_spans}
                </div>
                <div style="font-size: 11px; color: #64748b; margin-top: 8px; line-height: 1.4; border-top: 1px dashed #e2e8f0; padding-top: 6px;">
                  {esc(progs_note)}
                </div>
              </div>
            </td>
          </tr>
        """
    else:
        programs_box_html = f"""
          <tr>
            <td style="padding: 0 24px 16px 24px;">
              <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px 16px;">
                <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: #475569; letter-spacing: 0.04em; margin-bottom: 4px;">Eligible Programs:</div>
                <div style="font-size: 12px; color: #64748b; font-style: italic;">{esc(progs_note)}</div>
              </div>
            </td>
          </tr>
        """

    footer_html = f"""
            <tr>
              <td style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 20px 24px; text-align: center; font-size: 12px; color: #64748b; line-height: 1.6;">
                <p style="margin: 0 0 8px 0;">You are receiving this automated alert because you subscribed to TPO-WATCHER for the VIT Pune 2028 placement cycle.</p>
                <p style="margin: 0 0 12px 0; font-style: italic; color: #475569;">Branch programme match does not confirm full eligibility. Please verify eligibility criteria and application requirements on the official VIT TPO portal before applying.</p>
                <p style="margin: 0;">
                  <a href="{safe_pref}" style="color: #2563eb; text-decoration: none; font-weight: 600;">Manage Preferences</a>
                  &nbsp;&nbsp;•&nbsp;&nbsp;
                  <a href="{safe_unsub}" style="color: #dc2626; text-decoration: none; font-weight: 600;">Unsubscribe</a>
                </p>
              </td>
            </tr>
    """ if channel == "subscriber" else f"""
            <tr>
              <td style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 16px 24px; text-align: center; font-size: 12px; color: #64748b; line-height: 1.5;">
                <p style="margin: 0 0 6px 0;">Internal Administrative Dispatch Notification.</p>
                <p style="margin: 0; font-style: italic; color: #475569;">Branch programme match does not confirm full eligibility. Please verify eligibility criteria and application requirements on the official VIT TPO portal before any action.</p>
              </td>
            </tr>
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <meta name="color-scheme" content="light dark"/>
  <title>{esc(company_name)} - TPO-WATCHER</title>
  <style>
    @media print {{
      * {{
        -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important;
      }}
      body, table {{
        background-color: #ffffff !important;
      }}
      .email-card {{
        border: 1px solid #cbd5e1 !important;
        box-shadow: none !important;
        page-break-inside: avoid !important;
      }}
      tr {{
        page-break-inside: avoid !important;
      }}
      .header-bar {{
        background-color: #0f172a !important;
        color: #ffffff !important;
      }}
    }}
  </style>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased; color: #1e293b;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8fafc; padding: 24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="email-card" style="max-width: 600px; background-color: #ffffff; border-radius: 10px; overflow: hidden; border: 1px solid #e2e8f0; box-shadow: 0 4px 12px rgba(15, 23, 42, 0.05);">
          <!-- Header Bar -->
          <tr class="header-bar">
            <td style="background-color: #0f172a; padding: 18px 24px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td>
                    <span style="font-size: 14px; font-weight: 800; color: #ffffff; letter-spacing: 0.08em; text-transform: uppercase;">TPO-WATCHER</span>
                    <div style="font-size: 11px; color: #94a3b8; margin-top: 3px; font-weight: 500;">Autonomous Placement Intelligence • VIT Pune 2028</div>
                  </td>
                  <td align="right">
                    <span style="display: inline-block; background-color: {badge_bg}; color: {badge_color}; border: 1px solid {badge_border}; font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 9999px; text-transform: uppercase; letter-spacing: 0.04em;">{badge_label}</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <!-- Company Headline -->
          <tr>
            <td style="padding: 24px 24px 16px 24px;">
              <h1 style="margin: 0; font-size: 22px; font-weight: 800; color: #0f172a; line-height: 1.25;">{esc(company_name)}</h1>
              <div style="margin-top: 6px; font-size: 13px; color: #64748b; font-weight: 500;">
                <span style="background-color: #f1f5f9; padding: 2px 7px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: #334155;">{esc(company_code)}</span>
                &nbsp;•&nbsp; Academic Year: <strong style="color: #334155;">{esc(academic_year)}</strong>
              </div>
            </td>
          </tr>
          {branch_block_html}
          <!-- Key Details Table -->
          <tr>
            <td style="padding: 0 24px 16px 24px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse: collapse;">
                <tr style="border-bottom: 1px solid #f1f5f9;">
                  <td style="padding: 10px 0; color: #64748b; font-size: 13px; font-weight: 600; width: 38%;">Placement Type</td>
                  <td style="padding: 10px 0; font-size: 14px; color: #0f172a; font-weight: 600; text-align: right;">{esc(placement_type)}</td>
                </tr>
                {internship_row_html}
                <tr style="border-bottom: 1px solid #f1f5f9;">
                  <td style="padding: 10px 0; color: #64748b; font-size: 13px; font-weight: 600; vertical-align: top;">Compensation</td>
                  <td style="padding: 10px 0; font-size: 14px; text-align: right;">
                    <div style="color: #15803d; font-weight: 700;">{esc(compensation_main)}</div>
                    {comp_note_html}
                  </td>
                </tr>
                <tr style="border-bottom: 1px solid #f1f5f9;">
                  <td style="padding: 10px 0; color: #64748b; font-size: 13px; font-weight: 600; vertical-align: top;">Registration Window</td>
                  <td style="padding: 10px 0; font-size: 13px; text-align: right;">
                    <div style="font-weight: 600; color: #0f172a;">{esc(reg_window_text)}</div>
                    {reg_badge_html}
                    {reg_note_html}
                  </td>
                </tr>
                <tr style="border-bottom: 1px solid #f1f5f9;">
                  <td style="padding: 10px 0; color: #64748b; font-size: 13px; font-weight: 600;">Organization</td>
                  <td style="padding: 10px 0; font-size: 13px; color: #334155; text-align: right;">{esc(organization)}</td>
                </tr>
              </table>
            </td>
          </tr>
          {programs_box_html}
          {changes_box_html}
          <!-- CTA Action -->
          <tr>
            <td align="center" style="padding: 12px 24px 18px 24px;">
              {portal_button}
              <div style="font-size: 12px; color: #94a3b8; margin-top: 14px;">{detected_label} {esc(now_ist_str)}</div>
            </td>
          </tr>
          {footer_html}
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def render_opportunity_text(
    *,
    company_name: str,
    company_code: str,
    placement_type: str,
    internship_type: Optional[str],
    compensation_main: str,
    compensation_note: str,
    reg_window_text: str,
    reg_badge: Optional[str],
    academic_year: str,
    organization: str,
    user_branch: str,
    programs: str,
    notif_type: str,
    now_ist_str: str,
    portal_url: str,
    pref_url: str,
    unsub_url: str,
    changes: Optional[List[str]] = None,
    channel: str = "subscriber",
) -> str:
    """
    Renders clean, structured plain-text MIME alternative.
    """
    is_new = str(notif_type).upper() == "NEW"
    title = "NEW TPO OPPORTUNITY" if is_new else "TPO OPPORTUNITY UPDATED"

    lines = [
        "============================================================",
        f"TPO-WATCHER | {title}",
        "============================================================",
        f"Company: {company_name} ({company_code})",
        f"Academic Year: {academic_year}",
        f"Organization: {organization}",
        "",
    ]

    if user_branch and channel == "subscriber":
        lines.extend([
            "YOUR MATCHED BRANCH:",
            f"  {user_branch}",
            "",
        ])

    lines.extend([
        "OPPORTUNITY DETAILS:",
        f"  - Placement Type: {placement_type}",
    ])

    if internship_type:
        lines.append(f"  - Internship Duration: {internship_type}")

    comp_line = f"  - Compensation: {compensation_main}"
    if compensation_note:
        comp_line += f" ({compensation_note})"
    lines.append(comp_line)

    reg_line = f"  - Registration Window: {reg_window_text}"
    if reg_badge:
        reg_line += f" [{reg_badge}]"
    lines.append(reg_line)

    cleaned_progs, progs_note = format_eligible_programs(programs)
    lines.extend([
        "",
        "ELIGIBLE PROGRAMS:",
    ])
    if cleaned_progs:
        for p in cleaned_progs:
            lines.append(f"  - {p}")
        lines.append(f"  ({progs_note})")
    else:
        lines.append(f"  {progs_note}")
    lines.append("")

    if changes:
        lines.extend([
            "UPDATED FIELDS DETECTED:",
            *[f"  - {c}" for c in changes],
            "",
        ])

    detected_label = "Detected:" if channel == "admin" else "Detected at:"
    lines.extend([
        "OFFICIAL PORTAL ACTION:",
        f"  Open official TPO portal to verify and apply:",
        f"  {portal_url}",
        "",
        f"{detected_label} {now_ist_str}",
        "",
        "------------------------------------------------------------",
        "DISCLAIMER:",
        "Branch programme match does not confirm full eligibility. Please verify",
        "eligibility criteria and application requirements on the official VIT TPO",
        "portal before applying.",
    ])

    if channel == "subscriber":
        lines.extend([
            "",
            "You are receiving this automated alert because you subscribed to TPO-WATCHER for the VIT Pune 2028 placement cycle.",
            "",
            "MANAGE PREFERENCES & SUBSCRIPTION:",
            f"  Manage Preferences: {pref_url}",
            f"  Unsubscribe: {unsub_url}",
        ])

    lines.extend([
        "============================================================",
        "",
    ])

    return "\n".join(lines)
