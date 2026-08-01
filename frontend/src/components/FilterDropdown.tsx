import { useState } from "react";
import {
  Modal,
  Pressable,
  Text,
  View,
  StyleSheet,
  FlatList,
  TouchableOpacity,
} from "react-native";
import * as Haptics from "expo-haptics";
import { Ionicons } from "@expo/vector-icons";
import { colors, radius, spacing } from "../theme/tokens";

export interface FilterOption {
  key: string;
  label: string;
  count?: number;
}

export function FilterDropdown({
  options,
  activeKey,
  onSelect,
  testID,
}: {
  options: FilterOption[];
  activeKey: string;
  onSelect: (key: string) => void;
  testID?: string;
}) {
  const [open, setOpen] = useState(false);
  const active = options.find((o) => o.key === activeKey) ?? options[0];

  return (
    <View testID={testID}>
      {/* Trigger */}
      <Pressable
        testID="filter-dropdown-trigger"
        onPress={() => {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
          setOpen(true);
        }}
        style={styles.trigger}
      >
        <Text style={styles.triggerLabel} numberOfLines={1}>
          {active?.label ?? "All"}
        </Text>
        {typeof active?.count === "number" ? (
          <View style={styles.countWrap}>
            <Text style={styles.countText}>{active.count}</Text>
          </View>
        ) : null}
        <Ionicons
          name={open ? "chevron-up" : "chevron-down"}
          size={16}
          color={colors.onSurfaceTertiary}
        />
      </Pressable>

      {/* Dropdown modal */}
      <Modal
        visible={open}
        transparent
        animationType="fade"
        onRequestClose={() => setOpen(false)}
      >
        <Pressable style={styles.backdrop} onPress={() => setOpen(false)}>
          <View style={styles.sheet}>
            <View style={styles.handle} />
            <Text style={styles.sheetTitle}>Filter deals</Text>
            <FlatList
              data={options}
              keyExtractor={(o) => o.key}
              style={styles.list}
              renderItem={({ item }) => {
                const isActive = item.key === activeKey;
                return (
                  <TouchableOpacity
                    testID={`dropdown-option-${item.key}`}
                    onPress={() => {
                      Haptics.selectionAsync().catch(() => {});
                      onSelect(item.key);
                      setOpen(false);
                    }}
                    style={[styles.option, isActive && styles.optionActive]}
                  >
                    <Text
                      style={[
                        styles.optionLabel,
                        { color: isActive ? colors.brandPrimary : colors.onSurface },
                      ]}
                    >
                      {item.label}
                    </Text>
                    {typeof item.count === "number" ? (
                      <View style={[styles.optionCount, isActive && { backgroundColor: colors.brandPrimary }]}>
                        <Text
                          style={[
                            styles.optionCountText,
                            { color: isActive ? colors.onBrandPrimary : colors.onSurfaceTertiary },
                          ]}
                        >
                          {item.count}
                        </Text>
                      </View>
                    ) : null}
                    {isActive ? (
                      <Ionicons name="checkmark-circle" size={18} color={colors.brandPrimary} />
                    ) : null}
                  </TouchableOpacity>
                );
              }}
            />
            <Pressable style={styles.closeBtn} onPress={() => setOpen(false)}>
              <Text style={styles.closeText}>Close</Text>
            </Pressable>
          </View>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  trigger: {
    height: 42,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: 14,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  triggerLabel: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.onSurface,
    flexShrink: 1,
  },
  countWrap: {
    minWidth: 22,
    height: 20,
    paddingHorizontal: 6,
    borderRadius: radius.pill,
    backgroundColor: colors.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  countText: { fontSize: 11, fontWeight: "800", color: colors.onSurfaceTertiary },
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.5)",
    justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingBottom: 28,
    paddingHorizontal: 16,
    maxHeight: "70%",
  },
  handle: {
    alignSelf: "center",
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.border,
    marginTop: 10,
    marginBottom: 12,
  },
  sheetTitle: {
    fontSize: 16,
    fontWeight: "800",
    color: colors.onSurface,
    marginBottom: 10,
  },
  list: { flexGrow: 0 },
  option: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 13,
    paddingHorizontal: 12,
    borderRadius: radius.md,
    marginBottom: 4,
  },
  optionActive: { backgroundColor: colors.surfaceSecondary },
  optionLabel: { fontSize: 15, fontWeight: "600", flex: 1 },
  optionCount: {
    minWidth: 24,
    height: 20,
    paddingHorizontal: 7,
    borderRadius: radius.pill,
    backgroundColor: colors.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  optionCountText: { fontSize: 12, fontWeight: "800" },
  closeBtn: {
    marginTop: 12,
    height: 44,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surfaceSecondary,
  },
  closeText: { fontSize: 15, fontWeight: "700", color: colors.onSurface },
});
