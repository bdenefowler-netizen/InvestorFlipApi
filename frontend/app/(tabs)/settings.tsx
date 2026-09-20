import { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator, Linking, Platform, TextInput } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";
import { getFeedsStatus, exportUrl, type FeedStatus } from "@/src/lib/feeds";
import { getStoredAdminKey, saveAdminKey } from "@/src/lib/admin";
import { API_BASE } from "@/src/lib/api";

type Lead = Record<string, any>;
type BrightStatus = { brightdata_mcp_configured?: boolean; sources?: { name?: string }[] };
type Preview = { total?: number; preview?: Lead[]; by_source?: Record<string, number>; error?: string };

const addr = (x: Lead) => String(x.situs_address || x.address || x.full_address || x.title || "Address not returned");
const source = (x: Lead) => String(x.data_source || x.source_platform || x.source || "Bright Data");
const price = (x: Lead) => {
  const n = Number(x.price);
  return Number.isFinite(n) && n > 0 ? `$${Math.round(n).toLocaleString()}` : "price unknown";
};

export default function SettingsScreen() {
  const [feeds, setFeeds] = useState<FeedStatus[]>([]);
  const [adminKey, setAdminKey] = useState("");
  const [adminSaved, setAdminSaved] = useState(false);
  const [status, setStatus] = useState<BrightStatus | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setFeeds((await getFeedsStatus()).feeds); } catch {}
    try {
      const r = await fetch(`${API_BASE}/api/brightdata-mcp/status`);
      const d = await r.json();
      if (!r.ok) throw new Error(d?.detail || `status ${r.status}`);
      setStatus(d);
    } catch (e: any) { setError(e?.message || "Bright Data status unavailable"); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    getStoredAdminKey().then((k) => { setAdminKey(k); setAdminSaved(Boolean(k)); });
  }, []);

  const saveKey = async () => {
    const ok = await saveAdminKey(adminKey);
    setAdminSaved(ok && Boolean(adminKey.trim()));
    if (!ok) setError("Admin key could not be stored.");
  };

  const runExperiment = async () => {
    setRunning(true); setPreview(null); setError("");
    try {
      const q = "include_offmarket=true&include_fsbo=true&include_hubzu=true&max_pages=1";
      const r = await fetch(`${API_BASE}/api/brightdata-mcp/preview?${q}`);
      const d: Preview = await r.json().catch(() => ({}));
      if (!r.ok || d.error) throw new Error(d.error || `preview ${r.status}`);
      setPreview(d);
    } catch (e: any) { setError(e?.message || "Bright Data preview failed"); }
    finally { setRunning(false); }
  };

  const openExport = async (format: "csv" | "xlsx") => {
    const url = exportUrl(format, "all");
    if (Platform.OS === "web") window.open(url, "_blank");
    else await Linking.openURL(url);
  };

  const rows = Array.isArray(preview?.preview) ? preview!.preview!.slice(0, 5) : [];
  const sourceSummary = Object.entries(preview?.by_source || {}).map(([k, v]) => `${k}: ${v}`).join(" · ");

  return (
    <SafeAreaView style={s.safe} edges={["top"]}>
      <View style={s.header}><Text style={s.eyebrow}>ACCOUNT · DATA</Text><Text style={s.title}>Settings</Text></View>
      <ScrollView contentContainerStyle={s.scroll}>
        <View style={s.card}>
          <Text style={s.section}>PRIVATE OPERATIONS</Text>
          <Text style={s.help}>Railway INVESTORFLIP_ADMIN_KEY. Stored on this device; used for protected imports and enrichment.</Text>
          <TextInput value={adminKey} onChangeText={(v) => { setAdminKey(v); setAdminSaved(false); }} secureTextEntry autoCapitalize="none" autoCorrect={false} placeholder="Railway admin key" placeholderTextColor={colors.muted} style={s.input} />
          <Pressable onPress={saveKey} style={s.button}><Ionicons name={adminSaved ? "checkmark-circle" : "lock-closed"} size={16} color="#fff" /><Text style={s.buttonText}>{adminSaved ? "Key Saved" : "Save Securely"}</Text></Pressable>
        </View>

        <View style={s.card} testID="bright-data-lab">
          <View style={s.row}>
            <View style={{ flex: 1 }}><Text style={s.section}>DATA ENRICHMENT · BRIGHT DATA</Text><Text style={s.labTitle}>Serenity Data Lab</Text></View>
            <View style={[s.pill, { backgroundColor: status?.brightdata_mcp_configured ? "#E3EBE5" : "#F7E4E1" }]}>
              <Text style={s.pillText}>{status?.brightdata_mcp_configured ? "READY" : "NOT CONFIGURED"}</Text>
            </View>
          </View>
          <Text style={s.help}>One-page experiment across configured scrapers. Preview only — nothing is saved or merged.</Text>
          <Text style={s.sources}>{(status?.sources || []).map(x => x.name).filter(Boolean).join(" · ") || "OffMarketDeck · FSBO · Hubzu"}</Text>
          <Pressable testID="run-bright-data-experiment" onPress={runExperiment} disabled={running || status?.brightdata_mcp_configured === false} style={[s.experiment, (running || status?.brightdata_mcp_configured === false) && { opacity: .5 }]}>
            {running ? <ActivityIndicator color="#fff" /> : <><Ionicons name="flask-outline" size={17} color="#fff" /><Text style={s.buttonText}>Run Bright Data Experiment</Text></>}
          </Pressable>
          <Text style={s.previewOnly}>PREVIEW ONLY · NOTHING SAVED</Text>
          {error ? <Text style={s.error}>{error}</Text> : null}
          {preview ? <View style={s.results}>
            <Text style={s.resultTitle}>Found <Text style={tabularNums}>{Number(preview.total || 0).toLocaleString()}</Text> unique leads</Text>
            {sourceSummary ? <Text style={s.help}>{sourceSummary}</Text> : null}
            {rows.map((x, i) => <View key={`${addr(x)}-${i}`} style={s.lead}>
              <Text style={s.leadNum}>{i + 1}</Text>
              <View style={{ flex: 1 }}><Text style={s.leadAddr}>{addr(x)}</Text><Text style={s.help}>{source(x)} · {price(x)}{Number.isFinite(Number(x.distress_score)) ? ` · distress ${Number(x.distress_score)}` : ""}</Text></View>
            </View>)}
            {!rows.length ? <Text style={s.help}>Request completed with no preview rows.</Text> : null}
          </View> : null}
        </View>

        <View style={s.card}>
          <Text style={s.section}>SOURCE INVENTORY · READ ONLY</Text>
          <Text style={s.help}>Existing feed records remain visible here. Bright Data is the single web-enrichment runner above.</Text>
          {feeds.map(f => <View key={f.name} style={s.feed}><Ionicons name="document-text" size={16} color={colors.brandPrimary} /><Text style={s.feedText}>{f.name} · <Text style={tabularNums}>{f.properties_from_feed.toLocaleString()}</Text></Text></View>)}
        </View>

        <View style={s.card}>
          <Text style={s.section}>DATA SOURCES</Text>
          <Text style={s.help}>County/TAD uploads: TAD · Tax · Pre-Foreclosure · Probate · Code Violations · Owner</Text>
          <Text style={s.help}>Bright Data: public-web discovery + structured scraping. Deep Lookup comes next for missing fields after this preview is verified.</Text>
        </View>

        <View style={s.card}>
          <Text style={s.section}>EXPORT DEALS</Text>
          <View style={s.row}><Pressable onPress={() => openExport("csv")} style={[s.button, { flex: 1 }]}><Text style={s.buttonText}>Export CSV</Text></Pressable><Pressable onPress={() => openExport("xlsx")} style={[s.button, { flex: 1, backgroundColor: colors.brandSecondary }]}><Text style={s.buttonText}>Export Excel</Text></Pressable></View>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.md, borderBottomWidth: 1, borderBottomColor: colors.border },
  scroll: { padding: spacing.lg, paddingBottom: spacing.xxxl },
  eyebrow: { fontSize: 10, color: colors.muted, fontWeight: "800", letterSpacing: 1 },
  title: { fontSize: 24, fontWeight: "800", color: colors.onSurface, marginTop: 2 },
  card: { backgroundColor: colors.surfaceSecondary, borderRadius: radius.lg, padding: spacing.lg, borderWidth: 1, borderColor: colors.border, marginBottom: spacing.md },
  section: { fontSize: 11, fontWeight: "800", letterSpacing: 1.2, color: colors.muted },
  help: { color: colors.muted, fontSize: 11, lineHeight: 16, marginTop: 6 },
  input: { minHeight: 44, marginTop: 10, borderWidth: 1, borderColor: colors.borderStrong, borderRadius: radius.md, paddingHorizontal: 12, color: colors.onSurface, backgroundColor: colors.surface },
  button: { minHeight: 42, marginTop: 9, borderRadius: radius.md, backgroundColor: colors.brandPrimary, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, paddingHorizontal: 12 },
  buttonText: { color: "#fff", fontSize: 12, fontWeight: "800" },
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  labTitle: { fontSize: 20, color: colors.onSurface, fontWeight: "800", marginTop: 3 },
  pill: { paddingHorizontal: 9, paddingVertical: 5, borderRadius: radius.pill },
  pillText: { fontSize: 9, fontWeight: "900", color: colors.onSurface },
  sources: { color: colors.onSurface, fontSize: 11, fontWeight: "700", marginTop: 10 },
  experiment: { minHeight: 48, marginTop: 12, borderRadius: radius.md, backgroundColor: colors.brandPrimary, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8 },
  previewOnly: { textAlign: "center", color: colors.muted, fontSize: 9, fontWeight: "800", marginTop: 6 },
  error: { color: colors.error, fontSize: 11, marginTop: 10 },
  results: { marginTop: 12, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 10 },
  resultTitle: { color: colors.onSurface, fontSize: 14, fontWeight: "800" },
  lead: { flexDirection: "row", gap: 8, paddingVertical: 8, borderTopWidth: 1, borderTopColor: colors.border },
  leadNum: { width: 22, color: colors.brandPrimary, fontWeight: "900" },
  leadAddr: { color: colors.onSurface, fontSize: 12, fontWeight: "800" },
  feed: { flexDirection: "row", gap: 8, alignItems: "center", marginTop: 10 },
  feedText: { color: colors.onSurface, fontSize: 12, fontWeight: "700" },
});
