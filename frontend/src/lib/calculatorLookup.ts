import { API_BASE, getCountyRecords, type CountyRecord } from "@/src/lib/api";

type RawComparable = {
  price?: number | string | null;
  bedrooms?: number | null;
  bathrooms?: number | null;
  livingArea?: number | null;
  address?: string | null;
};

type RawCalculatorLookup = {
  address?: string;
  source?: string;
  price?: number | null;
  arv_estimate?: number | null;
  bedrooms?: number | null;
  bathrooms?: number | null;
  living_area?: number | null;
  tax_assessed?: number | null;
  details?: {
    comparables?: RawComparable[];
    price?: number | null;
  };
};

export type CalculatorDealLookup = {
  address: string;
  purchase_price: number | null;
  bedrooms: number | null;
  bathrooms: number | null;
  living_area: number | null;
  screening_arv: number | null;
  arv_source: string;
  arv_confidence: "medium" | "low" | "insufficient";
  comparable_count: number;
  county_record: CountyRecord | null;
  sources: string[];
};

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value.replace(/[^0-9.-]/g, ""));
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return null;
}

function median(values: number[]): number | null {
  const sorted = [...values].filter((v) => Number.isFinite(v) && v > 0).sort((a, b) => a - b);
  if (!sorted.length) return null;
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}

function addressKey(value: string): string {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, "");
}

function bestCountyMatch(address: string, items: CountyRecord[]): CountyRecord | null {
  const needle = addressKey(address);
  return (
    items.find((item) => {
      const candidate = addressKey(String(item.situs_address || ""));
      return candidate && (needle.includes(candidate) || candidate.includes(needle));
    }) ||
    items[0] ||
    null
  );
}

/**
 * Calculator lookup deliberately keeps "asking/listing price" separate from ARV.
 * ARV auto-fill is a screening benchmark:
 *  1) median of provider "similar homes" prices when at least 2 are available;
 *  2) TAD/county market/appraised value as a lower-confidence fallback.
 * Neither is represented as verified sold-comp ARV.
 */
export async function lookupCalculatorDeal(address: string): Promise<CalculatorDealLookup> {
  const [providerResult, countyResult] = await Promise.allSettled([
    fetch(`${API_BASE}/api/calculator/lookup?address=${encodeURIComponent(address)}`).then(async (res) => {
      if (!res.ok) throw new Error(`Property API lookup failed (${res.status})`);
      return (await res.json()) as RawCalculatorLookup;
    }),
    getCountyRecords("all", address, 1, 8),
  ]);

  const provider: RawCalculatorLookup =
    providerResult.status === "fulfilled" ? providerResult.value : { address, source: "unavailable" };
  const countyItems = countyResult.status === "fulfilled" ? countyResult.value.items : [];
  const county = bestCountyMatch(address, countyItems);

  const comparablePrices = (provider.details?.comparables || [])
    .map((comp) => asNumber(comp.price))
    .filter((value): value is number => value !== null);
  const compBenchmark = comparablePrices.length >= 2 ? median(comparablePrices) : null;

  const tadBenchmark =
    asNumber(county?.market_value) ??
    asNumber(county?.appraised_value) ??
    asNumber(county?.tax_roll_market_value) ??
    asNumber(provider.tax_assessed);

  let screeningArv: number | null = null;
  let arvSource = "No ARV benchmark found";
  let arvConfidence: CalculatorDealLookup["arv_confidence"] = "insufficient";

  if (compBenchmark) {
    screeningArv = compBenchmark;
    arvSource = `Real Estate API similar-home median (${comparablePrices.length} comps)`;
    arvConfidence = comparablePrices.length >= 3 ? "medium" : "low";
  } else if (tadBenchmark) {
    screeningArv = tadBenchmark;
    arvSource = "TAD / County current market-appraised value";
    arvConfidence = "low";
  }

  const sources: string[] = [];
  if (provider.source && !["fallback", "unavailable"].includes(provider.source)) sources.push("Real Estate API");
  if (county) sources.push("TAD / County");

  return {
    address,
    purchase_price: asNumber(provider.price),
    bedrooms: asNumber(provider.bedrooms) ?? asNumber(county?.beds),
    bathrooms: asNumber(provider.bathrooms) ?? asNumber(county?.baths),
    living_area: asNumber(provider.living_area) ?? asNumber(county?.sqft),
    screening_arv: screeningArv,
    arv_source: arvSource,
    arv_confidence: arvConfidence,
    comparable_count: comparablePrices.length,
    county_record: county,
    sources,
  };
}
