import { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  Pressable,
  ActivityIndicator,
  Linking,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";
import { API_BASE } from "@/src/lib/api";
import { exportUrl } from "@/src/lib/feeds";

type HealthMap = Record<string, any>;
type PreviewLead = Record<string, any>;

const SOURCES = [
  { key: "tad", label: "TAD", note: "Tarrant Appraisal District parcel/property records" },
  { key: "tax_roll", label: "Tax Roll", note: "Official Tarrant County current + delinquent tax roll" },
  { key: "code_violations", label: "Code Violations", note: "Fort Worth code cases and violation records" },
] as const;

const statusLabel = (item: any) => {
  if (!item) return "NOT TESTED";
  return item.available ? "SOURCE ONLINE" : "SOURCE DID NOT ANSWER";
};

const addressOf = (x: PreviewLead) =>
  String(x.situs_address || x.address || x.full_address || x.property_address || x.title || "Address not returned");

const sourceOf = (x: PreviewLead) =>
  String(x.data_source || x.source_platform || x.source || "Bright Data");

export default function SettingsScreen() {
  const [testing, setTesting] = useState(false);
  const [health, setHealth] = useState<HealthMap>({});
  const [brightReady, setBrightReady] = useState<boolean | null>(null);
  const [labMessage, setLabMessage] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<PreviewLead[]>([]);
  const [previewTotal, setPreviewTotal] = useState<number | null>(null);

  const testSources = useCallback(async () => {
    setTesting(true);
    setLabMessage("");
    try {
      const [sourceResp, brightResp] = await Promise.all([
        fetch(`${API_BASE}/api/data-sources/status`),
        fetch(`${API_BASE}/api/brightdata-mcp/status`),
      ]);

      const sourceData = await sourceResp.json().catch(() => ({}));
      const brightData = await brightResp.json().catch(() => ({}));

      setHealth(sourceData || {});
      setBrightReady(
        Boolean(
          brightData?.brightdata_mcp_configured ||
          brightData?.configured ||
          brightData?.token_configured
        )
      );

      setLabMessage("Source check finished. Nothing was written to the database.");
    } catch (e: any) {
      setLabMessage(e?.message || "Source check failed.");
    } finally {
      setTesting(false);
    }
  }, []);

  useEffect(() => {
    testSources();
  }, [testSources]);

  const runBrightPreview = async () => {
    setPreviewing(true);
    setPreview([]);
    setPreviewTotal(null);
    setLabMessage("");
    try {
      const query = "include_offmarket=true&include_fsbo=true&include_hubzu=true&max_pages=1";
      const resp = await fetch(`${API_BASE}/api/brightdata-mcp/preview?${query}`);
      const data = await resp.json().catch(() => ({}));

      if (!resp.ok || data?.error) {
        throw new Error(data?.error || data?.detail || `HTTP ${resp.status}`);
      }

      const rows = Array.isArray(data?.preview) ? data.preview : [];
      setPreview(rows.slice(0, 5));
      setPreviewTotal(Number(data?.total ?? rows.length));
      setLabMessage("Bright Data preview finished. Preview only — nothing was saved.");
    } catch (e: any) {
      setLabMessage(e?.message || "Bright Data preview failed.");
    } finally {
      setPreviewing(false);
    }
  };

  const openExport = async (format: "csv" | "xlsx") => {
    const url = exportUrl(format, "all");
    if (Platform.OS === "web") window.open(url, "_blank");
    else await Linking.openURL(url);
  };

  return (
    <SafeAreaView style={s.safe} edges={["top"]}>
      <View style={s.header}>
        <Text style={s.eyebrow}>ACCOUNT · DATA</Text>
        <Text style={s.title}>Settings</Text>
      </View>

      <ScrollView contentContainerStyle={s.scroll}>
        <View style={s.card} testID="serenity-data-lab">
          <View style={s.rowBetween}>
            <View style={{ flex: 1 }}>
              <Text style={s.section}>SERENITY DATA LAB</Text>
              <Text style={s.labTitle}>Live County Sources</Text>
            </View>
            <View style={s.labPill}>
              <Text style={s.labPillText}>LIVE</Text>
            </View>
          </View>

          <Text style={s.help}>
            Test the live sources here. No admin key lives in Settings. Source credentials stay server-side in Railway.
          </Text>

          <Pressable onPress={testSources} disabled={testing} style={[s.outlineButton, testing && s.disabled]}>
            {testing ? (
              <ActivityIndicator />
            ) : (
              <Ionicons name="pulse-outline" size={17} color={colors.brandPrimary} />
            )}
            <Text style={s.outlineText}>{testing ? "Testing sources…" : "Test All Sources"}</Text>
          </Pressable>

          {SOURCES.map(({ key, label, note }) => {
            const item = health?.[key];
            const online = Boolean(item?.available);
            return (
              <View key={key} style={s.sourceCard}>
                <View style={{ flex: 1 }}>
                  <Text style={s.sourceName}>{label}</Text>
                  <Text style={s.help}>{note}</Text>
                </View>
                <Text style={[s.health, online && s.healthGood]}>{statusLabel(item)}</Text>
              </View>
            );
          })}

          <View style={s.divider} />

          <Text style={s.section}>BRIGHT DATA</Text>
          <View style={s.sourceCard}>
            <View style={{ flex: 1 }}>
              <Text style={s.sourceName}>Web Enrichment</Text>
              <Text style={s.help}>
                Preview public-web results before we merge anything into Property Master.
              </Text>
            </View>
            <Text style={[s.health, brightReady === true && s.healthGood]}>
              {brightReady === null ? "NOT TESTED" : brightReady ? "READY" : "NOT CONFIGURED"}
            </Text>
          </View>

          <Pressable
            onPress={runBrightPreview}
            disabled={previewing || brightReady === false}
            style={[s.primaryButton, (previewing || brightReady === false) && s.disabled]}
          >
            {previewing ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Ionicons name="flask-outline" size={18} color="#fff" />
            )}
            <Text style={s.buttonText}>{previewing ? "Running preview…" : "Run Bright Data Preview"}</Text>
          </Pressable>

          <Text style={s.previewOnly}>PREVIEW ONLY · NOTHING SAVED</Text>

          {previewTotal !== null ? (
            <View style={s.results}>
              <Text style={s.resultTitle}>
                Found <Text style={tabularNums}>{previewTotal.toLocaleString()}</Text> leads
              </Text>
              {preview.map((lead, index) => (
                <View key={`${addressOf(lead)}-${index}`} style={s.lead}>
                  <Text style={s.leadNum}>{index + 1}</Text>
                  <View style={{ flex: 1 }}>
                    <Text style={s.leadAddress}>{addressOf(lead)}</Text>
                    <Text style={s.help}>{sourceOf(lead)}</Text>
                  </View>
                </View>
              ))}
            </View>
          ) : null}

          {labMessage ? <Text style={s.labMessage}>{labMessage}</Text> : null}
        </View>

        <View style={s.card}>
          <Text style={s.section}>SOURCE PLAN</Text>
          <Text style={s.help}>
            TAD, Tax Roll, Code Violations, Pre-Foreclosure and Probate flow into County Records first, then match into Property Master. Excel stays available as a backup/manual intake path.
          </Text>
          <Text style={s.help}>
            Probate: Odyssey/Tarrant County via Bright Data is the remaining source we are wiring.
          </Text>
        </View>

        <View style={s.card}>
          <Text style={s.section}>EXPORT DEALS</Text>
          <View style={s.row}>
            <Pressable onPress={() => openExport("csv")} style={[s.primaryButton, { flex: 1 }]}>
              <Text style={s.buttonText}>Export CSV</Text>
            </Pressable>
            <Pressable onPress={() => openExport("xlsx")} style={[s.primaryButton, { flex: 1, backgroundColor: colors.brandSecondary }]}>
              <Text style={s.buttonText}>Export Excel</Text>
            </Pressable>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  scroll: { padding: spacing.lg, paddingBottom: spacing.xxxl },
  eyebrow: { fontSize: 10, color: colors.muted, fontWeight: "800", letterSpacing: 1 },
  title: { fontSize: 24, fontWeight: "800", color: colors.onSurface, marginTop: 2 },
  card: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.lg,
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: spacing.md,
  },
  section: { fontSize: 11, fontWeight: "800", letterSpacing: 1.2, color: colors.muted },
  labTitle: { fontSize: 20, color: colors.onSurface, fontWeight: "800", marginTop: 3 },
  help: { color: colors.muted, fontSize: 11, lineHeight: 16, marginTop: 6 },
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  rowBetween: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  labPill: { paddingHorizontal: 9, paddingVertical: 5, borderRadius: radius.pill, backgroundColor: "#E3EBE5" },
  labPillText: { fontSize: 9, fontWeight: "900", color: colors.onSurface },
  outlineButton: {
    minHeight: 46,
    marginTop: 12,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.brandPrimary,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  outlineText: { color: colors.brandPrimary, fontSize: 12, fontWeight: "800" },
  sourceCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 12,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    marginTop: 10,
  },
  sourceName: { color: colors.onSurface, fontSize: 13, fontWeight: "800" },
  health: { maxWidth: 135, color: colors.muted, fontSize: 9, fontWeight: "900", textAlign: "right" },
  healthGood: { color: "#2E6B43" },
  divider: { height: 1, backgroundColor: colors.border, marginVertical: 16 },
  primaryButton: {
    minHeight: 46,
    marginTop: 12,
    borderRadius: radius.md,
    backgroundColor: colors.brandPrimary,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingHorizontal: 12,
  },
  buttonText: { color: "#fff", fontSize: 12, fontWeight: "800" },
  disabled: { opacity: 0.55 },
  previewOnly: { textAlign: "center", color: colors.muted, fontSize: 9, fontWeight: "800", marginTop: 6 },
  results: { marginTop: 12, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 10 },
  resultTitle: { color: colors.onSurface, fontSize: 14, fontWeight: "800" },
  lead: { flexDirection: "row", gap: 8, paddingVertical: 8, borderTopWidth: 1, borderTopColor: colors.border },
  leadNum: { width: 22, color: colors.brandPrimary, fontWeight: "900" },
  leadAddress: { color: colors.onSurface, fontSize: 12, fontWeight: "800" },
  labMessage: { marginTop: 12, color: colors.onSurface, fontSize: 11, lineHeight: 16 },
});
