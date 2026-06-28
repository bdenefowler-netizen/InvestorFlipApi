// API client for TarrantREI backend
const BASE = (process.env.EXPO_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");
const API = `${BASE}/api`;

export type Property = {
  id: string;
  situs_address: string;
  city: string;
  state: string;
  zip: string;
  county: string;
  beds: number;
  baths: number;
  sqft: number;
  year_built: number;
  lot_size_sqft: number;
  image_url: string;
  price: number;
  market_value: number;
  assessed_value: number;
  annual_taxes: number;
  equity_estimate: number;
  est_roi_pct: number;
  legal_description: string;
  listing_type: string;
  owner_name: string;
  owner_type: string;
  owner_mailing_address: string;
  out_of_state_owner: boolean;
  tax_delinquent: boolean;
  vacant: boolean;
  high_equity: boolean;
  cash_buyer: boolean;
  investor_owned: boolean;
  data_source: string;
  investment_score: number;
  wholesale_score: number;
  flip_score: number;
  rental_score: number;
  risk_score: number;
};

export type FilterDef = { key: string; label: string; count: number };

async function jsonGet<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} → ${res.status}`);
  return res.json();
}

export async function getFilters(): Promise<{ filters: FilterDef[] }> {
  return jsonGet(`${API}/filters`);
}

export async function getProperties(
  filter: string,
  search: string,
): Promise<{ count: number; items: Property[] }> {
  const params = new URLSearchParams({ filter });
  if (search) params.set("search", search);
  return jsonGet(`${API}/properties?${params.toString()}`);
}

export async function getProperty(id: string): Promise<Property> {
  return jsonGet(`${API}/properties/${id}`);
}

export async function getNearby(id: string): Promise<{
  nearby_foreclosures: Pick<Property, "id" | "situs_address" | "price" | "listing_type" | "image_url">[];
  nearby_investor_purchases: Pick<Property, "id" | "situs_address" | "price" | "owner_type" | "image_url">[];
}> {
  return jsonGet(`${API}/properties/${id}/nearby`);
}

export async function getAIAnalysis(id: string): Promise<{ property_id: string; narrative: string }> {
  const res = await fetch(`${API}/properties/${id}/ai-analysis`, { method: "POST" });
  if (!res.ok) throw new Error(`AI analysis failed (${res.status})`);
  return res.json();
}

export type Enrichment = {
  property_id: string;
  address_queried: string;
  found: boolean;
  zpid?: number | string;
  beds?: number;
  baths?: number;
  sqft?: number;
  year_built?: number;
  lot_size?: string;
  home_type?: string;
  home_status?: string;
  list_price?: number;
  rapidapi_address?: string;
  rapidapi_city?: string;
  rapidapi_state?: string;
  rapidapi_zip?: string;
  appliances?: string[];
  cooling?: string[];
  heating?: string[];
  parcel_id?: string;
  photos?: string[];
  hi_res_image?: string;
  error?: string;
};

export async function enrichProperty(id: string): Promise<Enrichment> {
  const res = await fetch(`${API}/properties/${id}/enrich`, { method: "POST" });
  if (!res.ok) throw new Error(`enrich failed (${res.status})`);
  return res.json();
}

export type TaxHistoryEntry = {
  year: number;
  tax: number;
  assessment?: { building?: number; land?: number; total?: number };
  market?: { building?: number; land?: number; total?: number };
};

export async function getTaxHistory(id: string): Promise<{ tax_history: TaxHistoryEntry[]; available: boolean }> {
  const res = await fetch(`${API}/properties/${id}/tax-history`);
  if (!res.ok) throw new Error(`tax history failed (${res.status})`);
  return res.json();
}

export async function getSavedIds(): Promise<{ ids: string[] }> {
  return jsonGet(`${API}/saved/ids`);
}

export async function getSaved(): Promise<{ count: number; items: Property[] }> {
  return jsonGet(`${API}/saved`);
}

export async function saveProperty(id: string): Promise<void> {
  const res = await fetch(`${API}/saved`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ property_id: id }),
  });
  if (!res.ok) throw new Error("save failed");
}

export async function unsaveProperty(id: string): Promise<void> {
  const res = await fetch(`${API}/saved/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("unsave failed");
}
