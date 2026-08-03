import { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator, Linking, Platform, TextInput } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";
import { getFeedsStatus, syncFeeds, exportUrl, type FeedStatus, type SyncResult } from "@/src/lib/feeds";
import { getStoredAdminKey, saveAdminKey } from "@/src/lib/admin";

function Row({ icon, label, value }: { icon: any; label: string; value: string }) {
  return (
    <View style={styles.row} testID={`settings-${label.toLowerCase().replace(/\s/g, "-")}`}>
      <View style={styles.rowIcon}><Ionicons name={icon} size={16} color={colors.brandPrimary} /></View>
      <View style={{ flex: 1 }}>
        <Text style={styles.rowLabel}>{label}</Text>
        <Text style={styles.rowValue}>{value}</Text>
      </View>
    </View>
  );
}

export default function SettingsScreen() {
  const [feeds, setFeeds] = useState<FeedStatus[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [lastSync, setLastSync] = useState<SyncResult | null>(null);
  const [adminKey, setAdminKey] = useState("");
  const [adminSaved, setAdminSaved] = useState(false);
  const [operationError, setOperationError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { const d = await getFeedsStatus(); setFeeds(d.feeds); } catch {}
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    getStoredAdminKey().then((key) => {
      setAdminKey(key);
      setAdminSaved(Boolean(key));
    });
  }, []);

  const persistAdminKey = async () => {
    const saved = await saveAdminKey(adminKey);
    setAdminSaved(saved && Boolean(adminKey.trim()));
    setOperationError(saved ? null : "The admin key could not be stored securely.");
  };

  const runSync = async (only?: string) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    setSyncing(true);
    setOperationError(null);
    try {
      const r = await syncFeeds(only, 50);
      setLastSync(r);
      await load();
    } catch (error: any) {
      setOperationError(error?.message || "The sync could not be started.");
    }
    finally { setSyncing(false); }
  };

  const openExport = async (format: "csv" | "xlsx") => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    const url = exportUrl(format, "all");
    if (Platform.OS === "web") {
      window.open(url, "_blank");
    } else {
      await Linking.openURL(url);
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.eyebrow}>ACCOUNT · DATA</Text>
        <Text style={styles.title}>Settings</Text>
      </View>
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxxl }}>
        <View style={styles.card}>
          <Text style={styles.section}>PRIVATE OPERATIONS</Text>
          <Text style={styles.adminHelp}>
            Enter the same value as Railway’s INVESTORFLIP_ADMIN_KEY. It stays in this device’s secure storage and protects paid pulls, imports, and Quill analysis.
          </Text>
          <TextInput
            value={adminKey}
            onChangeText={(value) => { setAdminKey(value); setAdminSaved(false); }}
            secureTextEntry
            autoCapitalize="none"
            autoCorrect={false}
            placeholder="Railway admin key"
            placeholderTextColor={colors.muted}
            style={styles.adminInput}
          />
          <Pressable onPress={persistAdminKey} style={styles.adminButton}>
            <Ionicons name={adminSaved ? "checkmark-circle" : "lock-closed"} size={16} color="#fff" />
            <Text style={styles.adminButtonText}>{adminSaved ? "Key Saved" : "Save Securely"}</Text>
          </Pressable>
          {operationError ? <Text style={styles.operationError}>{operationError}</Text> : null}
        </View>

        {/* Feeds */}
        <View style={styles.card}>
          <View style={styles.cardHeader}>
            <Text style={styles.section}>Live Feeds</Text>
            <Pressable
              testID="sync-all-button"
              onPress={() => runSync()}
              disabled={syncing}
              style={[styles.syncBtn, syncing && { opacity: 0.5 }]}
            >
              {syncing ? <ActivityIndicator size="small" color="#fff" /> : (
                <>
                  <Ionicons name="refresh" size={14} color="#fff" />
                  <Text style={styles.syncBtnText}>Sync All</Text>
                </>
              )}
            </Pressable>
          </View>
          {feeds.map((f) => (
            <View key={f.name} style={styles.feedRow} testID={`feed-${f.name.toLowerCase().replace(/\s/g, "-")}`}>
              <View style={styles.rowIcon}>
                <Ionicons
                  name={f.name === "Xome" ? "business" : f.name === "RealtyInUS" ? "globe-outline" : "document-text"}
                  size={16}
                  color={colors.brandPrimary}
                />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.rowLabel}>{f.name}</Text>
                <Text style={styles.rowValue}>
                  <Text style={tabularNums}>{f.properties_from_feed.toLocaleString()}</Text> properties from feed
                </Text>
              </View>
              <Pressable
                testID={`sync-${f.name.toLowerCase().replace(/\s/g, "-")}`}
                onPress={() => runSync(f.name)}
                disabled={syncing}
                style={styles.feedSyncBtn}
              >
                <Ionicons name="refresh" size={14} color={colors.brandPrimary} />
              </Pressable>
            </View>
          ))}
          {lastSync ? (
            <View style={styles.syncResult} testID="sync-result">
              <Text style={styles.syncResultText}>
                Last sync: <Text style={tabularNums}>{lastSync.totals.inserted}</Text> inserted ·{" "}
                <Text style={tabularNums}>{lastSync.totals.matched}</Text> matched
              </Text>
            </View>
          ) : null}
        </View>

        {/* Export */}
        <View style={styles.card}>
          <Text style={styles.section}>Export Deals</Text>
          <View style={styles.exportRow}>
            <Pressable testID="export-csv" onPress={() => openExport("csv")} style={styles.exportBtn}>
              <Ionicons name="document-text" size={16} color={colors.onBrandPrimary} />
              <Text style={styles.exportBtnText}>Export CSV</Text>
            </Pressable>
            <Pressable testID="export-xlsx" onPress={() => openExport("xlsx")} style={[styles.exportBtn, { backgroundColor: colors.brandSecondary }]}>
              <Ionicons name="grid" size={16} color="#fff" />
              <Text style={styles.exportBtnText}>Export Excel</Text>
            </Pressable>
          </View>
          <Text style={styles.exportNote}>
            Exports include owner signals, source-backed value fields, preliminary scores, and tax-roll data.
            Unknown equity and ROI stay blank until their required inputs exist.
          </Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.section}>Data Sources</Text>
          <Row icon="document-text" label="Tarrant County Tax Roll" value="Official county file · exact-address matches to Fort Worth listings" />
          <Row icon="hammer-outline" label="Foreclosure Finder API" value="RapidAPI connector · provider availability shown above" />
          <Row icon="globe-outline" label="Property Lookup API" value="Third-party RapidAPI lookup · not a direct Zillow connection" />
          <Row icon="receipt-outline" label="Listing & Tax History APIs" value="Realtor.com / NTREIS data through RapidAPI wrappers" />
        </View>

        <View style={styles.card}>
          <Text style={styles.section}>Owner Intelligence</Text>
          <Row icon="people-outline" label="Classification Model" value="9 owner types · LLC · Trust · Bank · Law Firm · etc." />
          <Row icon="scale-outline" label="Law Firm Detector" value="Suffix + keyword + known-firm list" />
        </View>

        <View style={styles.card}>
          <Text style={styles.section}>AI Deal Scoring</Text>
          <Row icon="sparkles-outline" label="Narrative Engine" value="Quill analysis with explicit missing-data warnings" />
          <Row icon="calculator-outline" label="Score Vectors" value="Preliminary screening · confidence and source labels included" />
        </View>

        <View style={[styles.card, { alignItems: "center" }]}>
          <Text style={{ color: colors.muted, fontSize: 12 }}>TarrantREI · v1.1</Text>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.md,
    borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  eyebrow: { fontSize: 10, color: colors.muted, fontWeight: "800", letterSpacing: 1 },
  title: { fontSize: 24, fontWeight: "800", color: colors.onSurface, marginTop: 2 },
  card: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.lg,
    borderWidth: 1, borderColor: colors.border,
    marginBottom: spacing.md,
  },
  cardHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: spacing.md },
  section: { fontSize: 11, fontWeight: "800", letterSpacing: 1.2, color: colors.muted },
  adminHelp: { color: colors.muted, fontSize: 11, lineHeight: 16, marginTop: 8, marginBottom: 10 },
  adminInput: { minHeight: 46, borderWidth: 1, borderColor: colors.borderStrong, borderRadius: radius.md, paddingHorizontal: 12, color: colors.onSurface, backgroundColor: colors.surface },
  adminButton: { marginTop: 9, minHeight: 42, borderRadius: radius.md, backgroundColor: colors.brandPrimary, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7 },
  adminButtonText: { color: "#fff", fontSize: 12, fontWeight: "800" },
  operationError: { color: colors.error, fontSize: 11, lineHeight: 16, marginTop: 8 },
  row: { flexDirection: "row", alignItems: "flex-start", marginBottom: spacing.md, gap: spacing.md },
  rowIcon: {
    width: 28, height: 28, borderRadius: 14,
    backgroundColor: colors.brandTertiary,
    alignItems: "center", justifyContent: "center",
  },
  rowLabel: { fontSize: 14, color: colors.onSurface, fontWeight: "700" },
  rowValue: { fontSize: 12, color: colors.muted, marginTop: 2 },

  syncBtn: {
    flexDirection: "row", alignItems: "center", gap: 6,
    backgroundColor: colors.brandPrimary,
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: radius.pill,
  },
  syncBtnText: { color: "#fff", fontSize: 11, fontWeight: "800", letterSpacing: 0.4 },

  feedRow: {
    flexDirection: "row", alignItems: "center", gap: spacing.md, marginBottom: spacing.md,
  },
  feedSyncBtn: {
    width: 32, height: 32, borderRadius: 16,
    borderWidth: 1, borderColor: colors.border,
    alignItems: "center", justifyContent: "center",
  },
  syncResult: {
    marginTop: 4, padding: 8,
    backgroundColor: "#E3EBE5", borderRadius: radius.sm,
  },
  syncResultText: { color: colors.success, fontSize: 12, fontWeight: "700", textAlign: "center" },

  exportRow: { flexDirection: "row", gap: 8 },
  exportBtn: {
    flex: 1, height: 44, borderRadius: radius.md,
    backgroundColor: colors.brandPrimary,
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
  },
  exportBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },
  exportNote: { fontSize: 11, color: colors.muted, marginTop: 10, lineHeight: 16 },
});
