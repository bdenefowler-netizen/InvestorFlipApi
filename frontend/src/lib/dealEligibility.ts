import type { Property } from "./api";

const n = (v: unknown) => {
  const x = Number(v);
  return Number.isFinite(x) && x > 0 ? x : null;
};

export function getDealEligibility(p: Property) {
  const x = p as any;
  const signals: string[] = Array.isArray(x.opportunity_signal_keys)
    ? x.opportunity_signal_keys.map((key: unknown) => String(key))
    : [];
  const signal = (key: string) => signals.includes(key);
  const txt = [
    x.listing_type,
    x.listing_status,
    x.listing_description,
    x.description,
    x.public_remarks,
    x.remarks,
    x.marketing_remarks,
    x.distress_status,
    x.upload_category,
    Array.isArray(x.upload_categories) ? x.upload_categories.join(" ") : "",
    Array.isArray(x.opportunity_signals) ? x.opportunity_signals.join(" ") : "",
  ].filter(Boolean).join(" ").toLowerCase().replace(/_/g, " ");

  const code = x.has_code_violations === true
    || Number(x.code_violation_count || 0) > 0
    || Number(x.open_code_violation_count || 0) > 0
    || signal("code_violation");
  const tax = x.tax_delinquent === true
    || x.has_uploaded_tax_auction === true
    || Number(x.current_tax_amount_due || 0) > 0
    || Number(x.prior_tax_amount_due || 0) > 0
    || /\btax lien\b|\btax delinquent\b|\btax auction\b/.test(txt)
    || signal("tax_lien");
  const probate = x.has_probate === true || /\bprobate\b|\bdecedent\b/.test(txt);
  const preForeclosure = x.pre_foreclosure === true
    || /\bpre[ -]?foreclosure\b/.test(txt)
    || signal("pre_foreclosure");
  const foreclosure = preForeclosure
    || /\bforeclosure\b/.test(txt)
    || signal("foreclosure");
  const fsbo = x.fsbo_confirmed === true || /\bfsbo\b|\bfor sale by owner\b/.test(txt) || signal("fsbo");
  const motivated = /\bmotivated seller\b|\bmust sell\b|\bpriced to sell\b/.test(txt) || signal("motivated_seller");
  const distressed = signal("distressed") || /\bdistressed propert|\bfixer|\bneeds (?:major )?repairs?\b/.test(txt);

  let category = "Opportunity";
  if (code) category = "Code Violation";
  else if (tax) category = "Tax Lien / Auction";
  else if (probate) category = "Probate";
  else if (preForeclosure) category = "Pre-Foreclosure";
  else if (foreclosure) category = "Foreclosure";
  else if (distressed) category = "Distressed";
  else if (motivated) category = "Motivated Seller";
  else if (fsbo) category = "FSBO";

  const listed = n(x.listed_value) ?? n(x.price) ?? n(x.listing_price);
  const market = n(x.market_value)
    ?? n(x.tax_roll_market_value)
    ?? n(x.appraised_value)
    ?? n(x.assessed_value)
    ?? n(x.value_benchmark)
    ?? n(x.arv_estimate);
  const sqft = n(x.sqft);
  const repairAllowance = n(x.repair_allowance) ?? (sqft ? sqft * 35 : null);
  const maxOffer = n(x.max_offer) ?? (market && repairAllowance ? market - repairAllowance : null);
  const spread = n(x.spread) ?? (market && listed ? market - listed : null);

  return {
    allowed: true,
    category,
    is_distressed: code || tax || probate || distressed,
    acquisition_basis: listed,
    basis_source: listed ? "listed_value" : null,
    market_value: market,
    listed_value: listed,
    repair_allowance: repairAllowance,
    max_offer: maxOffer,
    exit_value: market,
    spread,
  };
}
