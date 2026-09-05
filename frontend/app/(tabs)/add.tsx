import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useRouter } from "expo-router";

import {
  addPropertyLink,
  pastePropertyCsv,
  syncAllListingSources,
  type AllSourceSyncResult,
  type LinkIntakeResult,
  type PasteIntakeResult,
  type UploadIntakeResult,
} from "@/src/lib/api";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";
import { adminRequestHeaders } from "@/src/lib/admin";

type Busy = "sync" | "link" | "paste" | "url" | null;

function ResultBox({ children, error = false }: { children: React.ReactNode; error?: boolean }) {
  return <View style={[styles.result, error && styles.resultError]}>{children}</View>;
}

function FileTypePill({ label, icon }: { label: string; icon: string }) {
  return (
    <View style={styles.pill}>
      <Ionicons name={icon as any} size={11} color={colors.brandPrimary} />
      <Text style={styles.pillText}>{label}</Text>
    </View>
  );
}

export default function AddScreen() {
  const router = useRouter();

  // Pull all sources
  const [syncBusy, setSyncBusy] = useState(false);
  const [syncResult, setSyncResult] = useState<AllSourceSyncResult | null>(null);

  // Paste CSV
  const [pasteText, setPasteText] = useState("");
  const [pasteBusy, setPasteBusy] = useState(false);
  const [pasteResult, setPasteResult] = useState<PasteIntakeResult | null>(null);

  // URL import — new feature
  const [urlInput, setUrlInput] = useState("");
  const [urlBusy, setUrlBusy] = useState(false);
  const [urlResult, setUrlResult] = useState<{ ok: boolean; message: string; files?: any[] } | null>(null);

  // Link add
  const [link, setLink] = useState("");
  const [linkBusy, setLinkBusy] = useState(false);
  const [linkResult, setLinkResult] = useState<LinkIntakeResult | null>(null);

  const [error, setError] = useState<string | null>(null);

  const begin = (kind: Busy) => {
    setError(null);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    return kind;
  };

  // ── Pull all sources ───────────────────────────────────
  const syncAll = async () => {
    setSyncBusy(true);
    setSyncResult(null);
    try {
      const result = await syncAllListingSources(50);
      setSyncResult(result);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "The source sync could not finish.");
    } finally {
      setSyncBusy(false);
    }
  };

  // ── Import pasted CSV rows ─────────────────────────────
  const importPastedRows = async () => {
    if (!pasteText.trim()) { setError("Paste CSV rows with a header first."); return; }
    setPasteBusy(true);
    setPasteResult(null);
    try {
      const result = await pastePropertyCsv(pasteText.trim());
      setPasteResult(result);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "Those rows could not be imported.");
    } finally {
      setPasteBusy(false);
    }
  };

  // ── Import via URL (CSV / XLSX / ZIP) ─────────────────
  const importFromUrl = async () => {
    const url = urlInput.trim();
    if (!url) { setError("Paste a URL to your file first."); return; }
    // Accept: .csv, .xls, .xlsx, .zip, Google Sheets share links, or any URL with csv/xlsx in path
    const isHostedFile = /\.(csv|xls|xlsx|zip)(\?|$|#)/i.test(url) ||
                         /docs\.google\.com/i.test(url) ||
                         /drive\.google\.com/i.test(url) ||
                         /dropbox\.com/i.test(url) ||
                         /onedrive/i.test(url) ||
                         /sharepoint/i.test(url);
    if (!isHostedFile) {
      setError("URL must be a hosted CSV, Excel, or ZIP file.\nFor Google Sheets, use the share link.");
      return;
    }
    setUrlBusy(true);
    setUrlResult(null);
    try {
      const headers = await adminRequestHeaders({ "Content-Type": "application/json" });
      const res = await fetch(`${API_BASE}/api/import/url`, {
        method: "POST",
        headers,
        body: JSON.stringify({ url, source_label: `URL: ${url.split("/").pop() || url}` }),
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(result.detail || `Import failed (${res.status})`);
      setUrlResult({
        ok: result.ok,
        message: result.ok
          ? `Imported ${result.total_accepted} properties (${result.total_inserted} new, ${result.total_updated} updated)`
          : "Import completed with issues",
        files: result.files,
      });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "Could not import from that URL.");
    } finally {
      setUrlBusy(false);
    }
  };

  // ── Add property link ──────────────────────────────────
  const addLink = async () => {
    if (!link.trim()) { setError("Paste a property-page link first."); return; }
    setLinkBusy(true);
    setLinkResult(null);
    try {
      const result = await addPropertyLink(link.trim());
      setLinkResult(result);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "That property link could not be imported.");
    } finally {
      setLinkBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
      >
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.eyebrow}>ADD · IMPORT · ENRICH</Text>
          <Text style={styles.title}>Bring in a Deal</Text>
          <Text style={styles.subtitle}>
            Run all sources, paste a CSV, import from a URL, or add a single property link.
          </Text>
        </View>

        <ScrollView
          contentContainerStyle={styles.content}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {/* ── Pull All Sources ── */}
          <View style={styles.card}>
            <View style={styles.cardTitleRow}>
              <View style={styles.icon}><Ionicons name="refresh" size={19} color={colors.brandPrimary} /></View>
              <View style={styles.cardHeading}>
                <Text style={styles.cardTitle}>Pull every source</Text>
                <Text style={styles.cardText}>
                  Runs all configured listing APIs, merges duplicates, then enriches every record.
                </Text>
              </View>
            </View>
            <Pressable
              disabled={syncBusy}
              onPress={syncAll}
              style={[styles.primaryButton, syncBusy && styles.disabled]}
            >
              {syncBusy
                ? <ActivityIndicator color="#fff" size="small" />
                : <Ionicons name="refresh" size={16} color="#fff" />}
              <Text style={styles.primaryButtonText}>
                {syncBusy ? "Pulling and enriching…" : "Pull All Sources Now"}
              </Text>
            </Pressable>
            {syncResult ? (
              <ResultBox>
                <Text style={styles.resultTitle}>
                  {syncResult.total_properties_touched} records processed
                </Text>
                <Text style={styles.resultText}>
                  County matched {syncResult.county_enrichment.enriched} · Detail API found{" "}
                  {syncResult.detail_enrichment.found}/{syncResult.detail_enrichment.attempted}
                </Text>
                {syncResult.providers.map((p) => (
                  <View key={p.provider} style={styles.providerRow}>
                    <View style={[styles.dot, p.status === "success" ? styles.dotGood : styles.dotMuted]} />
                    <Text style={styles.providerName}>{p.provider}</Text>
                    <Text style={styles.providerCount}>{p.accepted ?? p.fetched ?? 0}</Text>
                  </View>
                ))}
              </ResultBox>
            ) : null}
          </View>

          {/* ── Import from URL (CSV / XLSX / ZIP) — NEW ── */}
          <View style={styles.card}>
            <View style={styles.cardTitleRow}>
              <View style={[styles.icon, { backgroundColor: "#E8F0EB" }]}>
                <Ionicons name="cloud-download-outline" size={19} color="#355C44" />
              </View>
              <View style={styles.cardHeading}>
                <Text style={styles.cardTitle}>Import from URL</Text>
                <Text style={styles.cardText}>
                  Paste a direct link to a .csv, .xlsx, or .zip file — any public URL works.
                </Text>
              </View>
            </View>
            {/* File type pills */}
            <View style={styles.pillRow}>
              <FileTypePill label="CSV" icon="document-text-outline" />
              <FileTypePill label="Excel" icon="grid-outline" />
              <FileTypePill label="ZIP (multiple files)" icon="folder-outline" />
            </View>
            <TextInput
              value={urlInput}
              onChangeText={(v) => { setUrlInput(v); setUrlResult(null); }}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              placeholder="https://example.com/leads.csv"
              placeholderTextColor={colors.muted}
              style={styles.urlInput}
            />
            <View style={styles.buttonRow}>
              <Pressable
                disabled={urlBusy || syncBusy || pasteBusy || linkBusy}
                onPress={importFromUrl}
                style={[styles.primaryButton, (urlBusy || syncBusy || pasteBusy || linkBusy) && styles.disabled]}
              >
                {urlBusy
                  ? <ActivityIndicator color="#fff" size="small" />
                  : <Ionicons name="cloud-download-outline" size={16} color="#fff" />}
                <Text style={styles.primaryButtonText}>
                  {urlBusy ? "Importing…" : "Import from URL"}
                </Text>
              </Pressable>
            </View>
            {urlResult ? (
              <ResultBox>
                <Text style={styles.resultTitle}>
                  {urlResult.ok ? "✅ Import successful!" : "⚠️ Import completed"}
                </Text>
                <Text style={styles.resultText}>{urlResult.message}</Text>
                {urlResult.files ? (
                  <View style={{ marginTop: 6 }}>
                    {urlResult.files.map((f: any, i: number) => (
                      <View key={i} style={styles.fileRow}>
                        <Ionicons
                          name={f.status === "ok" ? "checkmark-circle" : "alert-circle"}
                          size={14}
                          color={f.status === "ok" ? colors.success : colors.error}
                        />
                        <Text style={styles.fileName}>{f.file}</Text>
                        {f.accepted != null && (
                          <Text style={styles.fileCount}>{f.accepted} rows</Text>
                        )}
                        {f.reason && <Text style={[styles.fileCount, { color: colors.error }]}>{f.reason}</Text>}
                      </View>
                    ))}
                  </View>
                ) : null}
              </ResultBox>
            ) : null}
          </View>

          {/* ── Paste CSV Rows ── */}
          <View style={styles.card}>
            <View style={styles.cardTitleRow}>
              <View style={styles.icon}><Ionicons name="document-attach-outline" size={19} color={colors.brandPrimary} /></View>
              <View style={styles.cardHeading}>
                <Text style={styles.cardTitle}>Paste CSV rows</Text>
                <Text style={styles.cardText}>
                  Copy rows from any spreadsheet or data export and paste them here.
                </Text>
              </View>
            </View>
            <TextInput
              value={pasteText}
              onChangeText={(v) => { setPasteText(v); setPasteResult(null); }}
              autoCapitalize="none"
              autoCorrect={false}
              multiline
              placeholder={"address,city,state,zip,price,description\n123 Main St,Fort Worth,TX,76102,175000,needs TLC"}
              placeholderTextColor={colors.muted}
              style={styles.pasteInput}
              textAlignVertical="top"
            />
            <Pressable
              disabled={pasteBusy || syncBusy || urlBusy || linkBusy}
              onPress={importPastedRows}
              style={[styles.secondaryButton, (pasteBusy || syncBusy || urlBusy || linkBusy) && styles.disabled]}
            >
              {pasteBusy
                ? <ActivityIndicator color={colors.brandPrimary} size="small" />
                : <Ionicons name="cloud-upload-outline" size={16} color={colors.brandPrimary} />}
              <Text style={styles.secondaryButtonText}>
                {pasteBusy ? "Importing and enriching…" : "Import Pasted Rows"}
              </Text>
            </Pressable>
            {pasteResult ? (
              <ResultBox>
                <Text style={styles.resultTitle}>
                  {pasteResult.accepted} imported · {pasteResult.rejected} rejected
                </Text>
                <Text style={styles.resultText}>
                  Inserted {pasteResult.inserted} · Updated {pasteResult.updated} · Duplicates merged{" "}
                  {pasteResult.duplicates_merged}
                </Text>
              </ResultBox>
            ) : null}
          </View>

          {/* ── Paste Property Link ── */}
          <View style={styles.card}>
            <View style={styles.cardTitleRow}>
              <View style={styles.icon}><Ionicons name="link-outline" size={19} color={colors.brandPrimary} /></View>
              <View style={styles.cardHeading}>
                <Text style={styles.cardTitle}>Paste a property link</Text>
                <Text style={styles.cardText}>
                  Works with Zillow, Realtor, Redfin, Auction.com, Xome, Trulia, Homes.com, and HAR.
                </Text>
              </View>
            </View>
            <TextInput
              value={link}
              onChangeText={(v) => { setLink(v); setLinkResult(null); }}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              placeholder="https://www.zillow.com/homedetails/…"
              placeholderTextColor={colors.muted}
              style={styles.linkInput}
            />
            <Pressable
              disabled={linkBusy || syncBusy || urlBusy || pasteBusy}
              onPress={addLink}
              style={[styles.primaryButton, (linkBusy || syncBusy || urlBusy || pasteBusy) && styles.disabled]}
            >
              {linkBusy
                ? <ActivityIndicator color="#fff" size="small" />
                : <Ionicons name="sparkles-outline" size={16} color="#fff" />}
              <Text style={styles.primaryButtonText}>
                {linkBusy ? "Pulling and enriching…" : "Add & Enrich Property"}
              </Text>
            </Pressable>
            {linkResult ? (
              <ResultBox>
                <Text style={styles.resultTitle}>Property added</Text>
                <Text style={styles.resultMetric}>{linkResult.property.situs_address}</Text>
                <Text style={styles.resultText}>
                  County matched {linkResult.enrichment.county.enriched} · Detail API found{" "}
                  {linkResult.enrichment.details.found}/{linkResult.enrichment.details.attempted}
                </Text>
                <Pressable
                  onPress={() => router.push(`/property/${linkResult.property_id}`)}
                  style={styles.openButton}
                >
                  <Text style={styles.openButtonText}>Open Enriched Property</Text>
                  <Ionicons name="arrow-forward" size={14} color="#fff" />
                </Pressable>
              </ResultBox>
            ) : null}
          </View>

          {error ? (
            <ResultBox error>
              <Text style={styles.errorText}>{error}</Text>
            </ResultBox>
          ) : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg,
    borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  eyebrow: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 1.2 },
  title: { color: colors.onSurface, fontSize: 26, fontWeight: "800", marginTop: 3 },
  subtitle: { color: colors.muted, fontSize: 12, lineHeight: 17, marginTop: 5, maxWidth: 520 },
  content: { padding: spacing.lg, paddingBottom: spacing.xxxl },

  // Cards
  card: {
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1, borderColor: colors.border,
    borderRadius: radius.lg, padding: spacing.lg, marginBottom: spacing.md,
  },
  cardTitleRow: { flexDirection: "row", gap: spacing.md, alignItems: "flex-start", marginBottom: spacing.md },
  icon: { width: 38, height: 38, borderRadius: 19, backgroundColor: colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  cardHeading: { flex: 1 },
  cardTitle: { color: colors.onSurface, fontSize: 17, fontWeight: "800" },
  cardText: { color: colors.muted, fontSize: 12, lineHeight: 17, marginTop: 3 },

  // Pills (file type indicators)
  pillRow: { flexDirection: "row", gap: 6, marginBottom: 10 },
  pill: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 8, paddingVertical: 3,
    borderRadius: radius.pill,
    backgroundColor: "#E8F0EB",
    borderWidth: 1, borderColor: "#C9DACE",
  },
  pillText: { fontSize: 10, fontWeight: "700", color: "#355C44" },

  // URL input
  urlInput: {
    minHeight: 50, borderWidth: 1, borderColor: colors.borderStrong,
    borderRadius: radius.md, paddingHorizontal: spacing.md,
    color: colors.onSurface, backgroundColor: colors.surface,
    marginBottom: spacing.sm, fontSize: 13,
  },

  // Buttons
  buttonRow: { flexDirection: "row", gap: spacing.sm },
  primaryButton: {
    minHeight: 48, borderRadius: radius.md,
    backgroundColor: colors.brandPrimary,
    alignItems: "center", justifyContent: "center",
    flexDirection: "row", gap: 8, paddingHorizontal: spacing.md,
  },
  primaryButtonText: { color: "#fff", fontSize: 13, fontWeight: "800" },
  secondaryButton: {
    minHeight: 48, borderRadius: radius.md, borderWidth: 1, borderColor: colors.brandPrimary,
    alignItems: "center", justifyContent: "center",
    flexDirection: "row", gap: 8, paddingHorizontal: spacing.md,
  },
  secondaryButtonText: { color: colors.brandPrimary, fontSize: 13, fontWeight: "800" },
  disabled: { opacity: 0.55 },
  linkInput: {
    minHeight: 50, borderWidth: 1, borderColor: colors.borderStrong,
    borderRadius: radius.md, paddingHorizontal: spacing.md,
    color: colors.onSurface, backgroundColor: colors.surface,
    marginBottom: spacing.sm, fontSize: 13,
  },
  pasteInput: {
    minHeight: 120, borderWidth: 1, borderColor: colors.borderStrong,
    borderRadius: radius.md, padding: spacing.md,
    color: colors.onSurface, backgroundColor: colors.surface,
    marginBottom: spacing.sm, fontSize: 12, lineHeight: 17,
  },

  // Result boxes
  result: { marginTop: spacing.md, padding: spacing.md, borderRadius: radius.md, backgroundColor: "#E8F0EB", borderWidth: 1, borderColor: "#C9DACE" },
  resultError: { backgroundColor: "#F8E9E7", borderColor: "#ECC7C3" },
  resultTitle: { color: colors.onSurface, fontSize: 13, fontWeight: "800" },
  resultMetric: { color: colors.brandPrimary, fontSize: 13, fontWeight: "700", marginTop: 4 },
  resultText: { color: colors.muted, fontSize: 11, lineHeight: 16, marginTop: 3 },
  providerRow: { flexDirection: "row", alignItems: "center", gap: 7, marginTop: 7 },
  providerName: { flex: 1, color: colors.onSurface, fontSize: 11 },
  providerCount: { color: colors.muted, fontSize: 11, fontWeight: "700", ...tabularNums },
  dot: { width: 7, height: 7, borderRadius: 4 },
  dotGood: { backgroundColor: colors.success },
  dotMuted: { backgroundColor: colors.warning },

  // File import result rows
  fileRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 4 },
  fileName: { flex: 1, fontSize: 11, color: colors.onSurface },
  fileCount: { fontSize: 10, color: colors.muted, fontWeight: "700" },

  openButton: {
    alignSelf: "flex-start", marginTop: spacing.md,
    flexDirection: "row", gap: 6,
    backgroundColor: colors.brandPrimary,
    borderRadius: radius.pill,
    paddingVertical: 8, paddingHorizontal: 12,
  },
  openButtonText: { color: "#fff", fontWeight: "800", fontSize: 11 },
  errorText: { color: colors.error, fontSize: 12, fontWeight: "700", lineHeight: 17 },
});
