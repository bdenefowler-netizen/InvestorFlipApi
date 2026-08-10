import { useMemo, useState } from "react";
import { ActivityIndicator, Linking, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { WebView } from "react-native-webview";

import { colors, radius, spacing } from "@/src/theme/tokens";

const TARRANT_PUBLIC_SEARCH_URL = "https://tarrant.tx.publicsearch.us/";

function asText(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] || "" : value || "";
}

function safeUrl(value: string): string {
  return value.startsWith("https://") ? value : TARRANT_PUBLIC_SEARCH_URL;
}

export default function TarrantSearchScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ address?: string; account?: string; url?: string }>();
  const initialAddress = asText(params.address);
  const account = asText(params.account);
  const initialUrl = safeUrl(asText(params.url) || TARRANT_PUBLIC_SEARCH_URL);
  const [address, setAddress] = useState(initialAddress);
  const [currentUrl, setCurrentUrl] = useState(initialUrl);
  const [loading, setLoading] = useState(true);

  const title = useMemo(() => (
    currentUrl.includes("fclosure.com") ? "Fclosure detail" : "Tarrant public search"
  ), [currentUrl]);

  const openOfficialSearch = () => {
    setLoading(true);
    setCurrentUrl(TARRANT_PUBLIC_SEARCH_URL);
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <Stack.Screen options={{ title, headerBackTitle: "Back" }} />
      <View style={styles.header}>
        <View style={styles.navRow}>
          <Pressable onPress={() => router.back()} style={styles.iconButton} testID="records-back">
            <Ionicons name="chevron-back" size={20} color={colors.onSurface} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={styles.eyebrow}>OFFICIAL RECORDS WORKSPACE</Text>
            <Text style={styles.title}>{title}</Text>
          </View>
          <Pressable onPress={() => Linking.openURL(currentUrl)} style={styles.iconButton} testID="records-open-external">
            <Ionicons name="open-outline" size={18} color={colors.onSurface} />
          </Pressable>
        </View>

        <View style={styles.lookupBox}>
          <Ionicons name="location-outline" size={17} color={colors.brandPrimary} />
          <TextInput
            value={address}
            onChangeText={setAddress}
            placeholder="Address to search"
            placeholderTextColor={colors.muted}
            style={styles.lookupInput}
            selectTextOnFocus
            testID="records-address-input"
          />
        </View>
        {account ? <Text selectable style={styles.metaText}>Account / parcel: {account}</Text> : null}

        <View style={styles.actions}>
          <Pressable onPress={openOfficialSearch} style={styles.primaryAction} testID="records-tarrant-search">
            <Ionicons name="search" size={15} color={colors.onBrandPrimary} />
            <Text style={styles.primaryActionText}>Tarrant Search</Text>
          </Pressable>
          <Pressable onPress={() => Linking.openURL(TARRANT_PUBLIC_SEARCH_URL)} style={styles.secondaryAction}>
            <Ionicons name="open-outline" size={15} color={colors.onSurface} />
            <Text style={styles.secondaryActionText}>Open Portal</Text>
          </Pressable>
        </View>
      </View>

      <View style={styles.webWrap}>
        {loading ? (
          <View style={styles.loading} pointerEvents="none">
            <ActivityIndicator color={colors.brandPrimary} />
          </View>
        ) : null}
        <WebView
          source={{ uri: currentUrl }}
          onLoadStart={() => setLoading(true)}
          onLoadEnd={() => setLoading(false)}
          startInLoadingState
          javaScriptEnabled
          domStorageEnabled
          sharedCookiesEnabled
          style={styles.web}
          testID="records-webview"
        />
      </View>
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
    backgroundColor: colors.surface,
    gap: spacing.sm,
  },
  navRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  iconButton: {
    width: 38,
    height: 38,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  eyebrow: { fontSize: 9, fontWeight: "900", letterSpacing: 1, color: colors.muted },
  title: { fontSize: 19, fontWeight: "800", color: colors.onSurface, marginTop: 2 },
  lookupBox: {
    height: 42,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: 12,
  },
  lookupInput: { flex: 1, color: colors.onSurface, fontSize: 13, fontWeight: "700", paddingVertical: 0 },
  metaText: { color: colors.muted, fontSize: 11, fontWeight: "700" },
  actions: { flexDirection: "row", gap: spacing.sm },
  primaryAction: {
    flex: 1,
    height: 40,
    borderRadius: radius.md,
    backgroundColor: colors.brandPrimary,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 7,
  },
  primaryActionText: { color: colors.onBrandPrimary, fontSize: 12, fontWeight: "800" },
  secondaryAction: {
    flex: 1,
    height: 40,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 7,
  },
  secondaryActionText: { color: colors.onSurface, fontSize: 12, fontWeight: "800" },
  webWrap: { flex: 1, backgroundColor: colors.surfaceSecondary },
  web: { flex: 1, backgroundColor: colors.surfaceSecondary },
  loading: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 2,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(247,247,246,0.7)",
  },
});
