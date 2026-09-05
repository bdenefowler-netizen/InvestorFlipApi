import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TextInput,
  ActivityIndicator,
  RefreshControl,
  Pressable,
  ScrollView,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import {
  getFilters,
  getProperties,
  getAddressSuggestions,
  getSavedIds,
  saveProperty,
  unsaveProperty,
  type FilterDef,
  type AddressSuggestion,
  type Property,
} from "@/src/lib/api";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";
import { PropertyCard } from "@/src/components/PropertyCard";

export default function ListingsScreen() {
  const router = useRouter();
  const [filters, setFilters] = useState<FilterDef[]>([]);
  const [active, setActive] = useState<string>("opportunities");
  const [search, setSearch] = useState("");
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([]);
  const [suggesting, setSuggesting] = useState(false);
  const suppressSuggestions = useRef(false);
  const [items, setItems] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set());

  const loadFilters = useCallback(async () => {
    try {
      const data = await getFilters();
      setFilters(data.filters);
    } catch { /* non-fatal */ }
  }, []);

  const loadSaved = useCallback(async () => {
    try {
      const { ids } = await getSavedIds();
      setSavedIds(new Set(ids));
    } catch { /* non-fatal */ }
  }, []);

  const loadProperties = useCallback(async () => {
    setError(null);
    try {
      const data = await getProperties(active, search.trim());
      setItems(data.items ?? []);
    } catch (e: any) {
      setError("Unable to load listings.");
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

  useEffect(() => {
    const query = search.trim();
    if (suppressSuggestions.current) {
      suppressSuggestions.current = false;
      setSuggestions([]);
      setSuggesting(false);
      return;
    }
    if (query.length < 5) { setSuggestions([]); setSuggesting(false); return; }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setSuggesting(true);
      try {
        const data = await getAddressSuggestions(query, controller.signal);
        setSuggestions(data.items);
      } catch (error: any) {
        if (error?.name !== "AbortError") setSuggestions([]);
      } finally {
        if (!controller.signal.aborted) setSuggesting(false);
      }
    }, 450);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [search]);

  const selectSuggestion = (s: AddressSuggestion) => {
    suppressSuggestions.current = true;
    setSuggestions([]);
    setSearch(s.street_address || s.title || "");
  };

  useFocusEffect(useCallback(() => { loadProperties(); loadSaved(); }, [loadProperties, loadSaved]));

  const onRefresh = () => { setRefreshing(true); Promise.all([loadFilters(), loadSaved(), loadProperties()]); };

  const toggleSave = async (id: string) => {
    const next = new Set(savedIds);
    if (next.has(id)) { next.delete(id); setSavedIds(next); try { await unsaveProperty(id); } catch {} }
    else { next.add(id); setSavedIds(next); try { await saveProperty(id); } catch {} }
  };

  const totalLabel = useMemo(() => {
    const f = filters.find((x) => x.key === active);
    return f ? `${items.length} of ${f.count}` : `${items.length}`;
  }, [filters, active, items.length]);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* ── Header ── */}
      <View style={styles.header}>
        <View style={styles.titleRow}>
          <View>
            <Text style={styles.eyebrow}>TARRANT COUNTY · TX</Text>
            <Text style={styles.title}>Deals</Text>
          </View>
          <View style={styles.countPill}>
            <Text style={styles.countPillText}>{totalLabel}</Text>
          </View>
        </View>

        {/* Search bar */}
        <View style={styles.searchBox}>
          <Ionicons name="search" size={15} color={colors.muted} />
          <TextInput
            placeholder="Address, city, owner, ZIP…"
            placeholderTextColor={colors.muted}
            value={search}
            onChangeText={(v) => { suppressSuggestions.current = false; setSearch(v); }}
            style={styles.searchInput}
            returnKeyType="search"
            autoCorrect={false}
            autoCapitalize="words"
          />
          {search ? (
            <Pressable onPress={() => { suppressSuggestions.current = true; setSuggestions([]); setSearch(""); }} hitSlop={10}>
              <Ionicons name="close-circle" size={16} color={colors.muted} />
            </Pressable>
          ) : null}
        </View>

        {/* Address suggestions */}
        {(suggesting || suggestions.length > 0) ? (
          <View style={styles.suggestionPanel}>
            {suggesting && suggestions.length === 0 ? (
              <View style={styles.suggestionLoading}>
                <ActivityIndicator size="small" color={colors.brandPrimary} />
                <Text style={styles.suggestionMeta}>Checking that address…</Text>
              </View>
            ) : (
              suggestions.map((s) => (
                <Pressable key={`${s.property_reach_id}-${s.title}`} onPress={() => selectSuggestion(s)} style={({ pressed }) => [styles.suggestionRow, pressed && styles.suggestionPressed]}>
                  <Ionicons name="location-outline" size={15} color={colors.brandPrimary} />
                  <View style={styles.suggestionText}>
                    <Text style={styles.suggestionTitle} numberOfLines={1}>{s.title}</Text>
                    <Text style={styles.suggestionMeta} numberOfLines={1}>{(s.county || "") + (s.zip ? " · " + s.zip : "")}</Text>
                  </View>
                </Pressable>
              ))
            )}
          </View>
        ) : null}
      </View>

      {/* ── Filter Chips ── */}
      <View style={styles.chipSection}>
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chipScroll}
        >
          {filters.map((f) => {
            const isActive = f.key === active;
            return (
              <Pressable
                key={f.key}
                onPress={() => { setActive(f.key); setLoading(true); }}
                style={[styles.chip, isActive && styles.chipActive]}
              >
                <Text style={[styles.chipText, isActive && styles.chipTextActive]}>{f.label}</Text>
                {f.count != null && (
                  <View style={[styles.chipCount, isActive && styles.chipCountActive]}>
                    <Text style={[styles.chipCountText, isActive && styles.chipCountTextActive]}>
                      {f.count > 999 ? "999+" : f.count}
                    </Text>
                  </View>
                )}
              </Pressable>
            );
          })}
        </ScrollView>
      </View>

      {/* ── Content ── */}
      {loading ? (
        <View style={styles.center}><ActivityIndicator color={colors.brandPrimary} /></View>
      ) : error ? (
        <View style={styles.center}>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable style={styles.retryBtn} onPress={loadProperties}>
            <Text style={styles.retryText}>Retry</Text>
          </Pressable>
        </View>
      ) : items.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="home-outline" size={44} color={colors.muted} />
          <Text style={styles.emptyText}>No deals found.</Text>
          <Text style={styles.emptySub}>Try a different filter or search term.</Text>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(p) => p.id}
          contentContainerStyle={styles.listContent}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.brandPrimary} />}
          showsVerticalScrollIndicator={false}
          renderItem={({ item }) => (
            <PropertyCard
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
  eyebrow: { fontSize: 10, color: colors.muted, fontWeight: "800", letterSpacing: 1.2 },
  title: { fontSize: 24, fontWeight: "800", color: colors.onSurface, marginTop: 1 },
  countPill: {
    backgroundColor: colors.brandTertiary,
    paddingHorizontal: 10, paddingVertical: 5,
    borderRadius: radius.pill,
  },
  countPillText: { fontSize: 12, fontWeight: "800", color: colors.onBrandTertiary, ...tabularNums },
  searchBox: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.border,
    paddingHorizontal: 12, height: 42, marginTop: spacing.md, gap: 8,
  },
  searchInput: { flex: 1, fontSize: 14, color: colors.onSurface, paddingVertical: 0 },
  suggestionPanel: {
    backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border,
    borderRadius: radius.md, marginTop: 6, overflow: "hidden",
  },
  suggestionLoading: { minHeight: 46, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", gap: 9 },
  suggestionRow: {
    minHeight: 50, paddingHorizontal: 12, paddingVertical: 8,
    flexDirection: "row", alignItems: "center", gap: 9,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border,
  },
  suggestionPressed: { backgroundColor: colors.surfaceSecondary },
  suggestionText: { flex: 1 },
  suggestionTitle: { color: colors.onSurface, fontSize: 13, fontWeight: "700" },
  suggestionMeta: { color: colors.muted, fontSize: 11, marginTop: 1 },

  // Horizontal filter chips
  chipSection: {
    backgroundColor: colors.surface,
    borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  chipScroll: { paddingHorizontal: spacing.lg, paddingVertical: 10, gap: 8, flexDirection: "row" },
  chip: {
    flexDirection: "row", alignItems: "center", gap: 6,
    paddingHorizontal: 14, paddingVertical: 7,
    borderRadius: radius.pill,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1, borderColor: colors.border,
  },
  chipActive: { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary },
  chipText: { fontSize: 13, fontWeight: "700", color: colors.onSurface },
  chipTextActive: { color: "#fff" },
  chipCount: {
    minWidth: 22, height: 18, paddingHorizontal: 5,
    borderRadius: radius.pill, backgroundColor: colors.surfaceTertiary,
    alignItems: "center", justifyContent: "center",
  },
  chipCountActive: { backgroundColor: "rgba(255,255,255,0.2)" },
  chipCountText: { fontSize: 10, fontWeight: "800", color: colors.onSurfaceTertiary },
  chipCountTextActive: { color: "#fff" },

  listContent: { padding: spacing.lg, paddingBottom: spacing.xxxl },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  errorText: { color: colors.error, fontSize: 14, marginBottom: spacing.md, fontWeight: "600" },
  retryBtn: { paddingHorizontal: 18, paddingVertical: 10, backgroundColor: colors.brandPrimary, borderRadius: radius.md },
  retryText: { color: "#fff", fontWeight: "700" },
  emptyText: { fontSize: 15, color: colors.onSurface, fontWeight: "700", marginTop: 12 },
  emptySub: { fontSize: 13, color: colors.muted, marginTop: 4 },
});
