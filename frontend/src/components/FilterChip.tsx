import { Pressable, Text, StyleSheet, View } from "react-native";
import * as Haptics from "expo-haptics";
import { colors, radius } from "../theme/tokens";

export function FilterChip({
  label,
  count,
  active,
  onPress,
  testID,
}: {
  label: string;
  count?: number;
  active: boolean;
  onPress: () => void;
  testID?: string;
}) {
  return (
    <Pressable
      testID={testID}
      onPress={() => {
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
        onPress();
      }}
      style={[
        styles.chip,
        active ? styles.chipActive : styles.chipInactive,
      ]}
    >
      <Text style={[styles.text, { color: active ? colors.onBrandPrimary : colors.onSurface }]}>
        {label}
      </Text>
      {typeof count === "number" ? (
        <View style={[styles.countWrap, { backgroundColor: active ? "rgba(255,255,255,0.18)" : colors.surfaceTertiary }]}>
          <Text style={[styles.countText, { color: active ? colors.onBrandPrimary : colors.onSurfaceTertiary }]}>
            {count}
          </Text>
        </View>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  chip: {
    height: 36,
    paddingHorizontal: 14,
    borderRadius: radius.pill,
    flexDirection: "row",
    alignItems: "center",
    flexShrink: 0,
    borderWidth: 1,
  },
  chipActive: {
    backgroundColor: colors.brandPrimary,
    borderColor: colors.brandPrimary,
  },
  chipInactive: {
    backgroundColor: colors.surfaceSecondary,
    borderColor: colors.border,
  },
  text: {
    fontSize: 13,
    fontWeight: "700",
    letterSpacing: 0.2,
  },
  countWrap: {
    marginLeft: 8,
    paddingHorizontal: 6,
    minWidth: 22,
    height: 18,
    borderRadius: radius.pill,
    alignItems: "center",
    justifyContent: "center",
  },
  countText: { fontSize: 11, fontWeight: "800" },
});
