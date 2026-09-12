import type { Property } from "./api";

export const MAX_DEAL_BASIS = 400_000;

const n=(v:unknown)=>{const x=Number(v);return Number.isFinite(x)&&x>0?x:null};

export function getDealEligibility(p: Property){
  const x=p as any;
  const txt=[
    x.listing_type,x.listing_status,x.listing_description,x.description,x.public_remarks,
    x.remarks,x.marketing_remarks,x.distress_status,x.upload_category,
    Array.isArray(x.upload_categories)?x.upload_categories.join(" "):"",
    Array.isArray(x.opportunity_signal_keys)?x.opportunity_signal_keys.join(" "):"",
    Array.isArray(x.opportunity_signals)?x.opportunity_signals.join(" "):"",
  ].filter(Boolean).join(" ").toLowerCase().replace(/_/g," ");

  const code=x.has_code_violations===true||Number(x.code_violation_count||0)>0||Number(x.open_code_violation_count||0)>0;
  const condition=/\bas[ -]?is\b|\bhandyman special\b|\bneeds tlc\b/.test(txt);
  const pre=x.pre_foreclosure===true||/\bpre[ -]?foreclosure\b/.test(txt);
  const fsbo=x.fsbo_confirmed===true||/\bfsbo\b|\bfor sale by owner\b/.test(txt);
  const probate=x.has_probate===true||/\bprobate\b/.test(txt);
  const tax=x.tax_delinquent===true||Number(x.current_tax_amount_due||0)>0||Number(x.prior_tax_amount_due||0)>0||/\btax lien\b|\btax delinquent\b/.test(txt);

  let category:string|null=null;
  if(code) category="Code Violation";
  else if(condition) category="Distressed";
  else if(pre) category="Pre-Foreclosure";
  else if(fsbo) category="FSBO";
  else if(probate) category="Probate";
  else if(tax) category="Tax Lien";

  const purchase=n(x.price)??n(x.listing_price);
  const assessed=n(x.assessed_value)??n(x.tax_appraised_value);
  const acquisition=purchase??assessed;
  const exit=n(x.value_benchmark)??n(x.arv_estimate)??n(x.market_value)??n(x.tax_roll_market_value)??n(x.provider_estimated_value)??n(x.zestimate);
  return {
    allowed:Boolean(category&&acquisition!==null&&acquisition<=MAX_DEAL_BASIS),
    category,
    acquisition_basis:acquisition,
    basis_source:purchase?"purchase_price":assessed?"assessed_value":null,
    exit_value:exit,
    spread:exit&&acquisition?exit-acquisition:null,
  };
}
