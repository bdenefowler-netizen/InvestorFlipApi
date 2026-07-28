import { View, Text, StyleSheet } from "react-native";
import { colors, radius, spacing } from "../theme/tokens";

type DistressType = 
  | "code_violation"
  | "vacant"
  | "nuisance"
  | "foreclosure"
  | "wholesale"
  | "distressed"
  | "tax_delinquent";

const DISTRESS_CONFIG: Record<DistressType, { bg: string; fg: string; label: string }> = {
  code_violation: { bg: "#F1D9D5", fg: "#7A2A24", label: "CODE VIOLATION" },
  vacant: { bg: "#F2E0BD", fg: "#5A3F0E", label: "VACANT" },
  nuisance: { bg: "#E4DCD3", fg: "#5A3E1E", label: "NUISANCE" },
  foreclosure: { bg: "#D6DDE0", fg: "#1E2D38", label: "FORECLOSURE" },
  wholesale: { bg: "#D9E7DC", fg: "#1F4329", label: "WHOLESALE" },
  distressed: { bg: "#E2DDE9", fg: "#3F2E5E", label: "DISTRESSED" },
  tax_delinquent: { bg: "#F1D9D5", fg: "#7A2A24", label: "TAX DELINQUENT" },
};

export function DistressBadge({ 
  type, 
  compact = false, 
  testID 
}: { 
  type: DistressType; 
  compact?: boolean; 
  testID?: string; 
}) {
  const config = DISTRESS_CONFIG[type] || DISTRESS_CONFIG.distressed;
  
  return (
    <View
      testID={testID}
      style={[
        styles.badge,
        { 
          backgroundColor: config.bg, 
          paddingVertical: compact ? 2 : 4, 
          paddingHorizontal: compact ? 6 : 8 
        },
      ]}
    >
      <Text style={[styles.badgeText, { color: config.fg, fontSize: compact ? 10 : 11 }]}>
        {config.label}
      </Text>
    </View>
  );
}

export function DistressScoreBar({ score }: { score: number }) {
  const getScoreColor = (s: number) => {
    if (s >= 70) return colors.error;
    if (s >= 40) return colors.warning;
    return colors.success;
  };

  const getScoreLabel = (s: number) => {
    if (s >= 70) return "High Distress";
    if (s >= 40) return "Moderate Distress";
    return "Low Distress";
  };

  return (
    <View style={styles.scoreContainer}>
      <View style={styles.scoreHeader}>
        <Text style={styles.scoreLabel}>Distress Score</Text>
        <Text style={[styles.scoreValue, { color: getScoreColor(score) }]}>{score}/100</Text>
      </View>
      <View style={styles.scoreTrack}>
        <View 
          style={[
            styles.scoreFill, 
            { 
              width: `${score}%`, 
              backgroundColor: getScoreColor(score) 
            }
          ]} 
        />
      </View>
      <Text style={[styles.scoreDescription, { color: getScoreColor(score) }]}>
        {getScoreLabel(score)}
      </Text>
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
  scoreContainer: {
    marginTop: spacing.sm,
  },
  scoreHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 4,
  },
  scoreLabel: {
    fontSize: 12,
    fontWeight: "600",
    color: colors.onSurfaceTertiary,
  },
  scoreValue: {
    fontSize: 14,
    fontWeight: "800",
  },
  scoreTrack: {
    height: 6,
    backgroundColor: colors.surfaceTertiary,
    borderRadius: radius.pill,
    overflow: "hidden",
  },
  scoreFill: {
    height: "100%",
    borderRadius: radius.pill,
  },
  scoreDescription: {
    fontSize: 11,
    fontWeight: "600",
    marginTop: 4,
  },
});
