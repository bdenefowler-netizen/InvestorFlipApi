// API client for TarrantREI backend
import { adminRequestHeaders } from "@/src/lib/admin";

const BASE = (process.env.EXPO_PUBLIC_BACKEND_URL || "https://investorflipapi-production-4970.up.railway.app").replace(/\/$/, "");
const API = `${BASE}/api`;

export type Property = {
  id: string;
  account_id?: string | null;
  parcel_id?: string | null;
  situs_address: string;
  city: string;
  state: string;
  zip: string;
  county: string;
  beds?: number | null;
  baths?: number | null;
  sqft?: number | null;
  year_built?: number | null;
  lot_size_sqft?: number | null;
  image_url?: string | { href?: string; url?: string } | null;
  photos?: Array<string | { href?: string; url?: string }> | null;
  price: number;
  market_value?: number | null;
  tax_roll_market_value?: number | null;
  assessed_value?: number | null;
  annual_taxes?: number | null;
  equity_estimate?: number | null;
  equity_status?: string;
  est_roi_pct?: number | null;
  roi_status?: string;
  value_benchmark?: number | null;
  value_benchmark_source?: string | null;
  value_spread?: number | null;
  discount_to_benchmark_pct?: number | null;
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
  investment_score?: number | null;
  wholesale_score?: number | null;
  flip_score?: number | null;
  rental_score?: number | null;
  risk_score?: number | null;
  score_confidence?: "high" | "medium" | "low" | "insufficient";
  score_kind?: string;
  score_missing_inputs?: string[];
  source_platform?: string | null;
  source_mls?: string | null;
  mls_id?: string | null;
  property_type?: string | null;
  home_type?: string | null;
  listing_status?: string | null;
  listing_description?: string | null;
  listing_agent_name?: string | null;
  listing_agent_phone?: string | null;
  listing_agent_email?: string | null;
  listing_agent_url?: string | null;
  listing_agent_rating?: number | null;
  listing_agent_review_count?: number | null;
  listing_agent_photo_url?: string | null;
  listing_agent_fulfillment_id?: string | null;
  agent_listings?: Array<{
    id: string;
    address?: string | null;
    price?: number | null;
    beds?: number | null;
    baths?: number | null;
    sqft?: number | null;
    image_url?: string | null;
    detail_url?: string | null;
  }>;
  agent_listings_source?: string | null;
  broker_name?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  detail_url?: string | null;
  listing_date?: string | null;
  hoa_fee?: number | null;
  listing_tags?: string[];
  is_target_opportunity?: boolean;
  opportunity_signal_keys?: string[];
  opportunity_signals?: string[];
  opportunity_evidence?: string[];
  raw_source_excerpt?: unknown;
  feed_extra?: Record<string, unknown> | null;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function mediaUrl(value: unknown): string | undefined {
  if (typeof value === "string") return highQualityImageUrl(value);
  const record = asRecord(value);
  const url = record.href || record.url || record.src;
  return typeof url === "string" ? highQualityImageUrl(url) : undefined;
}

function highQualityImageUrl(url: string): string {
  const secure = url.trim().replace(/^http:\/\//, "https://");
  if (!secure) return secure;
  if (secure.includes("ap.rdcpix.com") && !secure.includes("?")) {
    return `${secure}?w=1200&q=90`;
  }
  if (secure.includes("photos.zillowstatic.com") && secure.includes("-p_e.")) {
    return secure.replace("-p_e.", "-cc_ft_960.");
  }
  return secure;
}

function numberValue(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value.replace(/[^0-9.-]/g, ""));
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return undefined;
}

function stringValue(...values: unknown[]): string | undefined {
  return values.find((value) => typeof value === "string" && value.trim()) as string | undefined;
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** Recover fields embedded by older Railway syncs in raw_source_excerpt. */
export function normalizeProperty(property: Property): Property {
  const raw = asRecord(property.raw_source_excerpt);
  if (Object.keys(raw).length === 0) return property;

  const description = asRecord(raw.description);
  const location = asRecord(raw.location);
  const address = asRecord(location.address);
  const coordinate = asRecord(address.coordinate);
  const source = asRecord(raw.source);
  const sourceAgent = asRecord(arrayValue(source.agents)[0]);
  const advertiser = asRecord(arrayValue(raw.advertisers)[0]);
  const office = asRecord(advertiser.office);
  const officePhone = asRecord(arrayValue(office.phones)[0]);
  const fulfillmentValue = (
    advertiser.fulfillment_id
    ?? advertiser.fulfillmentId
    ?? office.fulfillment_id
    ?? office.fulfillmentId
  );
  const fulfillmentId = fulfillmentValue != null ? String(fulfillmentValue) : undefined;

  const photos = [
    ...arrayValue(property.photos),
    ...arrayValue(raw.photos),
    raw.primary_photo,
  ]
    .map(mediaUrl)
    .filter((url): url is string => Boolean(url));
  const uniquePhotos = [...new Set(photos)];
  const tags = arrayValue(raw.tags).filter((tag): tag is string => typeof tag === "string");

  return {
    ...property,
    beds: property.beds ?? numberValue(description.beds),
    baths: property.baths ?? numberValue(description.baths, description.baths_full_calc),
    sqft: property.sqft ?? numberValue(description.sqft),
    year_built: property.year_built ?? numberValue(description.year_built),
    lot_size_sqft: property.lot_size_sqft ?? numberValue(description.lot_sqft),
    latitude: property.latitude ?? numberValue(coordinate.lat),
    longitude: property.longitude ?? numberValue(coordinate.lon),
    image_url: property.image_url || uniquePhotos[0],
    photos: uniquePhotos,
    property_type: property.property_type || stringValue(description.type),
    home_type: property.home_type || stringValue(description.type),
    listing_status: property.listing_status || stringValue(raw.status),
    listing_description: property.listing_description || stringValue(description.text),
    source_mls: property.source_mls || stringValue(source.name),
    mls_id: property.mls_id || stringValue(source.listing_id),
    listing_agent_name: property.listing_agent_name || stringValue(sourceAgent.agent_name),
    listing_agent_phone: property.listing_agent_phone || stringValue(officePhone.number),
    listing_agent_email: property.listing_agent_email || stringValue(sourceAgent.email),
    listing_agent_url: property.listing_agent_url || stringValue(
      sourceAgent.agent_url,
      sourceAgent.profile_url,
      sourceAgent.href,
    ),
    listing_agent_fulfillment_id: property.listing_agent_fulfillment_id || fulfillmentId,
    broker_name: property.broker_name || stringValue(sourceAgent.office_name, office.name),
    detail_url: property.detail_url || stringValue(raw.href),
    listing_date: property.listing_date || stringValue(raw.list_date),
    hoa_fee: property.hoa_fee ?? numberValue(asRecord(raw.hoa).fee),
    listing_tags: property.listing_tags?.length ? property.listing_tags : tags,
  };
}

export function propertyPhotoUrls(
  property: Pick<Property, "image_url" | "photos">,
): string[] {
  const urls = [
    mediaUrl(property.image_url),
    ...arrayValue(property.photos).map(mediaUrl),
  ].filter((url): url is string => Boolean(url));
  return [...new Set(urls)];
}

export function propertyImageUrl(
  property: Pick<Property, "image_url" | "photos">,
): string | undefined {
  return propertyPhotoUrls(property)[0];
}

export type FilterDef = { key: string; label: string; count: number };

export type CountyRecord = {
  id: string;
  account_id?: string;
  parcel_id?: string;
  situs_address: string;
  city?: string;
  state?: string;
  zip?: string;
  county?: string;
  owner_name?: string;
  owner_mailing_address?: string;
  mailing_city?: string;
  mailing_state?: string;
  mailing_zip?: string;
  beds?: number | null;
  baths?: number | null;
  sqft?: number | null;
  year_built?: number | null;
  lot_size_sqft?: number | null;
  lot_size_acres?: number | null;
  garage_capacity?: number | null;
  appraised_value?: number | null;
  market_value?: number | null;
  tax_roll_market_value?: number | null;
  land_value?: number | null;
  improvement_value?: number | null;
  annual_taxes?: number | null;
  current_tax_amount_due?: number | null;
  prior_tax_amount_due?: number | null;
  tax_delinquent?: boolean;
  delinquency_date?: string;
  legal_description?: string;
  roll_code?: string;
  account_status_codes?: string;
  owner_exemption_codes?: string;
  tad_litigation_flag?: string;
  school_district?: string;
  deed_date?: string;
  absentee_owner?: boolean;
  out_of_state_owner?: boolean;
  trust_owned?: boolean;
  company_owned?: boolean;
  has_tad?: boolean;
  has_tax_roll?: boolean;
  sources: string[];
  completeness_score?: number;
  missing_fields?: string[];
  tad_updated_at?: string;
  tax_roll_updated_at?: string;
  updated_at?: string;
  tad_raw?: Record<string, unknown>;
  tax_roll_raw?: Record<string, unknown>;
};

export type CountyRecordStats = {
  total: number;
  with_tad: number;
  with_tax_roll: number;
  tax_delinquent: number;
  tad_next_offset: number;
  tad_snapshot_completed_at?: string | null;
  recent_syncs: {
    id: string;
    source: string;
    status: string;
    written?: number;
    fetched?: number;
    created_at: string;
  }[];
};

export type AddressSuggestion = {
  type: string;
  title: string;
  street_address: string;
  city: string;
  state: string;
  zip: string;
  county: string;
  property_reach_id?: number | string | null;
};

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
): Promise<{ count: number; total?: number; items: Property[] }> {
  const params = new URLSearchParams({ filter, limit: "200" });
  if (search) params.set("search", search);
  const data = await jsonGet<{ count: number; total?: number; items: Property[] }>(
    `${API}/properties?${params.toString()}`,
  );
  return { ...data, items: data.items.map(normalizeProperty) };
}

export async function getCountyRecords(
  source: "all" | "tad" | "tax_roll" | "tax_delinquent" = "all",
  search = "",
  page = 1,
  limit = 75,
): Promise<{ count: number; total: number; page: number; pages: number; items: CountyRecord[] }> {
  const params = new URLSearchParams({ source, page: String(page), limit: String(limit) });
  if (search.trim()) params.set("search", search.trim());
  return jsonGet(`${API}/county-records?${params.toString()}`);
}

export async function getCountyRecordStats(): Promise<CountyRecordStats> {
  return jsonGet(`${API}/county-records/stats`);
}

export async function getCountyRecord(id: string): Promise<CountyRecord> {
  return jsonGet(`${API}/county-records/${encodeURIComponent(id)}`);
}

export function countyRecordsCsvUrl(
  source: "all" | "tad" | "tax_roll" | "tax_delinquent" = "all",
): string {
  return `${API}/county-records/export.csv?source=${encodeURIComponent(source)}`;
}

export async function getAddressSuggestions(
  query: string,
  signal?: AbortSignal,
): Promise<{ count: number; items: AddressSuggestion[]; cached: boolean }> {
  const params = new URLSearchParams({ query, limit: "6" });
  const headers = await adminRequestHeaders();
  const res = await fetch(`${API}/address-suggestions?${params.toString()}`, { signal, headers });
  if (!res.ok) throw new Error(`address suggestions failed (${res.status})`);
  return res.json();
}

export async function getProperty(id: string): Promise<Property> {
  return normalizeProperty(await jsonGet<Property>(`${API}/properties/${id}`));
}

export async function getNearby(id: string): Promise<{
  nearby_foreclosures: Pick<Property, "id" | "situs_address" | "price" | "listing_type" | "image_url">[];
  nearby_investor_purchases: Pick<Property, "id" | "situs_address" | "price" | "owner_type" | "image_url">[];
}> {
  return jsonGet(`${API}/properties/${id}/nearby`);
}

export async function getAIAnalysis(id: string): Promise<{ property_id: string; narrative: string }> {
  const headers = await adminRequestHeaders();
  const res = await fetch(`${API}/properties/${id}/ai-analysis`, { method: "POST", headers });
  if (!res.ok) throw new Error(`AI analysis failed (${res.status})`);
  return res.json();
}

export type QuillRepairs = {
  level?: string;
  per_sqft?: number;
  estimate?: number;
  contingency?: number;
  total?: number;
  sqft?: number;
};

export type QuillNumbers = {
  price: number;
  arv: number;
  validated_arv?: number;
  spread?: number;
  repairs?: QuillRepairs;
  deal_type?: string;
  deal_confidence?: number;
  deal_reason?: string;
};

export type QuillValueCheck = {
  available_sources: number;
  sources: Record<string, number>;
  validated_arv: number;
  confidence: "high" | "medium" | "low";
  flags?: string[];
  disagreements?: unknown[];
};

export type QuillPnl = {
  purchase_price: number;
  estimated_repairs: number;
  closing_costs: number;
  carry_costs: number;
  total_investment: number;
  arv: number;
  commission?: number;
  net_profit: number;
  roi_pct: number;
};

export type QuillLiveZillow = {
  status: string;
  zestimate?: number | null;
  cotality?: number | null;
  redfin_value?: number | null;
  zillow_url?: string;
  realtor_url?: string;
  redfin_url?: string;
  comps?: Array<{ url: string; title: string; estimated_value: number }>;
};

export type QuillFlag = {
  type?: string;
  label?: string;
};

export type QuillAnalysis = {
  property?: {
    address?: string;
    city?: string;
    state?: string;
    zip?: string;
    beds?: number;
    baths?: number;
    sqft?: number;
    year_built?: number;
  };
  numbers: QuillNumbers;
  value_check: QuillValueCheck;
  value_take: string;
  pnl: QuillPnl;
  flags?: QuillFlag[];
  flood?: { in_flood_zone?: boolean | null; zone?: string | null; note?: string };
  permits?: { additions_found?: boolean | null; permits?: unknown[]; note?: string };
  live_zillow?: QuillLiveZillow;
  take: string;
  generated_at: string;
};

export async function getQuillAnalysis(id: string): Promise<QuillAnalysis> {
  const headers = await adminRequestHeaders();
  const res = await fetch(`${API}/quill/analyze/${encodeURIComponent(id)}`, { headers });
  if (!res.ok) throw new Error(`Quill analysis failed (${res.status})`);
  return res.json();
}

export type Enrichment = {
  property_id: string;
  address_queried: string;
  source_api?: string;
  found: boolean;
  zpid?: number | string;
  beds?: number;
  baths?: number;
  sqft?: number;
  lot_size_sqft?: number;
  year_built?: number;
  lot_size?: string;
  home_type?: string;
  home_status?: string;
  list_price?: number;
  zestimate?: number;
  rent_zestimate?: number;
  tax_assessed_value?: number;
  latitude?: number;
  longitude?: number;
  rapidapi_address?: string;
  rapidapi_city?: string;
  rapidapi_state?: string;
  rapidapi_zip?: string;
  is_foreclosure?: boolean;
  mls_id?: string;
  source_mls?: string;
  listing_agent_name?: string;
  listing_agent_phone?: string;
  broker_name?: string;
  appliances?: string[];
  cooling?: string[];
  heating?: string[];
  parcel_id?: string;
  photos?: string[];
  hi_res_image?: string;
  description?: string;
  price_history?: Record<string, unknown>[];
  provider_tax_history?: Record<string, unknown>[];
  property_detail_found?: boolean;
  property_detail_endpoint?: string;
  error?: string;
};

export async function enrichProperty(id: string): Promise<Enrichment> {
  const headers = await adminRequestHeaders();
  const res = await fetch(`${API}/properties/${id}/enrich`, { method: "POST", headers });
  if (!res.ok) throw new Error(`enrich failed (${res.status})`);
  return res.json();
}

export type IntakeEnrichment = {
  county: { live_checked: number; enriched: number; tad_lookups: number; missing: number };
  details: { attempted: number; found: number; not_found: number; errors: { property_id: string; error: string }[] };
};

export type UploadIntakeResult = {
  ok: boolean;
  filename: string;
  rows_read: number;
  accepted: number;
  rejected: number;
  duplicates_merged: number;
  inserted: number;
  updated: number;
  property_ids: string[];
  rejections: { row: number; reason: string }[];
  enrichment: IntakeEnrichment;
};

export type PasteIntakeResult = UploadIntakeResult;

export type LinkIntakeResult = {
  ok: boolean;
  property_id: string;
  property: Property;
  source_host: string;
  enrichment: IntakeEnrichment;
};

export type ProviderSyncReport = {
  provider: string;
  status: "success" | "empty" | "error" | "skipped";
  fetched?: number;
  accepted?: number;
  errors?: string[];
};

export type AllSourceSyncResult = {
  ok: boolean;
  upserted: number;
  total_properties_touched: number;
  missed: number;
  retired: number;
  providers: ProviderSyncReport[];
  county_enrichment: { live_checked: number; enriched: number; tad_lookups: number; missing: number };
  detail_enrichment: { attempted: number; found: number; not_found: number; errors: unknown[] };
};

export async function uploadPropertyFile(asset: {
  uri: string;
  name?: string | null;
  mimeType?: string | null;
  file?: Blob;
  }): Promise<UploadIntakeResult> {
  const headers = await adminRequestHeaders();
  const form = new FormData();
  if (asset.file) form.append("file", asset.file, asset.name || "property-import.csv");
  else {
    form.append("file", {
      uri: asset.uri,
      name: asset.name || "property-import.csv",
      type: asset.mimeType || "text/csv",
    } as any);
  }
  const res = await fetch(`${API}/intake/upload`, { method: "POST", headers, body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `upload failed (${res.status})`);
  return data;
}

export async function pastePropertyCsv(csvText: string, filename = "pasted-leads.csv"): Promise<PasteIntakeResult> {
  const headers = await adminRequestHeaders({ "Content-Type": "application/json" });
  const res = await fetch(`${API}/intake/paste`, {
    method: "POST",
    headers,
    body: JSON.stringify({ filename, csv_text: csvText }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `paste import failed (${res.status})`);
  return data;
}

export async function addPropertyLink(url: string): Promise<LinkIntakeResult> {
  const headers = await adminRequestHeaders({ "Content-Type": "application/json" });
  const res = await fetch(`${API}/intake/link`, {
    method: "POST",
    headers,
    body: JSON.stringify({ url }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `link import failed (${res.status})`);
  return { ...data, property: normalizeProperty(data.property) };
}

export async function syncAllListingSources(limit = 50): Promise<AllSourceSyncResult> {
  const headers = await adminRequestHeaders();
  const res = await fetch(`${API}/live/sync-fort-worth?limit=${limit}`, { method: "POST", headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `source sync failed (${res.status})`);
  return data;
}

export type TaxHistoryEntry = {
  year: number;
  tax: number;
  assessment?: { building?: number; land?: number; total?: number };
  market?: { building?: number; land?: number; total?: number };
};

export async function getTaxHistory(id: string): Promise<{ tax_history: TaxHistoryEntry[]; available: boolean }> {
  const headers = await adminRequestHeaders();
  const res = await fetch(`${API}/properties/${id}/tax-history`, { headers });
  if (!res.ok) throw new Error(`tax history failed (${res.status})`);
  return res.json();
}

export async function getSavedIds(): Promise<{ ids: string[] }> {
  return jsonGet(`${API}/saved/ids`);
}

export async function getSaved(): Promise<{ count: number; total?: number; items: Property[] }> {
  const data = await jsonGet<{ count: number; total?: number; items: Property[] }>(`${API}/saved`);
  return { ...data, items: data.items.map(normalizeProperty) };
}

export async function saveProperty(id: string): Promise<void> {
  const headers = await adminRequestHeaders({ "Content-Type": "application/json" });
  const res = await fetch(`${API}/saved`, {
    method: "POST",
    headers,
    body: JSON.stringify({ property_id: id }),
  });
  if (!res.ok) throw new Error("save failed");
}

export async function unsaveProperty(id: string): Promise<void> {
  const headers = await adminRequestHeaders();
  const res = await fetch(`${API}/saved/${id}`, { method: "DELETE", headers });
  if (!res.ok) throw new Error("unsave failed");
}
