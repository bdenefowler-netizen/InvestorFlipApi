"""Fail-closed lead selection rules. Retain raw evidence; narrow active leads."""
import calendar
import re
from datetime import date, datetime

OPEN = {"open", "active", "in progress", "pending", "under investigation", "notice issued", "reopened"}
INACTIVE_SALES = {"cancelled", "canceled", "postponed", "completed", "sold", "rescinded", "withdrawn"}
CLOSED_TERMS = (
    "closed", "resolved", "completed", "complied", "dismissed", "cancel",
    "inactive", "withdrawn", "void", "abated",
)
OPEN_TERMS = re.compile(
    r"\b(open|opened|reopen|reopened|active|in progress|pending|under investigation|notice issued)\b"
)

def normalized_status(value):
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())

def is_open_code_case(record):
    # Prefer individual violation state over overall case state.
    for key in (
        "Violation_Current_Status",
        "violation_current_status",
        "Case_Current_Status",
        "case_current_status",
        "code_latest_status",
        "status",
    ):
        status = normalized_status(record.get(key))
        if not status:
            continue
        if any(term in status for term in CLOSED_TERMS):
            return False
        if status.startswith(("not ", "no ")):
            return False
        if status in OPEN or OPEN_TERMS.search(status):
            return True
        return False
    return False

def _first_value(record, names):
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return value
    return None

def parse_sale_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    for fmt in (
        "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%b %d %Y",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M", "%m/%d/%y %I:%M %p",
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None

def _shift_month(today, direction):
    month_index = today.month - 1 + direction
    year = today.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))

def one_month_out(today):
    return _shift_month(today, 1)

def one_month_ago(today):
    return _shift_month(today, -1)

def upcoming_foreclosure(record, today=None):
    """Keep foreclosure sales at least one calendar month from today."""
    today = today or date.today()
    status = normalized_status(_first_value(record, (
        "sale_status", "auction_status", "foreclosure_status",
        "Foreclosure Status", "status",
    )))
    if status in INACTIVE_SALES:
        return False
    sale = parse_sale_date(_first_value(record, (
        "sale_date", "auction_date", "sell_date", "Sale_Date", "Auction_Date", "Sell_Date",
        "sale date", "sell date", "auction date",
        "Sale Date", "Sell Date", "Auction Date",
        "scheduled_auction_date", "scheduled auction date", "Scheduled Auction Date",
        "foreclosure_date", "foreclosure date", "Foreclosure Date",
    )))
    return sale is not None and sale >= one_month_out(today)

def recent_probate(record, today=None):
    """Keep probate records filed from one calendar month ago through today."""
    today = today or date.today()
    filed = parse_sale_date(_first_value(record, (
        "filed_date", "filled_date", "date_filed", "filing_date", "case_filed_date",
        "Filed_Date", "Filled_Date", "Date_Filed", "Filing_Date", "Case_Filed_Date",
        "filed date", "filled date", "date filed", "filing date", "case filed date",
        "Filed Date", "Date Filed", "Filing Date",
    )))
    return filed is not None and one_month_ago(today) <= filed <= today
