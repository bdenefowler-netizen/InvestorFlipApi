from pathlib import Path

path = Path("frontend/app/(tabs)/county.tsx")
text = path.read_text()

if '"pre_foreclosure"' not in text or 'label: "Pre-Foreclosure"' not in text:
    raise SystemExit("Pre-Foreclosure tab/source is missing; refusing to patch blindly.")

if "const PRE_FORECLOSURE_COLUMNS: Column[] = [" not in text:
    marker = "const DEFAULT_COLUMNS: Column[] = ["
    if text.count(marker) != 1:
        raise SystemExit(f"Expected one DEFAULT_COLUMNS marker, found {text.count(marker)}")

    block = r'''function preField(item: CountyCodeRecord, ...keys: string[]): unknown {
  const record = item as any;
  for (const key of keys) {
    const value =
      record[key] ??
      record.raw_import_row?.[key] ??
      record.feed_extra?.[key] ??
      record.raw_source_excerpt?.[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return null;
}

function preMoney(value: unknown): string {
  if (value === undefined || value === null || value === "") return "—";
  const numeric = Number(String(value).replace(/[^0-9.-]/g, ""));
  return Number.isFinite(numeric) ? `$${Math.round(numeric).toLocaleString()}` : plain(value);
}

const PRE_FORECLOSURE_COLUMNS: Column[] = [
  { key: "address", label: "PROPERTY ADDRESS", width: 230, strong: true, value: (i) => plain(preField(i, "situs_address", "address", "Property Address")) },
  { key: "tad", label: "TAD ACCOUNT #", width: 125, value: (i) => plain(preField(i, "account_id", "TAD Account #", "TAD Account Number")) },
  { key: "apn", label: "TAX ACCOUNT / APN", width: 135, value: (i) => plain(preField(i, "parcel_id", "apn", "Tax Account/APN")) },
  { key: "cause", label: "COUNTY CAUSE NUMBER", width: 155, value: (i) => plain(preField(i, "cause_number", "county_cause_number", "County Cause Number")) },
  { key: "legal", label: "LEGAL DESCRIPTION", width: 245, value: (i) => plain(preField(i, "legal_description", "Legal Description")) },
  { key: "owner", label: "CURRENT OWNER", width: 190, value: (i) => plain(preField(i, "owner_name", "owner", "Current Owner")) },
  { key: "mailing", label: "TAD OWNER MAILING ADDRESS", width: 230, value: (i) => plain(preField(i, "owner_mailing_address", "TAD Owner Mailing Address")) },
  { key: "auction", label: "SCHEDULED AUCTION", width: 140, value: (i) => plain(preField(i, "auction_date", "scheduled_auction", "Scheduled Auction")) },
  { key: "saleStatus", label: "SALE STATUS", width: 120, value: (i) => plain(preField(i, "sale_status", "Sale Status")) },
  { key: "saleDate", label: "SALE DATE", width: 110, value: (i) => plain(preField(i, "sale_date", "Sale Date")) },
  { key: "filed", label: "FILED DATE", width: 110, value: (i) => plain(preField(i, "filed_date", "Filed Date")) },
  { key: "foreclosure", label: "FORECLOSURE STATUS", width: 155, value: (i) => plain(preField(i, "foreclosure_status", "listing_status", "Foreclosure Status")) },
  { key: "lender", label: "LENDER", width: 185, value: (i) => plain(preField(i, "lender", "Lender")) },
  { key: "loan", label: "LOAN AMOUNT", width: 125, value: (i) => preMoney(preField(i, "loan_amount", "Loan Amount")) },
  { key: "loanType", label: "LOAN TYPE", width: 115, value: (i) => plain(preField(i, "loan_type", "Loan Type")) },
  { key: "loanDue", label: "LOAN DUE DATE", width: 125, value: (i) => plain(preField(i, "loan_due_date", "Loan Due Date")) },
  { key: "latestSale", label: "LATEST SALE PRICE", width: 135, value: (i) => preMoney(preField(i, "latest_sale_price", "Latest Sale Price")) },
  { key: "recording", label: "LATEST RECORDING DATE", width: 145, value: (i) => plain(preField(i, "latest_recording_date", "Latest Recording Date")) },
  { key: "propertyTax", label: "PROPERTY TAX AMOUNT", width: 145, value: (i) => preMoney(preField(i, "property_tax_amount", "annual_taxes", "Property Tax Amount")) },
  { key: "land", label: "LAND VALUE", width: 120, value: (i) => preMoney(preField(i, "land_value", "Land Value")) },
  { key: "improvement", label: "IMPROVEMENT VALUE", width: 140, value: (i) => preMoney(preField(i, "improvement_value", "Improvement Value")) },
  { key: "assessed", label: "TOTAL ASSESSED VALUE", width: 150, value: (i) => preMoney(preField(i, "assessed_value", "appraised_value", "Total Assessed Value")) },
  { key: "market", label: "MARKET VALUE", width: 125, value: (i) => preMoney(preField(i, "market_value", "tax_roll_market_value", "Market Value")) },
  { key: "distress", label: "DISTRESS SCORE", width: 115, value: (i) => plain(preField(i, "distress_score", "Distress Score")) },
];

'''
    text = text.replace(marker, block + marker, 1)

old_selector = '''  const columns = useMemo(
    () => (source === "tax_roll" ? TAX_ROLL_COLUMNS : source === "tax_delinquent" ? TAX_DUE_COLUMNS : DEFAULT_COLUMNS),
    [source],
  );'''
new_selector = '''  const columns = useMemo(
    () =>
      source === "tax_roll"
        ? TAX_ROLL_COLUMNS
        : source === "tax_delinquent"
          ? TAX_DUE_COLUMNS
          : source === "pre_foreclosure"
            ? PRE_FORECLOSURE_COLUMNS
            : DEFAULT_COLUMNS,
    [source],
  );'''
if 'source === "pre_foreclosure"\n            ? PRE_FORECLOSURE_COLUMNS' not in text:
    if text.count(old_selector) != 1:
        raise SystemExit(f"Expected one columns selector, found {text.count(old_selector)}")
    text = text.replace(old_selector, new_selector, 1)

path.write_text(text)
print("County Pre-Foreclosure columns patched cleanly.")
