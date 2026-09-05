import { useState } from "react";
import {
  Modal,
  Pressable,
  Text,
  View,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Dimensions,
} from "react-native";
import * as Haptics from "expo-haptics";
import { Ionicons } from "@expo/vector-icons";
import { colors, radius, spacing } from "../theme/tokens";

const WIDTH = Dimensions.get("window").width;

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

  const handleSelect = (key: string) => {
    Haptics.selectionAsync().catch(() => {});
    onSelect(key);
    setOpen(false);
  };

  return (
    <View testID={testID}>
      {/* Trigger — styled pill */}
      <Pressable
        testID="filter-dropdown-trigger"
        onPress={() => {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
          setOpen(true);
        }}
        style={styles.trigger}
      >
        <Ionicons name="options-outline" size={14} color={colors.onSurfaceTertiary} />
        <Text style={styles.triggerLabel} numberOfLines={1}>
          {active?.label ?? "Filter"}
        </Text>
        {typeof active?.count === "number" && (
          <View style={styles.countPill}>
            <Text style={styles.countText}>{active.count}</Text>
          </View>
        )}
        <Ionicons name="chevron-down" size={14} color={colors.onSurfaceTertiary} />
      </Pressable>

      {/* Top drawer modal */}
      <Modal
        visible={open}
        transparent
        animationType="slide"
        onRequestClose={() => setOpen(false)}
      >
        <Pressable
          style={styles.backdrop}
          activeOpacity={1}
          onPress={() => setOpen(false)}
        >
          <View style={styles.drawer} onStartShouldSetResponder={() => true}>
            {/* Drawer handle */}
            <View style={styles.handleRow}>
              <View style={styles.handle} />
            </View>

            {/* Header */}
            <View style={styles.drawerHeader}>
              <Text style={styles.drawerTitle}>Filter Deals</Text>
              <TouchableOpacity
                onPress={() => setOpen(false)}
                hitSlop={12}
              >
                <Ionicons name="close" size={22} color={colors.onSurfaceTertiary} />
              </TouchableOpacity>
            </View>

            {/* Scrollable options */}
            <ScrollView
              style={styles.scrollArea}
              contentContainerStyle={styles.scrollContent}
              showsVerticalScrollIndicator={false}
            >
              {/* Active filter info */}
              {activeKey !== "all" && (
                <View style={styles.activeInfo}>
                  <Ionicons name="checkmark-circle" size={16} color={colors.brandPrimary} />
                  <Text style={styles.activeInfoText}>
                    Showing: <Text style={styles.activeInfoBold}>{active?.label}</Text> ({active?.count})
                  </Text>
                </View>
              )}

              {/* Options grid — 2 columns */}
              <View style={styles.grid}>
                {options.map((option) => {
                  const isActive = option.key === activeKey;
                  return (
                    <TouchableOpacity
                      key={option.key}
                      testID={`dropdown-option-${option.key}`}
                      onPress={() => handleSelect(option.key)}
                      style={[
                        styles.gridCard,
                        isActive && styles.gridCardActive,
                      ]}
                      activeOpacity={0.7}
                    >
                      <View style={styles.gridCardTop}>
                        <Text
                          style={[
                            styles.gridCardLabel,
                            isActive && styles.gridCardLabelActive,
                          ]}
                          numberOfLines={2}
                        >
                          {option.label}
                        </Text>
                        {isActive && (
                          <Ionicons
                            name="checkmark-circle-fill"
                            size={18}
                            color={colors.brandPrimary}
                          />
                        )}
                      </View>
                      {typeof option.count === "number" && (
                        <Text
                          style={[
                            styles.gridCardCount,
                            isActive && styles.gridCardCountActive,
                          ]}
                        >
                          {option.count.toLocaleString()} deals
                        </Text>
                      )}
                    </TouchableOpacity>
                  );
                })}
              </View>
            </ScrollView>

            {/* Apply button */}
            <View style={styles.footer}>
              <TouchableOpacity
                style={styles.applyBtn}
                onPress={() => setOpen(false)}
                activeOpacity={0.8}
              >
                <Text style={styles.applyBtnText}>Apply Filter</Text>
                <Ionicons name="checkmark" size={18} color="#fff" />
              </TouchableOpacity>
            </View>
          </View>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  trigger: {
    height: 40,
    paddingHorizontal: 14,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  triggerLabel: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.onSurface,
  },
  countPill: {
    minWidth: 24,
    height: 18,
    paddingHorizontal: 6,
    borderRadius: radius.pill,
    backgroundColor: colors.brandTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  countText: {
    fontSize: 10,
    fontWeight: "800",
    color: colors.onBrandTertiary,
  },

  // Modal backdrop
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "flex-start", // ← TOP, not bottom
  },

  // Drawer at top (was bottom sheet)
  drawer: {
    backgroundColor: colors.surface,
    borderBottomLeftRadius: 20,
    borderBottomRightRadius: 20,
    paddingBottom: 28,
    // Max height so it doesn't eat the whole screen
    maxHeight: Dimensions.get("window").height * 0.65,
  },

  handleRow: {
    alignItems: "center",
    paddingTop: 10,
    paddingBottom: 4,
  },
  handle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.border,
  },

  drawerHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  drawerTitle: {
    fontSize: 17,
    fontWeight: "800",
    color: colors.onSurface,
  },

  activeInfo: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 20,
    paddingTop: 14,
    paddingBottom: 8,
  },
  activeInfoText: {
    fontSize: 12,
    color: colors.muted,
  },
  activeInfoBold: {
    fontWeight: "800",
    color: colors.brandPrimary,
  },

  scrollArea: {
    flexGrow: 0,
  },
  scrollContent: {
    paddingHorizontal: 12,
    paddingTop: 10,
    paddingBottom: 4,
  },

  // 2-column grid
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  gridCard: {
    width: (WIDTH - 44) / 2, // 20+20 padding + 12+12 gap / 2 columns
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: 14,
    marginBottom: 4,
  },
  gridCardActive: {
    backgroundColor: colors.brandTertiary,
    borderColor: colors.brandPrimary,
  },
  gridCardTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 8,
  },
  gridCardLabel: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.onSurface,
    flex: 1,
    lineHeight: 18,
  },
  gridCardLabelActive: {
    color: colors.brandPrimary,
  },
  gridCardCount: {
    fontSize: 11,
    color: colors.muted,
    marginTop: 4,
  },
  gridCardCountActive: {
    color: colors.onBrandTertiary,
  },

  footer: {
    paddingHorizontal: 20,
    paddingTop: 14,
    borderTopWidth: 1,
    borderTopColor: colors.divider,
  },
  applyBtn: {
    height: 48,
    borderRadius: radius.md,
    backgroundColor: colors.brandPrimary,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  applyBtnText: {
    color: "#fff",
    fontSize: 15,
    fontWeight: "800",
  },
});
