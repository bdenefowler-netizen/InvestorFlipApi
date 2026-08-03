import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useLocalSearchParams } from "expo-router";

import { getCountyRecord, type CountyRecord } from "@/src/lib/api";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";


function valueText(value: unknown, money = false): string {
  if (value == null || value === "") return "—";
  if (money && Number.isFinite(Number(value))) return `$${Math.round(Number(value)).toLocaleString()}`;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return value.toLocaleString();
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function Field({ label, value, money }: { label: string; value: unknown; money?: boolean }) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <Text selectable style={[styles.value, tabularNums]}>{valueText(value, money)}</Text>
    </View>
  );
}

function RawSection({ title, data }: { title: string; data?: Record<string, unknown> }) {
  const rows = Object.entries(data || {}).filter(([, value]) => value != null && value !== "");
  if (!rows.length) return null;
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {rows.sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => (
        <Field key={key} label={key.replace(/_/g, " ")} value={value} />
      ))}
    </View>
  );
}

export default function CountyRecordDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [record, setRecord] = useState<CountyRecord | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    getCountyRecord(id).then(setRecord).catch((e) => setError(e?.message || "County record unavailable."));
  }, [id]);

  const sourceText = useMemo(() => record?.sources?.join(" + ") || "County record", [record]);

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <Stack.Screen options={{ title: "County record", headerBackTitle: "County" }} />
      {!record && !error ? (
        <View style={styles.center}><ActivityIndicator color={colors.brandPrimary} /></View>
      ) : error ? (
        <View style={styles.center}><Text style={styles.error}>{error}</Text></View>
      ) : record ? (
        <ScrollView contentContainerStyle={styles.content}>
          <View style={styles.hero}>
            <Text style={styles.eyebrow}>{sourceText.toUpperCase()}</Text>
            <Text style={styles.title}>{valueText(record.situs_address)}</Text>
            <Text style={styles.owner}>{valueText(record.owner_name)}</Text>
            <View style={styles.qualityRow}>
              <Text style={styles.quality}>{record.completeness_score ?? 0}% complete</Text>
              {record.tax_delinquent ? <Text style={styles.delinquent}>TAX DUE</Text> : null}
            </View>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Identity & owner</Text>
            <Field label="Account ID" value={record.account_id} />
            <Field label="Parcel ID" value={record.parcel_id} />
            <Field label="Owner" value={record.owner_name} />
            <Field label="Mailing address" value={record.owner_mailing_address} />
            <Field label="Absentee owner" value={record.absentee_owner} />
            <Field label="Out-of-state owner" value={record.out_of_state_owner} />
            <Field label="Trust owned" value={record.trust_owned} />
            <Field label="Company owned" value={record.company_owned} />
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Property</Text>
            <Field label="Beds" value={record.beds} />
            <Field label="Baths" value={record.baths} />
            <Field label="Living area" value={record.sqft ? `${record.sqft.toLocaleString()} sqft` : null} />
            <Field label="Year built" value={record.year_built} />
            <Field label="Lot size" value={record.lot_size_sqft ? `${record.lot_size_sqft.toLocaleString()} sqft` : null} />
            <Field label="Garage" value={record.garage_capacity} />
            <Field label="School district" value={record.school_district} />
            <Field label="Legal description" value={record.legal_description} />
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Values & taxes</Text>
            <Field label="TAD appraised value" value={record.appraised_value} money />
            <Field label="Market value" value={record.market_value} money />
            <Field label="Tax-roll market value" value={record.tax_roll_market_value} money />
            <Field label="Land value" value={record.land_value} money />
            <Field label="Improvement value" value={record.improvement_value} money />
            <Field label="Annual levy" value={record.annual_taxes} money />
            <Field label="Current amount due" value={record.current_tax_amount_due} money />
            <Field label="Prior amount due" value={record.prior_tax_amount_due} money />
            <Field label="Delinquency date" value={record.delinquency_date} />
            <Field label="Status codes" value={record.account_status_codes} />
            <Field label="Litigation flag" value={record.tad_litigation_flag} />
          </View>

          <RawSection title="All TAD source fields" data={record.tad_raw} />
          <RawSection title="All tax-roll source fields" data={record.tax_roll_raw} />
        </ScrollView>
      ) : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  content: { padding: spacing.lg, paddingBottom: spacing.xxxl, gap: spacing.md },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  error: { color: colors.error, fontWeight: "700" },
  hero: { backgroundColor: colors.brandPrimary, borderRadius: radius.lg, padding: spacing.lg },
  eyebrow: { color: "#C9D4CE", fontSize: 9, fontWeight: "800", letterSpacing: 1 },
  title: { color: colors.onBrandPrimary, fontSize: 22, lineHeight: 28, fontWeight: "800", marginTop: 7 },
  owner: { color: "#E2E9E5", fontSize: 13, marginTop: 6 },
  qualityRow: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: spacing.md },
  quality: { color: colors.onBrandPrimary, backgroundColor: "rgba(255,255,255,0.14)", paddingHorizontal: 9, paddingVertical: 5, borderRadius: radius.pill, fontSize: 10, fontWeight: "800" },
  delinquent: { color: "#FFFFFF", backgroundColor: colors.error, paddingHorizontal: 9, paddingVertical: 5, borderRadius: radius.pill, fontSize: 10, fontWeight: "900" },
  section: { backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, borderRadius: radius.lg, overflow: "hidden" },
  sectionTitle: { fontSize: 11, color: colors.muted, fontWeight: "900", letterSpacing: 0.8, textTransform: "uppercase", paddingHorizontal: spacing.lg, paddingTop: spacing.lg, paddingBottom: spacing.sm },
  field: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: spacing.md, paddingHorizontal: spacing.lg, paddingVertical: 11, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
  label: { flex: 0.42, color: colors.muted, fontSize: 12, textTransform: "capitalize" },
  value: { flex: 0.58, color: colors.onSurface, fontSize: 12, fontWeight: "600", textAlign: "right" },
});
