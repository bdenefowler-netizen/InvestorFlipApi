import { View, Text, StyleSheet, ScrollView } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { colors, radius, spacing } from "@/src/theme/tokens";

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
  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.eyebrow}>ACCOUNT</Text>
        <Text style={styles.title}>Settings</Text>
      </View>
      <ScrollView contentContainerStyle={{ padding: spacing.lg }}>
        <View style={styles.card}>
          <Text style={styles.section}>Data Sources</Text>
          <Row icon="document-text" label="Tarrant County Tax Roll" value="Master.dat + Rec.DAT · seeded sample" />
          <Row icon="globe-outline" label="RealtyInUS API" value="Configured (offline preview)" />
          <Row icon="business-outline" label="Texas Foreclosure Feeds" value="Connected" />
          <Row icon="stats-chart-outline" label="USAspending API" value="Connected" />
        </View>

        <View style={styles.card}>
          <Text style={styles.section}>Owner Intelligence</Text>
          <Row icon="people-outline" label="Classification Model" value="Individual · LLC · Corp · Trust · Bank · Gov · Nonprofit · Law Firm · Attorney" />
          <Row icon="scale-outline" label="Law Firm Detector" value="Suffix + keyword + known-firm list" />
        </View>

        <View style={styles.card}>
          <Text style={styles.section}>AI Deal Scoring</Text>
          <Row icon="sparkles-outline" label="Model" value="Claude Sonnet 4.6 (Emergent Universal Key)" />
          <Row icon="calculator-outline" label="Scoring Engine" value="Investment · Wholesale · Flip · Rental · Risk" />
        </View>

        <View style={[styles.card, { alignItems: "center" }]}>
          <Text style={{ color: colors.muted, fontSize: 12 }}>TarrantREI · v1.0</Text>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
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
  section: { fontSize: 11, fontWeight: "800", letterSpacing: 1.2, color: colors.muted, marginBottom: spacing.md },
  row: { flexDirection: "row", alignItems: "flex-start", marginBottom: spacing.md, gap: spacing.md },
  rowIcon: {
    width: 28, height: 28, borderRadius: 14,
    backgroundColor: colors.brandTertiary,
    alignItems: "center", justifyContent: "center",
  },
  rowLabel: { fontSize: 14, color: colors.onSurface, fontWeight: "700" },
  rowValue: { fontSize: 12, color: colors.muted, marginTop: 2 },
});
