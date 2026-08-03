import { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as DocumentPicker from "expo-document-picker";
import * as Haptics from "expo-haptics";
import { useRouter } from "expo-router";

import {
  addPropertyLink,
  syncAllListingSources,
  uploadPropertyFile,
  type AllSourceSyncResult,
  type LinkIntakeResult,
  type UploadIntakeResult,
} from "@/src/lib/api";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";


type Busy = "sync" | "upload" | "link" | null;

function ResultBox({ children, error = false }: { children: React.ReactNode; error?: boolean }) {
  return <View style={[styles.result, error && styles.resultError]}>{children}</View>;
}

export default function AddScreen() {
  const router = useRouter();
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<UploadIntakeResult | null>(null);
  const [linkResult, setLinkResult] = useState<LinkIntakeResult | null>(null);
  const [syncResult, setSyncResult] = useState<AllSourceSyncResult | null>(null);

  const begin = (kind: Busy) => {
    setBusy(kind);
    setError(null);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
  };

  const pickFile = async () => {
    const picked = await DocumentPicker.getDocumentAsync({
      type: [
        "text/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      ],
      copyToCacheDirectory: true,
      multiple: false,
    });
    if (picked.canceled || !picked.assets[0]) return;
    begin("upload");
    setUploadResult(null);
    try {
      const result = await uploadPropertyFile(picked.assets[0]);
      setUploadResult(result);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "The spreadsheet could not be imported.");
    } finally {
      setBusy(null);
    }
  };

  const addLink = async () => {
    if (!link.trim()) {
      setError("Paste a property-page link first.");
      return;
    }
    begin("link");
    setLinkResult(null);
    try {
      const result = await addPropertyLink(link.trim());
      setLinkResult(result);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "That property link could not be imported.");
    } finally {
      setBusy(null);
    }
  };

  const syncAll = async () => {
    begin("sync");
    setSyncResult(null);
    try {
      const result = await syncAllListingSources(50);
      setSyncResult(result);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "The source sync could not finish.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.eyebrow}>ADD · IMPORT · ENRICH</Text>
        <Text style={styles.title}>Bring in a Deal</Text>
        <Text style={styles.subtitle}>Every accepted property is matched to TAD and the tax roll, then sent through the configured detail APIs.</Text>
      </View>

      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <View style={styles.card}>
          <View style={styles.cardTitleRow}>
            <View style={styles.icon}><Ionicons name="cloud-download-outline" size={19} color={colors.brandPrimary} /></View>
            <View style={styles.cardHeading}>
              <Text style={styles.cardTitle}>Pull every source</Text>
              <Text style={styles.cardText}>Runs all configured listing APIs and scrapers, merges duplicate houses, then enriches the merged records.</Text>
            </View>
          </View>
          <Pressable disabled={busy !== null} onPress={syncAll} style={[styles.primaryButton, busy && styles.disabled]}>
            {busy === "sync" ? <ActivityIndicator color="#fff" /> : <Ionicons name="refresh" size={18} color="#fff" />}
            <Text style={styles.primaryButtonText}>{busy === "sync" ? "Pulling and enriching…" : "Pull All Sources Now"}</Text>
          </Pressable>
          {syncResult ? (
            <ResultBox>
              <Text style={styles.resultTitle}>{syncResult.total_properties_touched} properties pulled or updated</Text>
              <Text style={styles.resultText}>
                County matched {syncResult.county_enrichment.enriched} · Detail API found {syncResult.detail_enrichment.found}/{syncResult.detail_enrichment.attempted}
              </Text>
              {syncResult.providers.map((provider) => (
                <View key={provider.provider} style={styles.providerRow}>
                  <View style={[styles.dot, provider.status === "success" ? styles.dotGood : styles.dotMuted]} />
                  <Text style={styles.providerName}>{provider.provider}</Text>
                  <Text style={styles.providerCount}>{provider.accepted ?? provider.fetched ?? 0}</Text>
                </View>
              ))}
            </ResultBox>
          ) : null}
        </View>

        <View style={styles.card}>
          <View style={styles.cardTitleRow}>
            <View style={styles.icon}><Ionicons name="document-attach-outline" size={19} color={colors.brandPrimary} /></View>
            <View style={styles.cardHeading}>
              <Text style={styles.cardTitle}>Upload a spreadsheet</Text>
              <Text style={styles.cardText}>CSV or Excel, up to 250 rows. An Address column is required; every other column is preserved.</Text>
            </View>
          </View>
          <Pressable disabled={busy !== null} onPress={pickFile} style={[styles.secondaryButton, busy && styles.disabled]}>
            {busy === "upload" ? <ActivityIndicator color={colors.brandPrimary} /> : <Ionicons name="folder-open-outline" size={18} color={colors.brandPrimary} />}
            <Text style={styles.secondaryButtonText}>{busy === "upload" ? "Importing and enriching…" : "Choose CSV or Excel"}</Text>
          </Pressable>
          {uploadResult ? (
            <ResultBox>
              <Text style={styles.resultTitle}>{uploadResult.filename}</Text>
              <Text style={[styles.resultMetric, tabularNums]}>{uploadResult.accepted} accepted · {uploadResult.rejected} rejected</Text>
              <Text style={styles.resultText}>
                {uploadResult.inserted} new · {uploadResult.updated} updated · {uploadResult.duplicates_merged} duplicates merged
              </Text>
              <Text style={styles.resultText}>
                County matched {uploadResult.enrichment.county.enriched} · Details found {uploadResult.enrichment.details.found}/{uploadResult.enrichment.details.attempted}
              </Text>
              {uploadResult.property_ids.length === 1 ? (
                <Pressable onPress={() => router.push(`/property/${uploadResult.property_ids[0]}`)} style={styles.openButton}>
                  <Text style={styles.openButtonText}>Open Property</Text><Ionicons name="arrow-forward" size={15} color="#fff" />
                </Pressable>
              ) : null}
            </ResultBox>
          ) : null}
        </View>

        <View style={styles.card}>
          <View style={styles.cardTitleRow}>
            <View style={styles.icon}><Ionicons name="link-outline" size={19} color={colors.brandPrimary} /></View>
            <View style={styles.cardHeading}>
              <Text style={styles.cardTitle}>Paste a house link</Text>
              <Text style={styles.cardText}>Works with property pages from Zillow, Realtor, Redfin, Auction.com, Xome, Trulia, Homes.com, and HAR.</Text>
            </View>
          </View>
          <TextInput
            value={link}
            onChangeText={setLink}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            placeholder="https://www.zillow.com/homedetails/…"
            placeholderTextColor={colors.muted}
            style={styles.linkInput}
          />
          <Pressable disabled={busy !== null} onPress={addLink} style={[styles.primaryButton, busy && styles.disabled]}>
            {busy === "link" ? <ActivityIndicator color="#fff" /> : <Ionicons name="sparkles-outline" size={18} color="#fff" />}
            <Text style={styles.primaryButtonText}>{busy === "link" ? "Pulling and enriching…" : "Add & Enrich Property"}</Text>
          </Pressable>
          {linkResult ? (
            <ResultBox>
              <Text style={styles.resultTitle}>Property added</Text>
              <Text style={styles.resultMetric}>{linkResult.property.situs_address}</Text>
              <Text style={styles.resultText}>
                County matched {linkResult.enrichment.county.enriched} · Detail API found {linkResult.enrichment.details.found}/{linkResult.enrichment.details.attempted}
              </Text>
              <Pressable onPress={() => router.push(`/property/${linkResult.property_id}`)} style={styles.openButton}>
                <Text style={styles.openButtonText}>Open Enriched Property</Text><Ionicons name="arrow-forward" size={15} color="#fff" />
              </Pressable>
            </ResultBox>
          ) : null}
        </View>

        {error ? <ResultBox error><Text style={styles.errorText}>{error}</Text></ResultBox> : null}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg, borderBottomWidth: 1, borderBottomColor: colors.border },
  eyebrow: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 1.2 },
  title: { color: colors.onSurface, fontSize: 26, fontWeight: "800", marginTop: 3 },
  subtitle: { color: colors.muted, fontSize: 12, lineHeight: 17, marginTop: 5, maxWidth: 520 },
  content: { padding: spacing.lg, paddingBottom: spacing.xxxl },
  card: { backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, borderRadius: radius.lg, padding: spacing.lg, marginBottom: spacing.md },
  cardTitleRow: { flexDirection: "row", gap: spacing.md, alignItems: "flex-start", marginBottom: spacing.md },
  icon: { width: 38, height: 38, borderRadius: 19, backgroundColor: colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  cardHeading: { flex: 1 },
  cardTitle: { color: colors.onSurface, fontSize: 17, fontWeight: "800" },
  cardText: { color: colors.muted, fontSize: 12, lineHeight: 17, marginTop: 3 },
  primaryButton: { minHeight: 48, borderRadius: radius.md, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center", flexDirection: "row", gap: 8, paddingHorizontal: spacing.md },
  primaryButtonText: { color: colors.onBrandPrimary, fontSize: 13, fontWeight: "800" },
  secondaryButton: { minHeight: 48, borderRadius: radius.md, borderWidth: 1, borderColor: colors.brandPrimary, alignItems: "center", justifyContent: "center", flexDirection: "row", gap: 8, paddingHorizontal: spacing.md },
  secondaryButtonText: { color: colors.brandPrimary, fontSize: 13, fontWeight: "800" },
  disabled: { opacity: 0.55 },
  linkInput: { minHeight: 50, borderWidth: 1, borderColor: colors.borderStrong, borderRadius: radius.md, paddingHorizontal: spacing.md, color: colors.onSurface, backgroundColor: colors.surface, marginBottom: spacing.sm, fontSize: 13 },
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
  openButton: { alignSelf: "flex-start", marginTop: spacing.md, flexDirection: "row", gap: 6, backgroundColor: colors.brandPrimary, borderRadius: radius.pill, paddingVertical: 8, paddingHorizontal: 12 },
  openButtonText: { color: "#fff", fontWeight: "800", fontSize: 11 },
  errorText: { color: colors.error, fontSize: 12, fontWeight: "700", lineHeight: 17 },
});
