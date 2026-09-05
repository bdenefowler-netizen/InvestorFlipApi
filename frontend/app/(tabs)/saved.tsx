import { useCallback, useState } from "react";
import { View, Text, StyleSheet, FlatList, ActivityIndicator, TextInput, Pressable } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { getSaved, saveProperty, unsaveProperty, type Property } from "@/src/lib/api";
import { PropertyCard } from "@/src/components/PropertyCard";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";

export default function SavedScreen() {
  const router = useRouter();
  const [items, setItems] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    try {
      const { items: arr } = await getSaved();
      setItems(arr);
      setSavedIds(new Set(arr.map((p) => p.id)));
    } catch { /* non-fatal */ }
    finally { setLoading(false); }
  }, []);

  useFocusEffect(useCallback(() => { setLoading(true); load(); }, [load]));

  const toggle = async (id: string) => {
    const next = new Set(savedIds);
    if (next.has(id)) {
      next.delete(id);
      setItems((prev) => prev.filter((p) => p.id !== id));
      try { await unsaveProperty(id); } catch {}
    } else {
      next.add(id);
      try { await saveProperty(id); } catch {}
    }
    setSavedIds(next);
  };

  const filtered = search.trim()
    ? items.filter((p) => {
        const q = search.toLowerCase();
        return (p.situs_address || "").toLowerCase().includes(q) ||
               (p.city || "").toLowerCase().includes(q) ||
               (p.zip || "").toLowerCase().includes(q);
      })
    : items;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.titleRow}>
          <View>
            <Text style={styles.eyebrow}>YOUR WATCHLIST</Text>
            <Text style={styles.title}>Saved</Text>
          </View>
          <View style={styles.countPill}>
            <Text style={styles.countPillText}>{filtered.length}</Text>
          </View>
        </View>

        {/* Search bar */}
        {items.length > 0 ? (
          <View style={styles.searchBox}>
            <Ionicons name="search" size={15} color={colors.muted} />
            <TextInput
              placeholder="Filter saved deals…"
              placeholderTextColor={colors.muted}
              value={search}
              onChangeText={setSearch}
              style={styles.searchInput}
              autoCorrect={false}
              autoCapitalize="words"
            />
            {search ? (
              <Pressable onPress={() => setSearch("")} hitSlop={10}>
                <Ionicons name="close-circle" size={16} color={colors.muted} />
              </Pressable>
            ) : null}
          </View>
        ) : null}
      </View>

      {loading ? (
        <View style={styles.center}><ActivityIndicator color={colors.brandPrimary} /></View>
      ) : items.length === 0 ? (
        <View style={styles.center}>
          <View style={styles.emptyIcon}>
            <Ionicons name="bookmark-outline" size={36} color={colors.muted} />
          </View>
          <Text style={styles.emptyText}>No saved deals yet</Text>
          <Text style={styles.emptySub}>Tap the bookmark on any property to track it here.</Text>
        </View>
      ) : filtered.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="search-outline" size={36} color={colors.muted} />
          <Text style={styles.emptyText}>No matches</Text>
          <Text style={styles.emptySub}>Try a different search term.</Text>
        </View>
      ) : (
        <FlatList
          data={filtered}
          keyExtractor={(p) => p.id}
          contentContainerStyle={styles.listContent}
          showsVerticalScrollIndicator={false}
          renderItem={({ item }) => (
            <PropertyCard
              property={item}
              saved
              onPress={() => router.push(`/property/${item.id}`)}
              onToggleSave={() => toggle(item.id)}
            />
          )}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.md,
    borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  titleRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  eyebrow: { fontSize: 10, color: colors.muted, fontWeight: "800", letterSpacing: 1.2 },
  title: { fontSize: 24, fontWeight: "800", color: colors.onSurface, marginTop: 1 },
  countPill: {
    backgroundColor: colors.brandTertiary,
    paddingHorizontal: 10, paddingVertical: 5, borderRadius: radius.pill,
  },
  countPillText: { fontSize: 12, fontWeight: "800", color: colors.onBrandTertiary, ...tabularNums },
  searchBox: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.border,
    paddingHorizontal: 12, height: 42, marginTop: spacing.md, gap: 8,
  },
  searchInput: { flex: 1, fontSize: 14, color: colors.onSurface, paddingVertical: 0 },
  listContent: { padding: spacing.lg, paddingBottom: spacing.xxxl },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  emptyIcon: {
    width: 70, height: 70, borderRadius: 35,
    backgroundColor: colors.surfaceTertiary,
    alignItems: "center", justifyContent: "center",
  },
  emptyText: { fontSize: 15, color: colors.onSurface, fontWeight: "700", marginTop: 12 },
  emptySub: { fontSize: 13, color: colors.muted, marginTop: 4, textAlign: "center" },
});
