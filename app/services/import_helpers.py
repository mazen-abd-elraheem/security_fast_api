"""
SecureTrack Platform — Shared Excel/CSV import helpers.

Every bulk-import endpoint (shifts, roster, routes) MUST resolve rows against
the real tables through these helpers so that an imported row behaves exactly
like a manual, one-by-one action in the UI.

Handles the formats Excel / the Flutter `excel` package actually produce:
  * badges exported as numbers           → "12.0"           → "12"
  * dates as DateCellValue               → "2026-09-24 00:00:00.000"
  * dates as Excel serial numbers        → "46289"
  * dates as d/m/Y or Y/m/d              → "24/09/2026"
  * times as Excel day fractions         → "0.25"           → 06:00:00
  * times without seconds / with AM-PM   → "6:00", "6:00 PM"
  * our own CSV export wrapper           → ="2026-09-24"
  * Arabic spelling variants in names    → أ/إ/آ/ا, ة/ه, ى/ي, tatweel, diacritics
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

# ── Generic cleaning ────────────────────────────────────────────────────────

_INVISIBLE = re.compile(r"[\uFEFF\u200B-\u200F\u202A-\u202E\u00A0]")
_AR_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")


def clean(v) -> str:
    """Return a trimmed string, '' for None/null-ish values, strips ="..." wrappers."""
    if v is None:
        return ""
    s = _INVISIBLE.sub("", str(v)).strip()
    if s.startswith("="):
        s = s[1:].replace('"', "").strip()
    if s.lower() in ("", "null", "none", "nan", "-", "—"):
        return ""
    return s


def first(row: dict, *keys: str) -> str:
    """First non-empty cleaned value among several possible column keys."""
    for k in keys:
        v = clean(row.get(k))
        if v:
            return v
    return ""


def norm_badge(v) -> str:
    """'12.0' → '12', ' ST-G-002 ' → 'ST-G-002'."""
    s = clean(v)
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return s


def norm_name(v) -> str:
    """Normalise a site / shift name for tolerant matching (Arabic + English)."""
    s = clean(v).lower()
    s = _AR_DIACRITICS.sub("", s).replace("\u0640", "")  # diacritics + tatweel
    s = re.sub("[أإآٱ]", "ا", s)
    s = s.replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ── Dates & times ───────────────────────────────────────────────────────────

_EXCEL_EPOCH = date(1899, 12, 30)
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y")


def parse_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = clean(v)
    if not s:
        return None
    # Excel serial number
    if re.fullmatch(r"\d{4,6}(\.\d+)?", s):
        try:
            n = float(s)
            if 20000 <= n <= 80000:
                return _EXCEL_EPOCH + timedelta(days=int(n))
        except ValueError:
            pass
    # ISO datetime ("2026-09-24 00:00:00.000" / "2026-09-24T00:00:00")
    m = re.match(r"^(\d{4}-\d{1,2}-\d{1,2})[ T]", s)
    if m:
        s = m.group(1)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(v) -> Optional[time]:
    if isinstance(v, time):
        return v
    if isinstance(v, datetime):
        return v.time()
    s = clean(v)
    if not s:
        return None
    # Excel day fraction (0.25 → 06:00)
    if re.fullmatch(r"0?\.\d+|0|1(\.0+)?", s):
        try:
            total = round(float(s) * 24 * 3600) % (24 * 3600)
            return time(total // 3600, (total % 3600) // 60, total % 60)
        except ValueError:
            pass
    # Strip a leading date part ("1970-01-01 06:00:00")
    m = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?\s*(?:[aApP][mM])?)$", s)
    if m:
        s = m.group(1)
    s = s.split(".")[0].strip().upper()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p", "%I:%M%p"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


_DAY_MAP = {
    "mon": "mon", "monday": "mon", "الاثنين": "mon", "الإثنين": "mon", "اثنين": "mon",
    "tue": "tue", "tuesday": "tue", "الثلاثاء": "tue", "ثلاثاء": "tue",
    "wed": "wed", "wednesday": "wed", "الاربعاء": "wed", "الأربعاء": "wed", "اربعاء": "wed",
    "thu": "thu", "thursday": "thu", "الخميس": "thu", "خميس": "thu",
    "fri": "fri", "friday": "fri", "الجمعه": "fri", "الجمعة": "fri", "جمعه": "fri",
    "sat": "sat", "saturday": "sat", "السبت": "sat", "سبت": "sat",
    "sun": "sun", "sunday": "sun", "الاحد": "sun", "الأحد": "sun", "احد": "sun",
}
_DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_days(v) -> str:
    """'Mon, Tue' / 'السبت،الأحد' / 'all' → 'mon,tue'. Returns '' when blank/invalid."""
    s = clean(v).lower()
    if not s:
        return ""
    if s in ("all", "daily", "كل الايام", "كل الأيام", "يومي"):
        return ",".join(_DAY_ORDER)
    parts = [p.strip() for p in re.split(r"[,،;/\s]+", s) if p.strip()]
    days = {_DAY_MAP.get(p) for p in parts} - {None}
    return ",".join(d for d in _DAY_ORDER if d in days)


# ── Lookups against real tables ─────────────────────────────────────────────

class Lookup:
    """
    Pre-loads sites, shifts and users ONCE per import so each row is resolved
    against the live database with tolerant matching (no N+1 queries).
    """

    def __init__(self, db: Session, badges: Iterable[str] = ()):
        from app.models.site import Site
        from app.models.shift import Shift
        from app.models.user import User

        self.db = db
        self._sites = {}
        for s in db.query(Site).all():
            self._sites.setdefault(norm_name(s.name), s)

        # Prefer active shifts but keep inactive ones so they can be reactivated
        self._shifts: dict[tuple, object] = {}
        for sh in db.query(Shift).order_by(Shift.is_active.asc()).all():
            self._shifts[(sh.site_id, norm_name(sh.label))] = sh  # active overrides inactive

        wanted = {norm_badge(b) for b in badges if norm_badge(b)}
        self._users = {}
        if wanted:
            for u in db.query(User).filter(User.badge_number.in_(wanted)).all():
                self._users[norm_badge(u.badge_number)] = u

    def site(self, name: str):
        return self._sites.get(norm_name(name))

    def shift(self, site_id: str, label: str):
        return self._shifts.get((site_id, norm_name(label)))

    def add_shift(self, shift):
        self._shifts[(shift.site_id, norm_name(shift.label))] = shift

    def user(self, badge: str):
        return self._users.get(norm_badge(badge))


def role_of(user) -> str:
    r = getattr(user, "role", "")
    return (r.value if hasattr(r, "value") else str(r or "")).lower()


def result(created: int, updated: int, skipped: int, total: int, errors: list, label: str) -> dict:
    """Uniform response shape for every import endpoint."""
    return {
        "detail": f"{label}: {created} created, {updated} updated, {skipped} skipped",
        "created_count": created,
        "updated_count": updated,
        "skipped_count": skipped,
        "total_count": total,
        "errors": errors[:200],
    }
