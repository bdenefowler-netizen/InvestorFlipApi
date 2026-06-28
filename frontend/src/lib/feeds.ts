// Feeds + export API helpers
const BASE = (process.env.EXPO_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");
const API = `${BASE}/api`;

export type FeedStatus = { name: string; properties_from_feed: number };

export async function getFeedsStatus(): Promise<{ feeds: FeedStatus[] }> {
  const r = await fetch(`${API}/feeds/status`);
  if (!r.ok) throw new Error("feeds status failed");
  return r.json();
}

export type SyncResult = {
  by_feed: Record<string, { fetched: number; inserted: number; matched: number; skipped: number; error?: string }>;
  totals: { inserted: number; matched: number; skipped: number };
};

export async function syncFeeds(only?: string, limit = 50): Promise<SyncResult> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (only) params.set("only", only);
  const r = await fetch(`${API}/feeds/sync?${params.toString()}`, { method: "POST" });
  if (!r.ok) throw new Error("sync failed");
  return r.json();
}

export function exportUrl(format: "csv" | "xlsx", filter: string = "all"): string {
  return `${API}/export.${format}?filter=${encodeURIComponent(filter)}`;
}
