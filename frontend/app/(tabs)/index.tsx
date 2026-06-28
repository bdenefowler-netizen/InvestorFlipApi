import { useEffect, useMemo, useState, useCallback } from "react";
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  ScrollView,
  TextInput,
  ActivityIndicator,
  RefreshControl,
  Pressable,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import {
  getFilters,
  getProperties,
  getSavedIds,
  saveProperty,
  unsaveProperty,
  type FilterDef,
  type Property,
} from "@/src/lib/api";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";
import { FilterChip } from "@/src/components/FilterChip";
import { PropertyCard } from "@/src/components/PropertyCard";

export default function ListingsScreen() {
  const router = useRouter();
  const [filters, setFilters] = useState<FilterDef[]>([]);
  const [active, setActive] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set());

  const loadFilters = useCallback(async () => {
    try {
      const data = await getFilters();
      setFilters(data.filters);
    } catch (e: any) {
      // non-fatal
    }
  }, []);

  const loadSaved = useCallback(async () => {
    try {
      const { ids } = await getSavedIds();
      setSavedIds(new Set(ids));
    } catch {}
  }, []);

  const loadProperties = useCallback(async () => {
    setError(null);
    try {
      const data = await getProperties(active, search.trim());
      setItems(data.items);
    } catch (e: any) {
      setError("Unable to load Tarrant County listings.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [active, search]);

  useEffect(() => {
    loadFilters();
    loadSaved();
  }, [loadFilters, loadSaved]);

  useEffect(() => {
    setLoading(true);
    const t = setTimeout(loadProperties, 200);
    return () => clearTimeout(t);
  }, [loadProperties]);

  // Refetch when returning from detail page (enrichment may have updated beds/baths)
  useFocusEffect(
    useCallback(() => {
      loadProperties();
      loadSaved();
    }, [loadProperties, loadSaved])
  );

  const onRefresh = () => {
    setRefreshing(true);
    Promise.all([loadFilters(), loadSaved(), loadProperties()]);
  };

  const toggleSave = async (id: string) => {
    const next = new Set(savedIds);
    if (next.has(id)) {
      next.delete(id);
      setSavedIds(next);
      try { await unsaveProperty(id); } catch {}
    } else {
      next.add(id);
      setSavedIds(next);
      try { await saveProperty(id); } catch {}
    }
  };

  const totalLabel = useMemo(() => {
    const f = filters.find((x) => x.key === active);
    return f ? `${items.length} of ${f.count}` : `${items.length}`;
  }, [filters, active, items.length]);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* Sticky Header */}
      <View style={styles.header} testID="listings-header">
        <View style={styles.titleRow}>
          <View>
            <Text style={styles.eyebrow}>TARRANT COUNTY · TX</Text>
            <Text style={styles.title}>Investor Deals</Text>
          </View>
          <View testID="result-count" style={styles.countPill}>
            <Text style={[styles.countPillText, tabularNums]}>{totalLabel}</Text>
          </View>
        </View>

        <View style={styles.searchBox} testID="search-box">
          <Ionicons name="search" size={16} color={colors.muted} />
          <TextInput
            testID="search-input"
            placeholder="Search address, city, owner, ZIP"
            placeholderTextColor={colors.muted}
            value={search}
            onChangeText={setSearch}
            style={styles.searchInput}
            returnKeyType="search"
          />
          {search ? (
            <Pressable onPress={() => setSearch("")} hitSlop={10} testID="clear-search">
              <Ionicons name="close-circle" size={18} color={colors.muted} />
            </Pressable>
          ) : null}
        </View>

        <ScrollView
          testID="filter-row"
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chipRow}
          style={styles.chipScroll}
        >
          {filters.map((f) => (
            <FilterChip
              key={f.key}
              testID={`filter-${f.key}`}
              label={f.label}
              count={f.count}
              active={active === f.key}
              onPress={() => setActive(f.key)}
            />
          ))}
        </ScrollView>
      </View>

      {/* List */}
      {loading ? (
        <View style={styles.center} testID="listings-loading">
          <ActivityIndicator color={colors.brandPrimary} />
        </View>
      ) : error ? (
        <View style={styles.center} testID="listings-error">
          <Text style={styles.errorText}>{error}</Text>
          <Pressable testID="retry-button" style={styles.retryBtn} onPress={loadProperties}>
            <Text style={styles.retryText}>Retry</Text>
          </Pressable>
        </View>
      ) : items.length === 0 ? (
        <View style={styles.center} testID="listings-empty">
          <Ionicons name="home-outline" size={42} color={colors.muted} />
          <Text style={styles.emptyText}>No properties match your filters.</Text>
          <Text style={styles.emptySub}>Try broadening your criteria.</Text>
        </View>
      ) : (
        <FlatList
          testID="property-list"
          data={items}
          keyExtractor={(p) => p.id}
          contentContainerStyle={styles.listContent}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.brandPrimary} />}
          renderItem={({ item }) => (
            <PropertyCard
              testID={`property-card-${item.id}`}
              property={item}
              saved={savedIds.has(item.id)}
              onPress={() => router.push(`/property/${item.id}`)}
              onToggleSave={() => toggleSave(item.id)}
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
    backgroundColor: colors.surface,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  titleRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  eyebrow: { fontSize: 10, color: colors.muted, fontWeight: "800", letterSpacing: 1 },
  title: { fontSize: 24, fontWeight: "800", color: colors.onSurface, marginTop: 2 },
  countPill: {
    backgroundColor: colors.brandTertiary,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: radius.pill,
  },
  countPillText: { fontSize: 12, fontWeight: "800", color: colors.onBrandTertiary },
  searchBox: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: 12,
    height: 42,
    marginTop: spacing.md,
    gap: 8,
  },
  searchInput: {
    flex: 1,
    fontSize: 14,
    color: colors.onSurface,
    paddingVertical: 0,
  },
  chipScroll: {
    marginTop: spacing.md,
    height: 36,
  },
  chipRow: {
    gap: 8,
    paddingRight: 8,
    alignItems: "center",
  },
  listContent: {
    padding: spacing.lg,
    paddingBottom: spacing.xxxl,
  },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  errorText: { color: colors.error, fontSize: 14, marginBottom: spacing.md, fontWeight: "600" },
  retryBtn: {
    paddingHorizontal: 18,
    paddingVertical: 10,
    backgroundColor: colors.brandPrimary,
    borderRadius: radius.md,
  },
  retryText: { color: colors.onBrandPrimary, fontWeight: "700" },
  emptyText: { fontSize: 15, color: colors.onSurface, fontWeight: "700", marginTop: 12 },
  emptySub: { fontSize: 13, color: colors.muted, marginTop: 4 },
});
