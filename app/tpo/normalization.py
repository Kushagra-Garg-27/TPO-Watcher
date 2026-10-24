"""Pure source comparisons; no implied timezone, eligibility or storage contract."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Mapping, Optional


def normalize_token(value: str) -> str:
    """Normalize presentation only, retaining qualification and specializations."""
    text = " ".join(value.split()).casefold()
    text = re.sub(r"\s*-\s*", "-", text)
    return re.sub(r"\s*([()])\s*", r"\1", text)


@dataclass(frozen=True)
class TokenField:
    tokens: tuple[str, ...]
    malformed: bool = False
    evidence: str = ""

    @property
    def signature(self) -> tuple:
        return ("malformed", self.evidence) if self.malformed else ("tokens", self.tokens)


def tokenize_programmes(value: Optional[str]) -> TokenField:
    """Split whole tokens, never commas inside a specialization."""
    if value is None or not str(value).strip():
        return TokenField(())
    if not isinstance(value, str):
        return TokenField((), True, repr(value))
    parts, start, depth = [], 0, 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return TokenField((), True, normalize_token(value))
        elif char in ",;" and depth == 0:
            part = value[start:index].strip()
            if not part:
                return TokenField((), True, normalize_token(value))
            parts.append(normalize_token(part))
            start = index + 1
    last = value[start:].strip()
    if depth or not last:
        return TokenField((), True, normalize_token(value))
    parts.append(normalize_token(last))
    return TokenField(tuple(sorted(set(parts))))


def normalize_organizations(value: Optional[str]) -> TokenField:
    field = tokenize_programmes(value)
    if not field.malformed and any(not re.fullmatch(r"[a-z][a-z0-9_ ]*", token)
                                   for token in field.tokens):
        return TokenField((), True, normalize_token(str(value)))
    return field


@dataclass(frozen=True)
class RegistrationValue:
    raw: Any
    status: str
    value: Any = None
    precision: Optional[str] = None
    timezone_provenance: Optional[str] = None

    @property
    def signature(self) -> tuple:
        # Zero seconds are cosmetic; date-only never becomes midnight.
        if self.status == "unparsed":
            return (self.status, " ".join(str(self.raw).split()).casefold())
        # Native aware-datetime equality compares explicit-offset instants
        # without materializing a UTC year outside datetime's supported range.
        return (self.status, self.value)


_MONTHS = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_CLOCK = r"\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?"
_OFFSET = r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?"


def normalize_registration(value: Any, *, clock: bool = False) -> RegistrationValue:
    if value is None or (isinstance(value, str) and not value.strip()):
        return RegistrationValue(value, "missing")
    if not isinstance(value, str):
        return RegistrationValue(value, "unparsed")
    text = value.strip()
    try:
        if clock and re.fullmatch(_CLOCK + _OFFSET, text, re.IGNORECASE):
            parsed = time.fromisoformat(text.upper().replace("Z", "+00:00"))
        elif not clock and re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt ]" + _CLOCK + _OFFSET,
                                       text, re.IGNORECASE):
            parsed = datetime.fromisoformat(text.upper().replace("Z", "+00:00"))
        elif not clock and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return RegistrationValue(value, "date", date.fromisoformat(text), "date")
        elif not clock:
            match = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", text)
            if not match or match[2].casefold() not in _MONTHS:
                return RegistrationValue(value, "unparsed")
            return RegistrationValue(value, "date",
                                     date(int(match[3]), _MONTHS[match[2].casefold()], int(match[1])), "date")
        else:
            return RegistrationValue(value, "unparsed")
        precision = "fraction" if "." in text else "second" if re.search(r":\d{2}:\d{2}", text) else "minute"
        provenance = "explicit-source-offset" if parsed.tzinfo is not None else None
        return RegistrationValue(value, "time" if clock else "datetime", parsed, precision, provenance)
    except ValueError:
        return RegistrationValue(value, "unparsed")


REGISTRATION_FIELDS = ("regStartdate", "regStarttime", "regEnddate", "regEndtime",
                       "regStartdatenew", "regEnddatenew")


def registration_values(source: Mapping[str, Any]) -> dict[str, RegistrationValue]:
    """Missing keys are unavailable history, distinct from explicit null values."""
    return {key: normalize_registration(source[key], clock=key.endswith("time"))
            for key in REGISTRATION_FIELDS if key in source}


def registration_conflicts(source: Mapping[str, Any]) -> tuple[str, ...]:
    """Compare all supported candidates without selecting field authority.

    Complete aware candidates compare as instants; complete naive candidates
    compare as source-local tuples. Mixed naive/aware pairs are unresolved,
    not conflicts or verified agreement. A partial date compares only calendar
    components and never acquires midnight. Raw precision/provenance remain in
    registration_values even when no conflict can be established.
    """
    values = registration_values(source)
    conflicts = []
    for prefix in ("regStart", "regEnd"):
        primary = values.get(prefix + "date")
        clock = values.get(prefix + "time")
        alternate = values.get(prefix + "datenew")
        candidates = []
        if primary and primary.status in {"date", "datetime"}:
            primary_date = primary.value.date() if primary.status == "datetime" else primary.value
            if primary.status == "datetime":
                candidates.append(primary.value)
            if clock and clock.status == "time":
                # combine retains the clock's explicit offset. Never inherit
                # the primary datetime's zone for a separately naive clock.
                candidates.append(datetime.combine(primary_date, clock.value))
            elif primary.status == "date":
                candidates.append(primary.value)
        if alternate and alternate.status in {"date", "datetime"}:
            candidates.append(alternate.value)
        conflict = False
        for index, left in enumerate(candidates):
            for right in candidates[index + 1:]:
                if isinstance(left, datetime) and isinstance(right, datetime):
                    if (left.tzinfo is None) != (right.tzinfo is None):
                        continue
                    conflict |= left != right
                else:
                    left_date = left.date() if isinstance(left, datetime) else left
                    right_date = right.date() if isinstance(right, datetime) else right
                    conflict |= left_date != right_date
        if conflict:
            conflicts.append(prefix)
    return tuple(conflicts)
