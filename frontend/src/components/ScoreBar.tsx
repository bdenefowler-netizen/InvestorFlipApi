import { View, Text, StyleSheet } from "react-native";
import { colors, radius, spacing, tabularNums } from "../theme/tokens";

export function ScoreBar({
  label,
  value,
  tone = "primary",
  testID,
}: {
  label: string;
  value: number; // 1-99
  tone?: "primary" | "success" | "warning" | "error";
  testID?: string;
}) {
  const pct = Math.max(0, Math.min(100, value));
  const fill =
    tone === "success" ? colors.success :
    tone === "warning" ? colors.warning :
    tone === "error" ? colors.error :
    colors.brandPrimary;
  return (
    <View style={styles.wrap} testID={testID}>
      <View style={styles.row}>
        <Text style={styles.label}>{label}</Text>
        <Text style={[styles.value, tabularNums]}>{value}</Text>
      </View>
      <View style={styles.track}>
        <View style={[styles.fill, { width: `${pct}%`, backgroundColor: fill }]} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { marginBottom: spacing.md },
  row: { flexDirection: "row", justifyContent: "space-between", marginBottom: 4 },
  label: { color: colors.onSurfaceTertiary, fontSize: 12, fontWeight: "600", letterSpacing: 0.2 },
  value: { color: colors.onSurface, fontSize: 14, fontWeight: "800" },
  track: { height: 6, backgroundColor: colors.surfaceTertiary, borderRadius: radius.pill, overflow: "hidden" },
  fill: { height: "100%", borderRadius: radius.pill },
});
