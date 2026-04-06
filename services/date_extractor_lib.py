"""
Financial Date Extractor — Rule-Based NLP Pipeline
====================================================
No LLM/SLM required. Uses:
  - dateparser      (pip install dateparser)
  - python-dateutil (pip install python-dateutil)
  - regex           (built-in re)

Returns structured DateResult objects with one or two date ranges,
and a comparison flag when the user is asking to compare two periods.
"""

import re
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
import dateparser as _dateparser

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

# Financial year start month (4 = April for India/UK, 1 = January for US)
FY_START_MONTH = 4


# ─────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────

class DateRange:
    def __init__(self, start: date, end: date, label: str = ""):
        self.start = start
        self.end   = end
        self.label = label

    def __repr__(self):
        return f"{self.label or 'Range'}({self.start} → {self.end})"


class DateResult:
    def __init__(self, primary: DateRange, secondary: DateRange = None,
                 is_comparison: bool = False, raw_text: str = "", label: str = ""):
        self.primary       = primary
        self.secondary     = secondary
        self.is_comparison = is_comparison
        self.raw_text      = raw_text
        if label:
            self.primary.label = label

    def __repr__(self):
        if self.is_comparison:
            return f"COMPARE: {self.primary}  vs  {self.secondary}"
        return f"SINGLE:  {self.primary}"


# ─────────────────────────────────────────────
# HELPERS — Financial Calendar
# ─────────────────────────────────────────────

def fy_range(year: int) -> DateRange:
    """Financial year starting in April of `year`."""
    start = date(year, FY_START_MONTH, 1)
    end   = date(year + 1, FY_START_MONTH, 1) - timedelta(days=1)
    return DateRange(start, end, f"FY{year}/{str(year + 1)[-2:]}")


def fy_for_date(d: date) -> DateRange:
    """Which financial year does `d` fall in?"""
    return fy_range(d.year if d.month >= FY_START_MONTH else d.year - 1)


def quarter_range(q: int, year: int) -> DateRange:
    """Calendar quarter: Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec."""
    m = {1: 1, 2: 4, 3: 7, 4: 10}[q]
    start = date(year, m, 1)
    end   = (start + relativedelta(months=3)) - timedelta(days=1)
    return DateRange(start, end, f"Q{q} {year}")


def fq_range(q: int, fy_start_year: int) -> DateRange:
    """Financial quarter within a financial year."""
    start = date(fy_start_year, FY_START_MONTH, 1) + relativedelta(months=(q - 1) * 3)
    end   = (start + relativedelta(months=3)) - timedelta(days=1)
    return DateRange(start, end, f"FQ{q} FY{fy_start_year}")


def half_range(h: int, year: int) -> DateRange:
    """H1 = Jan–Jun, H2 = Jul–Dec."""
    if h == 1:
        return DateRange(date(year, 1, 1), date(year, 6, 30), f"H1 {year}")
    return DateRange(date(year, 7, 1), date(year, 12, 31), f"H2 {year}")


def month_range(month: int, year: int) -> DateRange:
    start = date(year, month, 1)
    end   = (start + relativedelta(months=1)) - timedelta(days=1)
    return DateRange(start, end, start.strftime("%B %Y"))


def week_range(d: date) -> DateRange:
    start = d - timedelta(days=d.weekday())
    return DateRange(start, start + timedelta(days=6), "Week")


def ytd_range(today: date) -> DateRange:
    return DateRange(date(today.year, 1, 1), today, "YTD")


def fytd_range(today: date) -> DateRange:
    return DateRange(fy_for_date(today).start, today, "FYTD")


# ─────────────────────────────────────────────
# HELPERS — Text Normalization
# ─────────────────────────────────────────────

MONTH_MAP = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

MONTH_KEYS_SORTED = sorted(MONTH_MAP.keys(), key=len, reverse=True)

QWORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4,
}


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r'\bfy\s+(\d{4})\b', r'fy\1', text)
    text = re.sub(r'\bq\.?\s*([1-4])\b', r'q\1', text)
    text = re.sub(r'\b(\d+)(st|nd|rd|th)\b', r'\1', text)
    text = re.sub(r'[–—]', '-', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def current_quarter(today: date) -> int:
    return (today.month - 1) // 3 + 1


def _dp_parse(text: str, today: date = None) -> date | None:
    settings = {
        "PREFER_DAY_OF_MONTH": "first",
        "RETURN_AS_TIMEZONE_AWARE": False,
    }
    if today:
        settings["RELATIVE_BASE"] = datetime(today.year, today.month, today.day)
    parsed = _dateparser.parse(text, settings=settings)
    return parsed.date() if parsed else None


# ─────────────────────────────────────────────
# CORE EXTRACTOR
# ─────────────────────────────────────────────

class FinancialDateExtractor:
    """
    Extraction pipeline (in order):
      1. Normalize text
      2. Try comparison patterns  (vs / compare X and Y / H1 and H2 …)
      3. Try single-period rules  (FY / quarter / half / month / relative / range)
      4. Fallback to dateparser   (free-form natural language dates)
    """

    def __init__(self, today: date = None, fy_start_month: int = FY_START_MONTH):
        self.today          = today or date.today()
        self.fy_start_month = fy_start_month

    def extract(self, text: str) -> DateResult | None:
        norm   = normalize(text)
        result = self._try_comparison(norm) or self._try_single(norm)
        if result:
            result.raw_text = text
        return result

    def _try_comparison(self, text: str) -> DateResult | None:
        today = self.today

        m = re.search(r'\bh([12])\s+(?:and|vs\.?|versus)\s+h([12])\s+(?:of\s+)?(\d{4})\b', text)
        if m:
            h1, h2, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return DateResult(half_range(h1, yr), half_range(h2, yr), is_comparison=True)

        for pat, fn_l, fn_r in [
            (r'last\s+(quarter|month|week|year)\s+(?:and|vs\.?|versus)\s+this\s+\1',
             self._last_unit, self._this_unit),
            (r'this\s+(quarter|month|week|year)\s+(?:and|vs\.?|versus)\s+last\s+\1',
             self._this_unit, self._last_unit),
        ]:
            m = re.search(pat, text)
            if m:
                u = m.group(1)
                l, r = fn_l(u), fn_r(u)
                if l and r:
                    return DateResult(l, r, is_comparison=True)

        m = re.search(
            r'(this|current)\s+year\s+(?:vs\.?|versus|and|compared\s+to)\s+(last|previous)\s+year',
            text)
        if m:
            return DateResult(self._this_unit("year"), self._last_unit("year"), is_comparison=True)
        m = re.search(
            r'(last|previous)\s+year\s+(?:vs\.?|versus|and|compared\s+to)\s+(this|current)\s+year',
            text)
        if m:
            return DateResult(self._last_unit("year"), self._this_unit("year"), is_comparison=True)

        m = re.search(
            r'(.+?)\s+(?:vs\.?|versus|compared\s+to|and)\s+same\s+(month|quarter|week)\s+last\s+year',
            text)
        if m:
            r1 = self._try_single(m.group(1).strip())
            unit = m.group(2)
            if r1:
                shifted = r1.primary.start - relativedelta(years=1)
                if unit == "month":
                    r2 = month_range(shifted.month, shifted.year)
                elif unit == "quarter":
                    r2 = quarter_range(current_quarter(shifted), shifted.year)
                else:
                    r2 = week_range(shifted)
                return DateResult(r1.primary, r2, is_comparison=True)

        m = re.search(r'(?:in\s+|for\s+)?(\d{4})\s+and\s+(\d{4})', text)
        if m:
            y1, y2 = int(m.group(1)), int(m.group(2))
            if 1980 <= y1 <= 2100 and 1980 <= y2 <= 2100:
                return DateResult(
                    DateRange(date(y1, 1, 1), date(y1, 12, 31), str(y1)),
                    DateRange(date(y2, 1, 1), date(y2, 12, 31), str(y2)),
                    is_comparison=True,
                )

        compare_triggers = [
            r'compare\s+(.+?)\s+(?:and|vs\.?|versus|with|against)\s+(.+)',
            r'(.+?)\s+vs\.?\s+(.+)',
            r'(.+?)\s+versus\s+(.+)',
            r'difference\s+between\s+(.+?)\s+and\s+(.+)',
            r'(.+?)\s+compared\s+(?:to|with)\s+(.+)',
        ]
        for pat in compare_triggers:
            m = re.search(pat, text)
            if not m:
                continue
            left, right = m.group(1).strip(), m.group(2).strip()
            r1 = self._try_single(left)
            r2 = self._try_single(right)

            if r1 and not r2:
                yr_m = re.search(r'\b(19|20)\d{2}\b', right)
                if yr_m and not re.search(r'\b(19|20)\d{2}\b', left):
                    r1 = self._try_single(left + " " + yr_m.group(0))
                    r2 = self._try_single(right)
            elif r2 and not r1:
                yr_m = re.search(r'\b(19|20)\d{2}\b', left)
                if yr_m and not re.search(r'\b(19|20)\d{2}\b', right):
                    r1 = self._try_single(left)
                    r2 = self._try_single(right + " " + yr_m.group(0))

            if r1 and r2:
                return DateResult(r1.primary, r2.primary, is_comparison=True)

        return None

    def _try_single(self, text: str) -> DateResult | None:
        t     = text
        today = self.today

        m = re.search(r'\bfy(\d{4})(?:[/-]\d{2,4})?\b', t)
        if m:
            return DateResult(fy_range(int(m.group(1))))

        m = re.search(r'\bfinancial\s+year\s+(\d{4})\b', t)
        if m:
            return DateResult(fy_range(int(m.group(1))))

        if re.search(r'\b(current|this)\s+(financial\s+year|fy)\b', t):
            return DateResult(fy_for_date(today), label="Current FY")

        if re.search(r'\b(last|previous)\s+(financial\s+year|fy)\b', t):
            fy = fy_for_date(today)
            return DateResult(fy_range(fy.start.year - 1), label="Last FY")

        if re.search(r'\b(financial\s+year|fy)\s*(to[\s-]?date|ytd)\b', t) or \
           re.search(r'\bfytd\b', t):
            return DateResult(fytd_range(today))

        for word, q in QWORDS.items():
            m = re.search(rf'\b{word}\s+quarter(?:\s+of)?\s+(\d{{4}})\b', t)
            if m:
                return DateResult(quarter_range(q, int(m.group(1))))
            m = re.search(rf'\b{word}\s+quarter\b', t)
            if m:
                return DateResult(quarter_range(q, today.year))

        m = re.search(r'\bq([1-4])(?:\s+of)?\s+(\d{4})\b', t)
        if m:
            return DateResult(quarter_range(int(m.group(1)), int(m.group(2))))

        m = re.search(r'\bq([1-4])\b', t)
        if m and not re.search(r'\d{4}', t):
            return DateResult(quarter_range(int(m.group(1)), today.year))

        m = re.search(r'\b(last|previous|this|current|next)\s+quarter\b', t)
        if m:
            which = m.group(1)
            cq    = current_quarter(today)
            if which in ("last", "previous"):
                q, yr = (cq - 1, today.year) if cq > 1 else (4, today.year - 1)
            elif which in ("this", "current"):
                q, yr = cq, today.year
            else:
                q, yr = (cq + 1, today.year) if cq < 4 else (1, today.year + 1)
            return DateResult(quarter_range(q, yr))

        m = re.search(r'\bh([12])(?:\s+of)?\s+(\d{4})\b', t)
        if m:
            return DateResult(half_range(int(m.group(1)), int(m.group(2))))

        m = re.search(r'\b(first|second)\s+half(?:\s+of)?\s+(\d{4})\b', t)
        if m:
            return DateResult(half_range(1 if m.group(1) == "first" else 2, int(m.group(2))))

        m = re.search(r'\b(first|second)\s+half\b', t)
        if m:
            return DateResult(half_range(1 if m.group(1) == "first" else 2, today.year))

        for mn in MONTH_KEYS_SORTED:
            mv = MONTH_MAP[mn]
            m = re.search(rf'\b{mn}\b', t)
            if not m:
                continue
            yr_m = re.search(r'\b(\d{4})\b', t)
            yr   = int(yr_m.group(1)) if yr_m else today.year
            return DateResult(month_range(mv, yr))

        m = re.search(r'\b((?:19|20)\d{2})\b', t)
        if m:
            yr  = int(m.group(1))
            end = min(date(yr, 12, 31), today) if yr == today.year else date(yr, 12, 31)
            return DateResult(DateRange(date(yr, 1, 1), end, str(yr)))

        m = re.search(r'\b(?:last|past|previous)\s+(\d+)\s+(day|week|month|year)s?\b', t)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            delta = {
                "day":   relativedelta(days=n),
                "week":  relativedelta(weeks=n),
                "month": relativedelta(months=n),
                "year":  relativedelta(years=n),
            }[unit]
            return DateResult(DateRange(today - delta, today, f"Last {n} {unit}(s)"))

        m = re.search(r'\b(last|previous|this|current)\s+(week|month|year)\b', t)
        if m:
            which, unit = m.group(1), m.group(2)
            r = self._last_unit(unit) if which in ("last", "previous") else self._this_unit(unit)
            if r:
                return DateResult(r)

        if re.search(r'\b(year[\s-]?to[\s-]?date|ytd)\b', t):
            return DateResult(ytd_range(today))
        if re.search(r'\b(month[\s-]?to[\s-]?date|mtd)\b', t):
            return DateResult(DateRange(date(today.year, today.month, 1), today, "MTD"))
        if re.search(r'\b(week[\s-]?to[\s-]?date|wtd)\b', t):
            start = today - timedelta(days=today.weekday())
            return DateResult(DateRange(start, today, "WTD"))

        if re.search(r'\btoday\b', t):
            return DateResult(DateRange(today, today, "Today"))
        if re.search(r'\byesterday\b', t):
            y = today - timedelta(days=1)
            return DateResult(DateRange(y, y, "Yesterday"))

        m = re.search(
            r'(?:from\s+|between\s+)?(.+?)\s+(?:to|till|until|through|thru)\s+(.+)', t)
        if m:
            d1 = self._parse_fragment(m.group(1).strip())
            d2 = self._parse_fragment(m.group(2).strip(), end_of_period=True)
            if d1 and d2 and d1 <= d2:
                return DateResult(DateRange(d1, d2, f"{m.group(1)} → {m.group(2)}"))

        m = re.search(r'\bsince\s+(.+)', t)
        if m:
            d = self._parse_fragment(m.group(1).strip())
            if d:
                return DateResult(DateRange(d, today, f"Since {m.group(1)}"))

        d = _dp_parse(t, today=today)
        if d:
            return DateResult(DateRange(d, d, "Parsed date"))

        return None

    def _this_unit(self, unit: str) -> DateRange | None:
        today = self.today
        if unit == "week":    return week_range(today)
        if unit == "month":   return month_range(today.month, today.year)
        if unit == "year":    return DateRange(date(today.year, 1, 1), date(today.year, 12, 31), str(today.year))
        if unit == "quarter": return quarter_range(current_quarter(today), today.year)
        return None

    def _last_unit(self, unit: str) -> DateRange | None:
        today = self.today
        if unit == "week":
            start = today - timedelta(days=today.weekday() + 7)
            return DateRange(start, start + timedelta(days=6), "Last week")
        if unit == "month":
            last_day = date(today.year, today.month, 1) - timedelta(days=1)
            return month_range(last_day.month, last_day.year)
        if unit == "year":
            return DateRange(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), str(today.year - 1))
        if unit == "quarter":
            cq = current_quarter(today)
            q, yr = (cq - 1, today.year) if cq > 1 else (4, today.year - 1)
            return quarter_range(q, yr)
        return None

    def _parse_fragment(self, text: str, end_of_period: bool = False) -> date | None:
        text = text.strip()

        m = re.match(r'^(\d{1,2})[/\-](\d{4})$', text)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            month, year = (a, b) if a <= 12 else (b, a)
            if end_of_period:
                return (date(year, month, 1) + relativedelta(months=1)) - timedelta(days=1)
            return date(year, month, 1)

        m = re.match(r'^(\d{4})$', text)
        if m:
            yr = int(m.group(1))
            return date(yr, 12, 31) if end_of_period else date(yr, 1, 1)

        for mn in MONTH_KEYS_SORTED:
            if re.search(rf'\b{mn}\b', text.lower()):
                yr_m = re.search(r'\d{4}', text)
                yr   = int(yr_m.group(0)) if yr_m else self.today.year
                mv   = MONTH_MAP[mn]
                if end_of_period:
                    return (date(yr, mv, 1) + relativedelta(months=1)) - timedelta(days=1)
                return date(yr, mv, 1)

        return _dp_parse(text, today=self.today)


# ─────────────────────────────────────────────
# CONVENIENCE WRAPPER
# ─────────────────────────────────────────────

def extract_dates(text: str, today: date = None) -> DateResult | None:
    return FinancialDateExtractor(today=today).extract(text)


def serialize_date_result(result: DateResult | None) -> dict | None:
    """Convert a DateResult object to a JSON-serialisable dict."""
    if result is None:
        return None

    def range_dict(r):
        return {"start": str(r.start), "end": str(r.end), "label": r.label or ""}

    return {
        "primary": range_dict(result.primary),
        "secondary": range_dict(result.secondary) if result.secondary else None,
        "is_comparison": result.is_comparison,
    }
