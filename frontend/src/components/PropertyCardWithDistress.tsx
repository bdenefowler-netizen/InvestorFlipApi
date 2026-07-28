import { View, Text, StyleSheet, Pressable } from "react-native";
import { Image } from "expo-image";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { colors, radius, spacing, tabularNums } from "../theme/tokens";
import { OwnerBadge } from "./OwnerBadge";
import { DistressBadge } from "./DistressBadge";
import { propertyImageUrl, type Property } from "../lib/api";

export function fmtMoney(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `$${Math.round(n / 1_000)}K`;
  return `$${n}`;
}

const LISTING_TYPE_COLORS: Record<string, string> = {
  REO: "#A3413B",
  Foreclosure: "#7A2A24",
  "As-Is": "#4A4A4A",
  Investor: "#2B3831",
  "Cash House": "#355C44",
  Wholesale: "#355C44",
  Distressed: "#B87B2E",
  "Code Violation": "#A3413B",
  Vacant: "#5A3F0E",
  Nuisance: "#5A3E1E",
  "Tax Lien": "#7A2A24",
};

export function PropertyCardWithDistress({
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
  const p = property;
  const photo = propertyImageUrl(p);
  const spread = p.value_spread;
  const typeColor = LISTING_TYPE_COLORS[p.listing_type] || colors.brandPrimary;
  
  // Check for distress indicators
  const hasViolation = (p as any).violation_count > 0;
  const isVacant = p.vacant;
  const hasDistress = hasViolation || isVacant || p.listing_type === "Distressed";
  const distressScore = (p as any).distress_score || 0;
  
  return (
    <Pressable testID={testID} onPress={onPress} style={styles.card}>
      <View style={styles.imageWrap}>
        <Image source={photo ? { uri: photo } : undefined} style={styles.image} contentFit="cover" transition={200} />
        <LinearGradient
          colors={["transparent", "rgba(26,28,26,0.85)"]}
          style={styles.scrim}
          pointerEvents="none"
        />
        
        {/* Listing type pill */}
        <View style={[styles.listingPill, { backgroundColor: typeColor }]}>
          <Text style={styles.listingPillText}>{p.listing_type.toUpperCase()}</Text>
        </View>
        
        {/* Distress score pill (if distressed) */}
        {hasDistress && distressScore > 0 && (
          <View style={[
            styles.distressPill, 
            { backgroundColor: distressScore >= 70 ? "#A3413B" : distressScore >= 40 ? "#B87B2E" : "#355C44" }
          ]}>
            <Text style={styles.distressPillText}>{distressScore}</Text>
          </View>
        )}
        
        {/* Save button */}
        <Pressable
          testID={`${testID}-save`}
          onPress={(e) => { e.stopPropagation(); onToggleSave(); }}
          style={styles.saveBtn}
          hitSlop={10}
        >
          <Ionicons name={saved ? "bookmark" : "bookmark-outline"} size={18} color="#fff" />
        </Pressable>
        
        {/* Price and spread */}
        <View style={styles.priceRow}>
          <View>
            <Text style={styles.price}>{fmtMoney(p.price)}</Text>
            <Text style={styles.priceLabel}>asking</Text>
          </View>
          <View style={{ alignItems: "flex-end" }}>
            <Text style={styles.roi}>{p.discount_to_benchmark_pct != null ? `${p.discount_to_benchmark_pct.toFixed(1)}%` : "—"}</Text>
            <Text style={styles.priceLabel}>value spread</Text>
          </View>
        </View>
      </View>
      
      <View style={styles.body}>
        <Text style={styles.address} numberOfLines={1}>{p.situs_address}</Text>
        <View style={styles.metaRow}>
          <Text style={styles.meta}>
            {p.beds ? `${p.beds} bd` : "— bd"} · {p.baths ? `${p.baths} ba` : "— ba"} · {p.sqft ? `${p.sqft.toLocaleString()} sqft` : "— sqft"}
          </Text>
        </View>
        
        {/* Owner and distress badges */}
        <View style={styles.badgeRow}>
          <OwnerBadge type={p.owner_type} compact />
          
          {p.out_of_state_owner ? (
            <View style={[styles.miniBadge, { backgroundColor: colors.surfaceTertiary }]}>
              <Text style={styles.miniBadgeText}>OUT-OF-STATE</Text>
            </View>
          ) : null}
          
          {p.tax_delinquent ? (
            <View style={[styles.miniBadge, { backgroundColor: "#F1D9D5" }]}>
              <Text style={[styles.miniBadgeText, { color: "#7A2A24" }]}>TAX DELINQUENT</Text>
            </View>
          ) : null}
          
          {isVacant ? (
            <View style={[styles.miniBadge, { backgroundColor: "#F2E0BD" }]}>
              <Text style={[styles.miniBadgeText, { color: "#5A3F0E" }]}>VACANT</Text>
            </View>
          ) : null}
          
          {hasViolation ? (
            <View style={[styles.miniBadge, { backgroundColor: "#F1D9D5" }]}>
              <Text style={[styles.miniBadgeText, { color: "#7A2A24" }]}>
                CODE VIOLATION ({(p as any).violation_count})
              </Text>
            </View>
          ) : null}
        </View>
        
        {/* Stats row */}
        <View style={styles.statsRow}>
          <View style={styles.stat}>
            <Text style={styles.statLabel}>Investment</Text>
            <Text style={[styles.statValue, tabularNums]}>{p.investment_score ?? "—"}</Text>
          </View>
          <View style={styles.statDivider} />
          <View style={styles.stat}>
            <Text style={styles.statLabel}>Spread</Text>
            <Text style={[styles.statValue, tabularNums]}>{spread != null ? fmtMoney(spread) : "—"}</Text>
          </View>
          <View style={styles.statDivider} />
          <View style={styles.stat}>
            <Text style={styles.statLabel}>Risk</Text>
            <Text style={[styles.statValue, tabularNums]}>{p.risk_score ?? "—"}</Text>
          </View>
        </View>
      </View>
    </Pressable>
  );
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
  imageWrap: { height: 160, width: "100%" },
  image: { width: "100%", height: "100%" },
  scrim: { position: "absolute", left: 0, right: 0, bottom: 0, height: 90 },
  listingPill: {
    position: "absolute",
    top: 10,
    left: 10,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: radius.sm,
  },
  listingPillText: { color: "#fff", fontWeight: "800", fontSize: 10, letterSpacing: 0.6 },
  distressPill: {
    position: "absolute",
    top: 10,
    left: 90,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: radius.sm,
  },
  distressPillText: { color: "#fff", fontWeight: "800", fontSize: 10, letterSpacing: 0.6 },
  saveBtn: {
    position: "absolute",
    top: 10,
    right: 10,
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: "rgba(0,0,0,0.45)",
    alignItems: "center",
    justifyContent: "center",
  },
  priceRow: {
    position: "absolute",
    left: 12,
    right: 12,
    bottom: 10,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-end",
  },
  price: { color: "#fff", fontSize: 22, fontWeight: "800", letterSpacing: -0.5, ...tabularNums },
  roi: { color: "#fff", fontSize: 20, fontWeight: "800", ...tabularNums },
  priceLabel: { color: "rgba(255,255,255,0.75)", fontSize: 10, letterSpacing: 0.4, marginTop: 1 },
  body: { padding: spacing.md },
  address: { fontSize: 15, fontWeight: "700", color: colors.onSurface },
  metaRow: { marginTop: 2 },
  meta: { fontSize: 12, color: colors.muted, ...tabularNums },
  badgeRow: { flexDirection: "row", flexWrap: "wrap", marginTop: 8, gap: 6 },
  miniBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.sm,
  },
  miniBadgeText: { fontSize: 10, fontWeight: "700", color: colors.onSurfaceTertiary, letterSpacing: 0.3 },
  statsRow: {
    marginTop: spacing.md,
    flexDirection: "row",
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    paddingVertical: 8,
  },
  stat: { flex: 1, alignItems: "center" },
  statDivider: { width: 1, backgroundColor: colors.divider, marginVertical: 4 },
  statLabel: { fontSize: 10, color: colors.muted, letterSpacing: 0.4, fontWeight: "600" },
  statValue: { fontSize: 14, color: colors.onSurface, fontWeight: "800", marginTop: 2 },
});
