"""Fail-closed lead selection rules. Retain raw evidence; narrow active leads."""
import calendar
from datetime import date, datetime

OPEN = {"open", "active", "in progress", "pending", "under investigation", "notice issued", "reopened"}
INACTIVE_SALES = {"cancelled", "canceled", "postponed", "completed", "sold", "rescinded", "withdrawn"}

def normalized_status(value):
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())

def is_open_code_case(record):
    # Prefer individual violation state over overall case state.
    for key in ("Violation_Current_Status", "violation_current_status", "Case_Current_Status", "case_current_status", "status"):
        status = normalized_status(record.get(key))
        if status:
            return status in OPEN
    return False

def parse_sale_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or ").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%b %d %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None

def one_month_out(today):
    month = today.month % 12 + 1
    year = today.year + (today.month == 12)
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))

def upcoming_foreclosure(record, today=None):
    today = today or date.today()
    status = normalized_status(record.get("sale_status") or record.get("auction_status") or record.get("status"))
    if status in INACTIVE_SALES:
        return False
    sale = parse_sale_date(record.get("sale_date") or record.get("auction_date") or record.get("Sale Date"))
    return sale is not None and today <= sale <= one_month_out(today)
