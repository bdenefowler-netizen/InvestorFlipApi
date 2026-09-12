import { useState } from "react";
import { ActivityIndicator, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as DocumentPicker from "expo-document-picker";
import * as Haptics from "expo-haptics";
import { useRouter } from "expo-router";

import { uploadCountyFile, type PublicUploadAsset, type PublicUploadResult } from "@/src/lib/publicUpload";
import { colors, radius, spacing } from "@/src/theme/tokens";

export default function AddScreen() {
  const router = useRouter();
  const [file, setFile] = useState<PublicUploadAsset | null>(null);
  const [result, setResult] = useState<PublicUploadResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const choose = async () => {
    setError(null);
    setResult(null);
    const picked = await DocumentPicker.getDocumentAsync({
      type: [
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/x-zip-compressed",
      ],
      copyToCacheDirectory: true,
      multiple: false,
    });
    if (picked.canceled || !picked.assets?.length) return;
    const asset = picked.assets[0];
    if ((asset.size || 0) > 300 * 1024 * 1024) {
      setError("That file is over 300 MB. Split it into smaller uploads.");
      return;
    }
    setFile({
      uri: asset.uri,
      name: asset.name,
      mimeType: asset.mimeType,
      size: asset.size,
      file: (asset as any).file,
    });
  };

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const next = await uploadCountyFile(file);
      setResult(next);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "Upload failed.");
    } finally {
      setBusy(false);
    }
  };

  const labels: Record<string, string> = {
    tad: "TAD",
    tax_roll: "Tax Roll",
    tax_due: "Tax Due",
    code_violations: "Code Violations",
    pre_foreclosure: "Pre-Foreclosure",
    probate: "Probate",
    uploaded: "Uploaded",
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.eyebrow}>ADD · COUNTY INTAKE</Text>
        <Text style={styles.title}>Upload Document</Text>
        <Text style={styles.subtitle}>
          Drop in county data. InvestorFlip sorts it, matches the property, then pulls County/TAD enrichment.
        </Text>
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.card}>
          <View style={styles.row}>
            <View style={styles.icon}>
              <Ionicons name="cloud-upload-outline" size={24} color={colors.brandPrimary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.cardTitle}>County File Upload</Text>
              <Text style={styles.cardText}>CSV · XLS · XLSX · ZIP · up to 300 MB</Text>
            </View>
          </View>

          <View style={styles.notice}>
            <Text style={styles.noticeStrong}>No admin key required.</Text>
            <Text style={styles.noticeText}>
              Auto-sorts into TAD · Tax Roll · Tax Due · Code Violations · Pre-Foreclosure · Probate.
              Probate uses the Decedent Property Address for its TAD lookup.
            </Text>
          </View>

          <Pressable disabled={busy} onPress={choose} style={[styles.secondary, busy && styles.disabled]}>
            <Ionicons name="folder-open-outline" size={18} color={colors.brandPrimary} />
            <Text style={styles.secondaryText}>Choose File</Text>
          </Pressable>

          {file ? (
            <View style={styles.fileBox}>
              <Ionicons name="document-attach-outline" size={20} color={colors.brandPrimary} />
              <View style={{ flex: 1 }}>
                <Text style={styles.fileName}>{file.name || "Selected file"}</Text>
                <Text style={styles.fileMeta}>
                  {file.size ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : "Ready to upload"}
                </Text>
              </View>
            </View>
          ) : null}

          <Pressable
            disabled={!file || busy}
            onPress={upload}
            style={[styles.primary, (!file || busy) && styles.disabled]}
          >
            {busy ? <ActivityIndicator color="#fff" /> : <Ionicons name="sparkles-outline" size={18} color="#fff" />}
            <Text style={styles.primaryText}>{busy ? "Uploading · Sorting · Enriching…" : "Upload & Process"}</Text>
          </Pressable>

          {result ? (
            <View style={styles.success}>
              <Text style={styles.successTitle}>✓ {result.filename}</Text>
              <Text style={styles.successText}>
                {result.accepted} accepted · {result.inserted} new · {result.updated} updated · {result.rejected} rejected
              </Text>
              {result.categories?.length ? (
                <Text style={styles.successText}>
                  Sorted to: {result.categories.map((x) => labels[x] || x).join(" · ")}
                </Text>
              ) : null}
              <Text style={styles.successText}>
                TAD lookups: {result.enrichment?.county?.tad_lookups ?? 0} · County enriched: {result.enrichment?.county?.enriched ?? 0}
              </Text>
              <Pressable style={styles.open} onPress={() => router.push("/county" as any)}>
                <Text style={styles.openText}>Open County Records</Text>
                <Ionicons name="arrow-forward" size={15} color="#fff" />
              </Pressable>
            </View>
          ) : null}

          {error ? <View style={styles.error}><Text style={styles.errorText}>{error}</Text></View> : null}
        </View>

        <View style={styles.help}>
          <Text style={styles.helpTitle}>What happens after upload?</Text>
          <Text style={styles.helpText}>
            1. Every supported sheet/file is read.{"\n"}
            2. The source is identified from its filename + headers.{"\n"}
            3. Address, TAD Account #, APN/Tax ID, Legal Description and Cause No. are preserved for matching.{"\n"}
            4. Matching records merge instead of creating parallel properties.{"\n"}
            5. Missing County/TAD data is pulled and attached to the property.
          </Text>
          <Text style={styles.platform}>File picker active on {Platform.OS}.</Text>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg, borderBottomWidth: 1, borderBottomColor: colors.border },
  eyebrow: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 1.2 },
  title: { color: colors.onSurface, fontSize: 27, fontWeight: "800", marginTop: 4 },
  subtitle: { color: colors.muted, fontSize: 12, lineHeight: 18, marginTop: 5 },
  content: { padding: spacing.lg, paddingBottom: 120 },
  card: { backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.brandPrimary, borderRadius: radius.lg, padding: spacing.lg, gap: 12 },
  row: { flexDirection: "row", gap: spacing.md, alignItems: "center" },
  icon: { width: 46, height: 46, borderRadius: 23, backgroundColor: colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  cardTitle: { color: colors.onSurface, fontSize: 18, fontWeight: "800" },
  cardText: { color: colors.muted, fontSize: 12, marginTop: 3 },
  notice: { backgroundColor: colors.surface, borderRadius: radius.md, padding: 12, borderWidth: 1, borderColor: colors.border },
  noticeStrong: { color: colors.brandPrimary, fontSize: 12, fontWeight: "800" },
  noticeText: { color: colors.onSurfaceTertiary, fontSize: 11, lineHeight: 17, marginTop: 4 },
  secondary: { minHeight: 48, borderRadius: radius.md, borderWidth: 1, borderColor: colors.brandPrimary, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8 },
  secondaryText: { color: colors.brandPrimary, fontSize: 13, fontWeight: "800" },
  primary: { minHeight: 50, borderRadius: radius.md, backgroundColor: colors.brandPrimary, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8 },
  primaryText: { color: "#fff", fontSize: 13, fontWeight: "800" },
  disabled: { opacity: 0.5 },
  fileBox: { flexDirection: "row", alignItems: "center", gap: 9, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 11 },
  fileName: { color: colors.onSurface, fontSize: 12, fontWeight: "800" },
  fileMeta: { color: colors.muted, fontSize: 10, marginTop: 2 },
  success: { backgroundColor: "#E8F0EB", borderWidth: 1, borderColor: "#C9DACE", borderRadius: radius.md, padding: spacing.md },
  successTitle: { color: colors.onSurface, fontSize: 13, fontWeight: "800" },
  successText: { color: colors.muted, fontSize: 11, lineHeight: 17, marginTop: 3 },
  open: { alignSelf: "flex-start", flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: colors.brandPrimary, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 8, marginTop: 10 },
  openText: { color: "#fff", fontSize: 11, fontWeigight: "800" },
  error: { backgroundColor: "#F8E9E7", borderWidth: 1, borderColor: "#ECC7C3", borderRadius: radius.md, padding: spacing.md },
  errorText: { color: colors.error, fontSize: 12, fontWeight: "700" },
  help: { marginTop: spacing.md, padding: spacing.md },
  helpTitle: { color: colors.onSurface, fontSize: 13, fontWeight: "800" },
  helpText: { color: colors.muted, fontSize: 11, lineHeight: 18, marginTop: 7 },
  platform: { color: colors.muted, fontSize: 10, marginTop: 8 },
});
