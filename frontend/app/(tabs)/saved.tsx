import { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, FlatList, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { getSaved, saveProperty, unsaveProperty, type Property } from "@/src/lib/api";
import { PropertyCard } from "@/src/components/PropertyCard";
import { colors, radius, spacing } from "@/src/theme/tokens";

export default function SavedScreen() {
  const router = useRouter();
  const [items, setItems] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      const { items: arr } = await getSaved();
      setItems(arr);
      setSavedIds(new Set(arr.map((p) => p.id)));
    } catch {}
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

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.eyebrow}>SAVED DEALS</Text>
        <Text style={styles.title}>Your Watchlist</Text>
      </View>
      {loading ? (
        <View style={styles.center}><ActivityIndicator color={colors.brandPrimary} /></View>
      ) : items.length === 0 ? (
        <View style={styles.center} testID="saved-empty">
          <Ionicons name="bookmark-outline" size={42} color={colors.muted} />
          <Text style={styles.emptyText}>No saved deals yet.</Text>
          <Text style={styles.emptySub}>Tap the bookmark on any property to track it here.</Text>
        </View>
      ) : (
        <FlatList
          testID="saved-list"
          data={items}
          keyExtractor={(p) => p.id}
          contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxxl }}
          renderItem={({ item }) => (
            <PropertyCard
              testID={`saved-card-${item.id}`}
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
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  eyebrow: { fontSize: 10, color: colors.muted, fontWeight: "800", letterSpacing: 1 },
  title: { fontSize: 24, fontWeight: "800", color: colors.onSurface, marginTop: 2 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  emptyText: { fontSize: 15, color: colors.onSurface, fontWeight: "700", marginTop: 12 },
  emptySub: { fontSize: 13, color: colors.muted, marginTop: 4, textAlign: "center" },
});
