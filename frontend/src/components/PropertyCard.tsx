import { View, Text, StyleSheet, Pressable } from "react-native";
import { Image } from "expo-image";
import { Ionicons } from "@expo/vector-icons";
import { colors, radius, spacing, tabularNums } from "../theme/tokens";
import { OwnerBadge } from "./OwnerBadge";
import { propertyImageUrl, type Property } from "../lib/api";
import { getDealEligibility } from "../lib/dealEligibility";

export function fmtMoney(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n < 0 ? "-" : "";
  const v = Math.abs(n);
  if (v >= 1_000_000) return `${sign}$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `${sign}$${Math.round(v / 1_000)}K`;
  return `${sign}$${Math.round(v)}`;
}

export function PropertyCard({
  property,
  saved,
  onPress,
  onToggleSave,
  testID,
}: {
  property: Property;
  saved: boolean;
  onPress: () => void;
  onToggleSave: () => void;
  testID?: string;
}) {
  const p = property as any;
  const eligibility = getDealEligibility(property);

  // Listings is intentionally a DEAL feed, not a general MLS feed.
  if (!eligibility.allowed) return null;

  const photo = propertyImageUrl(property);
  const basis = eligibility.acquisition_basis;
  const spread = eligibility.spread;
  const exit = eligibility.exit_value;
  const category = eligibility.category || "Opportunity";

  return (
    <Pressable testID={testID} onPress={onPress} style={styles.card}>
      <View style={styles.imageWrap}>
        <Image source={photo ? { uri: photo } : undefined} style={styles.image} contentFit="cover" transition={150} />
        <View style={styles.categoryPill}><Text style={styles.categoryText}>{category.toUpperCase()}</Text></View>
        <Pressable
          testID={`${testID}-save`}
          onPress={(e) => { e.stopPropagation(); onToggleSave(); }}
          style={styles.saveBtn}
          hitSlop={10}
        >
          <Ionicons name={saved ? "bookmark" : "bookmark-outline"} size={18} color="#fff" />
        </Pressable>
      </View>

      <View style={styles.body}>
        <Text style={styles.address} numberOfLines={1}>
          {p.situs_address || p.address || p.street_address || "Address unavailable"}
        </Text>
        <Text style={styles.meta}>
          {p.beds ? `${p.beds} bd` : "— bd"} · {p.baths ? `${p.baths} ba` : "— ba"} · {p.sqft ? `${Number(p.sqft).toLocaleString()} sqft` : "— sqft"}
        </Text>

        <View style={styles.priceRow}>
          <View style={styles.metric}>
            <Text style={styles.label}>{eligibility.basis_source === "purchase_price" ? "PURCHASE" : "ASSESSED BASIS"}</Text>
            <Text style={styles.value}>{fmtMoney(basis)}</Text>
          </View>
          <View style={styles.metric}>
            <Text style={styles.label}>EXIT VALUE</Text>
            <Text style={styles.value}>{fmtMoney(exit)}</Text>
          </View>
          <View style={styles.metric}>
            <Text style={styles.label}>SPREAD</Text>
            <Text style={[styles.value, spread != null && spread < 0 && styles.negative]}>{fmtMoney(spread)}</Text>
          </View>
        </View>

        <View style={styles.badges}>
          <OwnerBadge type={p.owner_type} compact />
          {p.pre_foreclosure ? <Badge text="PRE-FC" /> : null}
          {p.tax_delinquent || Number(p.current_tax_amount_due || 0) > 0 || Number(p.prior_tax_amount_due || 0) > 0 ? <Badge text="TAX LIEN" /> : null}
          {p.has_probate ? <Badge text="PROBATE" /> : null}
          {p.has_code_violations || Number(p.code_violation_count || 0) > 0 ? <Badge text="CODE" /> : null}
        </View>

        <Text style={styles.basisNote}>
          Spread = exit value − {eligibility.basis_source === "purchase_price" ? "purchase price" : "assessed value fallback"}.
        </Text>
      </View>
    </Pressable>
  );
}

function Badge({ text }: { text: string }) {
  return <View style={styles.badge}><Text style={styles.badgeText}>{text}</Text></View>;
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    overflow: "hidden",
    marginBottom: spacing.lg,
    borderWidth: 1,
    borderColor: colors.border,
  },
  imageWrap: { height: 150, width: "100%", backgroundColor: colors.surfaceTertiary },
  image: { width: "100%", height: "100%" },
  categoryPill: {
    position: "absolute", top: 10, left: 10,
    backgroundColor: "rgba(0,0,0,0.72)",
    borderRadius: radius.pill, paddingHorizontal: 9, paddingVertical: 5,
  },
  categoryText: { color: "#fff", fontSize: 10, fontWeight: "900", letterSpacing: 0.5 },
  saveBtn: {
    position: "absolute", top: 10, right: 10, width: 34, height: 34, borderRadius: 17,
    backgroundColor: "rgba(0,0,0,0.52)", alignItems: "center", justifyContent: "center",
  },
  body: { padding: spacing.md },
  address: { fontSize: 16, fontWeight: "800", color: collors.onSurface },
  meta: { marginTop: 3, fontSize: 12, color: colors.muted },
  priceRow: {
    flexDirection: "row", marginTop: spacing.md, paddingVertical: 10,
    borderTopWidth: StyleSheet.hairlineWidth, borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
  },
  metric: { flex: 1 },
  label: { fontSize: 9, fontWeight: "800", color: colors.muted, letterSpacing: 0.5 },
  value: { marginTop: 3, fontSize: 16, fontWeight: "900", color: colors.onSurface, ...tabularNums },
  negative: { color: colors.error },
  badges: { flexDirection: "row", flexWrap: "wrap", gap: 5, marginTop: 10, alignItems: "center" },
  badge: { backgroundColor: colors.brandTertiary, borderRadius: radius.pill, paddingHorizontal: 7, paddingVertical: 3 },
  badgeText: { fontSize: 9, fontWeight: "800", color: colors.onBrandTertiary },
  basisNote: { marginTop: 8, fontSize: 9, color: colors.muted },
});
