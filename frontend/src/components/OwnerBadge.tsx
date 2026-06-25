import { View, Text, StyleSheet } from "react-native";
import { colors, radius, spacing } from "../theme/tokens";

const TYPE_COLORS: Record<string, { bg: string; fg: string }> = {
  LLC: { bg: "#E4DCD3", fg: "#5A3E1E" },
  Corporation: { bg: "#E4DCD3", fg: "#5A3E1E" },
  Trust: { bg: "#E2DDE9", fg: "#3F2E5E" },
  Bank: { bg: "#F1D9D5", fg: "#7A2A24" },
  Government: { bg: "#D6DDE0", fg: "#1E2D38" },
  Nonprofit: { bg: "#D9E7DC", fg: "#1F4329" },
  "Law Firm": { bg: "#F2E0BD", fg: "#5A3F0E" },
  Attorney: { bg: "#F2E0BD", fg: "#5A3F0E" },
  Individual: { bg: colors.brandTertiary, fg: colors.onBrandTertiary },
};

export function OwnerBadge({ type, compact = false, testID }: { type: string; compact?: boolean; testID?: string }) {
  const c = TYPE_COLORS[type] || TYPE_COLORS.Individual;
  return (
    <View
      testID={testID}
      style={[
        styles.badge,
        { backgroundColor: c.bg, paddingVertical: compact ? 2 : 4, paddingHorizontal: compact ? 6 : 8 },
      ]}
    >
      <Text style={[styles.badgeText, { color: c.fg, fontSize: compact ? 10 : 11 }]}>{type.toUpperCase()}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    borderRadius: radius.sm,
    alignSelf: "flex-start",
  },
  badgeText: {
    fontWeight: "700",
    letterSpacing: 0.4,
  },
});
