import { useEffect, useState, useCallback } from "react";
import { View, Text, StyleSheet, ScrollView, ActivityIndicator, Pressable, Dimensions } from "react-native";
import { Image } from "expo-image";
import { LinearGradient } from "expo-linear-gradient";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import {
  getProperty,
  getNearby,
  getAIAnalysis,
  getSavedIds,
  saveProperty,
  unsaveProperty,
  enrichProperty,
  getTaxHistory,
  type Property,
  type Enrichment,
  type TaxHistoryEntry,
} from "@/src/lib/api";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";
import { OwnerBadge } from "@/src/components/OwnerBadge";
import { ScoreBar } from "@/src/components/ScoreBar";
import { fmtMoney } from "@/src/components/PropertyCard";

const W = Dimensions.get("window").width;

function KeyValue({ k, v, mono = true }: { k: string; v: string; mono?: boolean }) {
  return (
    <View style={kvStyles.row}>
      <Text style={kvStyles.k}>{k}</Text>
      <Text style={[kvStyles.v, mono && tabularNums]} numberOfLines={2}>{v}</Text>
    </View>
  );
}
const kvStyles = StyleSheet.create({
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.divider,
    gap: 12,
  },
  k: { fontSize: 12, color: colors.muted, fontWeight: "700", letterSpacing: 0.3, flexShrink: 0 },
  v: { fontSize: 14, color: colors.onSurface, fontWeight: "700", flexShrink: 1, textAlign: "right" },
});

export default function PropertyDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [prop, setProp] = useState<Property | null>(null);
  const [nearby, setNearby] = useState<{ nearby_foreclosures: any[]; nearby_investor_purchases: any[] } | null>(null);
  const [ai, setAi] = useState<string | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [enrich, setEnrich] = useState<Enrichment | null>(null);
  const [enrichLoading, setEnrichLoading] = useState(false);
  const [taxHistory, setTaxHistory] = useState<TaxHistoryEntry[]>([]);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [p, n, s] = await Promise.all([getProperty(id), getNearby(id), getSavedIds()]);
      setProp(p);
      setNearby(n);
      setSaved(s.ids.includes(id));
    } catch (e) {
      setError("Unable to load property.");
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  // Auto-enrich on mount (best-effort, non-blocking)
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setEnrichLoading(true);
    enrichProperty(id)
      .then((e) => { if (!cancelled) setEnrich(e); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setEnrichLoading(false); });
    getTaxHistory(id)
      .then((t) => { if (!cancelled) setTaxHistory(t.tax_history || []); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [id]);

  const runAI = async () => {
    if (!id) return;
    setAiLoading(true);
    try {
      const res = await getAIAnalysis(id);
      setAi(res.narrative);
    } catch {
      setAi("Could not generate analysis. Please retry.");
    } finally {
      setAiLoading(false);
    }
  };

  const toggleSave = async () => {
    if (!id) return;
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    if (saved) { setSaved(false); try { await unsaveProperty(id); } catch {} }
    else { setSaved(true); try { await saveProperty(id); } catch {} }
  };

  if (error) {
    return (
      <SafeAreaView style={styles.safe}>
        <View style={styles.center}><Text style={{ color: colors.error }}>{error}</Text></View>
      </SafeAreaView>
    );
  }
  if (!prop) {
    return (
      <SafeAreaView style={styles.safe}>
        <View style={styles.center}><ActivityIndicator color={colors.brandPrimary} /></View>
      </SafeAreaView>
    );
  }

  const taxRate = ((prop.annual_taxes / Math.max(prop.assessed_value, 1)) * 100).toFixed(2);
  const equityPct = ((prop.equity_estimate / Math.max(prop.market_value, 1)) * 100).toFixed(1);

  const beds = enrich?.beds ?? prop.beds;
  const baths = enrich?.baths ?? prop.baths;
  const sqft = enrich?.sqft ?? prop.sqft;
  const yearBuilt = enrich?.year_built ?? prop.year_built;
  const heroPhoto = enrich?.hi_res_image || (enrich?.photos && enrich.photos[0]) || prop.image_url;
  const enrichedAddress = enrich?.found && enrich.rapidapi_address
    ? `${enrich.rapidapi_address}, ${enrich.rapidapi_city}, ${enrich.rapidapi_state} ${enrich.rapidapi_zip}`
    : null;

  return (
    <View style={styles.safe}>
      <ScrollView contentContainerStyle={{ paddingBottom: 120 }} testID="property-detail-scroll">
        {/* Hero */}
        <View style={styles.hero}>
          <Image source={{ uri: heroPhoto }} style={styles.heroImg} contentFit="cover" />
          <LinearGradient colors={["rgba(0,0,0,0.55)", "transparent"]} style={styles.heroTopScrim} pointerEvents="none" />
          <LinearGradient colors={["transparent", "rgba(26,28,26,0.85)"]} style={styles.heroBottomScrim} pointerEvents="none" />
          <SafeAreaView edges={["top"]} style={styles.heroNav}>
            <Pressable testID="back-button" onPress={() => router.back()} style={styles.iconBtn}>
              <Ionicons name="chevron-back" size={20} color="#fff" />
            </Pressable>
            <Pressable testID="hero-save" onPress={toggleSave} style={styles.iconBtn}>
              <Ionicons name={saved ? "bookmark" : "bookmark-outline"} size={18} color="#fff" />
            </Pressable>
          </SafeAreaView>
          <View style={styles.heroMeta}>
            <View style={styles.listingChip}><Text style={styles.listingChipText}>{prop.listing_type.toUpperCase()}</Text></View>
            <Text style={styles.heroPrice}>{fmtMoney(prop.price)}</Text>
            <Text style={styles.heroAddress} numberOfLines={2}>{prop.situs_address}</Text>
          </View>
        </View>

        {/* Quick stats */}
        <View style={styles.section}>
          <View style={styles.quickRow}>
            <View style={styles.quick}><Text style={styles.quickLabel}>Beds</Text><Text style={[styles.quickValue, tabularNums]}>{beds || "—"}</Text></View>
            <View style={styles.quick}><Text style={styles.quickLabel}>Baths</Text><Text style={[styles.quickValue, tabularNums]}>{baths || "—"}</Text></View>
            <View style={styles.quick}><Text style={styles.quickLabel}>SqFt</Text><Text style={[styles.quickValue, tabularNums]}>{sqft ? sqft.toLocaleString() : "—"}</Text></View>
            <View style={styles.quick}><Text style={styles.quickLabel}>Built</Text><Text style={[styles.quickValue, tabularNums]}>{yearBuilt || "—"}</Text></View>
          </View>
          {enrichLoading && !enrich ? (
            <Text style={styles.enrichNote} testID="enrich-loading">Pulling live listing data…</Text>
          ) : enrich?.found ? (
            <View style={styles.enrichTag} testID="enrich-tag">
              <Ionicons name="checkmark-circle" size={12} color={colors.success} />
              <Text style={styles.enrichTagText}>
                Enriched · {enrich.home_type?.replace(/_/g, " ") || "Listing"}
                {enrich.list_price ? ` · List $${enrich.list_price.toLocaleString()}` : ""}
              </Text>
            </View>
          ) : null}
        </View>

        {/* Owner Intelligence */}
        <View style={styles.section} testID="section-owner">
          <Text style={styles.sectionTitle}>OWNER INTELLIGENCE</Text>
          <View style={styles.card}>
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <Text style={styles.ownerName} numberOfLines={2}>{prop.owner_name}</Text>
              <OwnerBadge type={prop.owner_type} testID="owner-badge" />
            </View>
            <KeyValue k="Mailing" v={prop.owner_mailing_address} mono={false} />
            <KeyValue k="Situs" v={prop.situs_address} mono={false} />
            <KeyValue k="Out-of-State" v={prop.out_of_state_owner ? "YES" : "NO"} />
            <KeyValue k="Investor-Owned" v={prop.investor_owned ? "YES" : "NO"} />
            <KeyValue k="Cash Buyer" v={prop.cash_buyer ? "YES" : "NO"} />
          </View>
        </View>

        {/* AI Deal Scoring */}
        <View style={styles.section} testID="section-scoring">
          <Text style={styles.sectionTitle}>AI DEAL SCORING</Text>
          <View style={styles.card}>
            <View style={styles.scoreGrid}>
              <View style={styles.scoreCol}>
                <ScoreBar testID="score-investment" label="Investment" value={prop.investment_score} tone="success" />
                <ScoreBar testID="score-wholesale" label="Wholesale" value={prop.wholesale_score} />
                <ScoreBar testID="score-flip" label="Flip" value={prop.flip_score} tone="warning" />
              </View>
              <View style={styles.scoreCol}>
                <ScoreBar testID="score-rental" label="Rental" value={prop.rental_score} />
                <ScoreBar testID="score-risk" label="Risk" value={prop.risk_score} tone="error" />
                <View style={{ marginBottom: spacing.md }}>
                  <Text style={{ fontSize: 12, color: colors.muted, fontWeight: "600" }}>Equity</Text>
                  <Text style={[{ fontSize: 18, fontWeight: "800", color: colors.success }, tabularNums]}>
                    {equityPct}%
                  </Text>
                </View>
              </View>
            </View>
          </View>
        </View>

        {/* AI Investment Analysis */}
        <View style={styles.section} testID="section-ai">
          <Text style={styles.sectionTitle}>AI INVESTMENT ANALYSIS</Text>
          <View style={styles.card}>
            {ai ? (
              <Text style={styles.aiText} testID="ai-narrative">{ai}</Text>
            ) : aiLoading ? (
              <View style={{ alignItems: "center", paddingVertical: 16 }}>
                <ActivityIndicator color={colors.brandPrimary} />
                <Text style={{ color: colors.muted, marginTop: 8, fontSize: 12 }}>Generating analysis with Claude…</Text>
              </View>
            ) : (
              <Pressable testID="ai-analyze-btn" onPress={runAI} style={styles.aiBtn}>
                <Ionicons name="sparkles" size={16} color={colors.onBrandPrimary} />
                <Text style={styles.aiBtnText}>Run AI Analysis</Text>
              </Pressable>
            )}
          </View>
        </View>

        {/* Financials */}
        <View style={styles.section} testID="section-financials">
          <Text style={styles.sectionTitle}>FINANCIALS · TAX ROLL</Text>
          <View style={styles.card}>
            <KeyValue k="Asking Price" v={`$${prop.price.toLocaleString()}`} />
            <KeyValue k="Market Value" v={`$${prop.market_value.toLocaleString()}`} />
            <KeyValue k="Assessed Value" v={`$${prop.assessed_value.toLocaleString()}`} />
            <KeyValue k="Annual Taxes" v={`$${prop.annual_taxes.toLocaleString()}`} />
            <KeyValue k="Effective Tax Rate" v={`${taxRate}%`} />
            <KeyValue k="Equity Estimate" v={`$${prop.equity_estimate.toLocaleString()}`} />
            <KeyValue k="Est ROI" v={`${prop.est_roi_pct.toFixed(1)}%`} />
            <KeyValue k="Legal Description" v={prop.legal_description} mono={false} />
            <KeyValue k="ZIP / County" v={`${prop.zip} · ${prop.county}`} />
            <KeyValue k="Tax Delinquent" v={prop.tax_delinquent ? "YES" : "NO"} />
            <KeyValue k="Vacant" v={prop.vacant ? "YES" : "NO"} />
            {enrich?.parcel_id ? <KeyValue k="Parcel ID (Realtor)" v={enrich.parcel_id} /> : null}
            {enrichedAddress ? <KeyValue k="Matched Listing" v={enrichedAddress} mono={false} /> : null}
          </View>
          <Text style={styles.sourceNote}>Source: {prop.data_source}</Text>
        </View>

        {/* Tax History (RapidAPI) */}
        {taxHistory.length > 0 ? (
          <View style={styles.section} testID="section-tax-history">
            <Text style={styles.sectionTitle}>TAX HISTORY · LAST {Math.min(taxHistory.length, 6)} YEARS</Text>
            <View style={styles.card}>
              <View style={styles.thHeader}>
                <Text style={[styles.thCell, { flex: 0.6 }]}>YEAR</Text>
                <Text style={[styles.thCell, { flex: 1, textAlign: "right" }]}>TAX</Text>
                <Text style={[styles.thCell, { flex: 1.2, textAlign: "right" }]}>ASSESSED</Text>
                <Text style={[styles.thCell, { flex: 1.2, textAlign: "right" }]}>MARKET</Text>
              </View>
              {taxHistory.slice(0, 6).map((t) => (
                <View key={t.year} style={styles.thRow}>
                  <Text style={[styles.thRowText, { flex: 0.6 }, tabularNums]}>{t.year}</Text>
                  <Text style={[styles.thRowText, { flex: 1, textAlign: "right" }, tabularNums]}>${(t.tax || 0).toLocaleString()}</Text>
                  <Text style={[styles.thRowText, { flex: 1.2, textAlign: "right" }, tabularNums]}>${((t.assessment?.total) || 0).toLocaleString()}</Text>
                  <Text style={[styles.thRowText, { flex: 1.2, textAlign: "right" }, tabularNums]}>${((t.market?.total) || 0).toLocaleString()}</Text>
                </View>
              ))}
            </View>
            <Text style={styles.sourceNote}>Source: Realtor.com via US Real Estate Listings API</Text>
          </View>
        ) : null}

        {/* Nearby */}
        {nearby ? (
          <View style={styles.section} testID="section-nearby">
            <Text style={styles.sectionTitle}>NEARBY IN {prop.zip}</Text>
            <Text style={styles.subTitle}>Foreclosures</Text>
            {nearby.nearby_foreclosures.length === 0 ? (
              <Text style={styles.muted}>None in this ZIP.</Text>
            ) : (
              <View>
                {nearby.nearby_foreclosures.map((n) => (
                  <Pressable key={n.id} onPress={() => router.push(`/property/${n.id}`)} style={styles.nearbyRow} testID={`nearby-fc-${n.id}`}>
                    <Image source={{ uri: n.image_url }} style={styles.nearbyImg} contentFit="cover" />
                    <View style={{ flex: 1, marginLeft: 10 }}>
                      <Text style={styles.nearbyAddr} numberOfLines={1}>{n.situs_address}</Text>
                      <Text style={[styles.nearbyMeta, tabularNums]}>{n.listing_type} · ${n.price.toLocaleString()}</Text>
                    </View>
                    <Ionicons name="chevron-forward" size={18} color={colors.muted} />
                  </Pressable>
                ))}
              </View>
            )}
            <Text style={[styles.subTitle, { marginTop: spacing.md }]}>Investor Purchases</Text>
            {nearby.nearby_investor_purchases.length === 0 ? (
              <Text style={styles.muted}>None in this ZIP.</Text>
            ) : (
              <View>
                {nearby.nearby_investor_purchases.map((n) => (
                  <Pressable key={n.id} onPress={() => router.push(`/property/${n.id}`)} style={styles.nearbyRow} testID={`nearby-inv-${n.id}`}>
                    <Image source={{ uri: n.image_url }} style={styles.nearbyImg} contentFit="cover" />
                    <View style={{ flex: 1, marginLeft: 10 }}>
                      <Text style={styles.nearbyAddr} numberOfLines={1}>{n.situs_address}</Text>
                      <Text style={[styles.nearbyMeta, tabularNums]}>{n.owner_type} · ${n.price.toLocaleString()}</Text>
                    </View>
                    <Ionicons name="chevron-forward" size={18} color={colors.muted} />
                  </Pressable>
                ))}
              </View>
            )}
          </View>
        ) : null}
      </ScrollView>

      {/* Sticky CTA */}
      <View style={styles.ctaBar}>
        <Pressable testID="cta-save" onPress={toggleSave} style={[styles.ctaSecondary]}>
          <Ionicons name={saved ? "bookmark" : "bookmark-outline"} size={18} color={colors.onSurface} />
          <Text style={styles.ctaSecondaryText}>{saved ? "Saved" : "Save Deal"}</Text>
        </Pressable>
        <Pressable testID="cta-contact" style={styles.ctaPrimary}>
          <Ionicons name="call" size={16} color={colors.onBrandPrimary} />
          <Text style={styles.ctaPrimaryText}>Contact Owner</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  hero: { width: W, height: 320 },
  heroImg: { width: "100%", height: "100%" },
  heroTopScrim: { position: "absolute", top: 0, left: 0, right: 0, height: 120 },
  heroBottomScrim: { position: "absolute", left: 0, right: 0, bottom: 0, height: 180 },
  heroNav: {
    position: "absolute", top: 0, left: 0, right: 0,
    flexDirection: "row", justifyContent: "space-between", paddingHorizontal: spacing.lg,
  },
  iconBtn: {
    width: 36, height: 36, borderRadius: 18,
    backgroundColor: "rgba(0,0,0,0.45)",
    alignItems: "center", justifyContent: "center",
    marginTop: 8,
  },
  heroMeta: { position: "absolute", left: spacing.lg, right: spacing.lg, bottom: spacing.lg },
  listingChip: {
    alignSelf: "flex-start",
    backgroundColor: colors.brandSecondary,
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: radius.sm, marginBottom: 8,
  },
  listingChipText: { color: "#fff", fontSize: 11, fontWeight: "800", letterSpacing: 0.8 },
  heroPrice: { color: "#fff", fontSize: 34, fontWeight: "800", letterSpacing: -0.8, ...tabularNums },
  heroAddress: { color: "rgba(255,255,255,0.92)", fontSize: 15, marginTop: 4, fontWeight: "600" },

  section: { paddingHorizontal: spacing.lg, paddingTop: spacing.lg },
  sectionTitle: { fontSize: 11, fontWeight: "800", color: colors.muted, letterSpacing: 1.2, marginBottom: spacing.sm },
  subTitle: { fontSize: 12, fontWeight: "800", color: colors.onSurfaceTertiary, marginTop: spacing.sm, marginBottom: 6, letterSpacing: 0.4 },
  card: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.lg,
    borderWidth: 1,
    borderColor: colors.border,
  },
  ownerName: { fontSize: 16, fontWeight: "800", color: colors.onSurface, flex: 1, marginRight: 12 },

  quickRow: {
    flexDirection: "row",
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    borderWidth: 1, borderColor: colors.border,
    paddingVertical: 12,
  },
  quick: { flex: 1, alignItems: "center" },
  quickLabel: { fontSize: 10, color: colors.muted, fontWeight: "700", letterSpacing: 0.6 },
  quickValue: { fontSize: 18, color: colors.onSurface, fontWeight: "800", marginTop: 2 },

  scoreGrid: { flexDirection: "row", gap: spacing.lg },
  scoreCol: { flex: 1 },

  aiText: { fontSize: 14, color: colors.onSurface, lineHeight: 22 },
  aiBtn: {
    backgroundColor: colors.brandPrimary,
    paddingVertical: 12, borderRadius: radius.md,
    alignItems: "center", justifyContent: "center",
    flexDirection: "row", gap: 8,
  },
  aiBtnText: { color: colors.onBrandPrimary, fontSize: 14, fontWeight: "800", letterSpacing: 0.3 },

  sourceNote: { fontSize: 10, color: colors.muted, marginTop: 6, fontStyle: "italic" },

  enrichNote: { fontSize: 11, color: colors.muted, marginTop: 6, textAlign: "center" },
  enrichTag: {
    flexDirection: "row", alignItems: "center", gap: 6,
    marginTop: 8, alignSelf: "center",
    backgroundColor: "#E3EBE5",
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12,
  },
  enrichTagText: { fontSize: 11, color: colors.success, fontWeight: "700" },

  thHeader: {
    flexDirection: "row",
    paddingBottom: 8,
    borderBottomWidth: 1, borderBottomColor: colors.divider,
  },
  thCell: { fontSize: 10, fontWeight: "800", color: colors.muted, letterSpacing: 0.4 },
  thRow: {
    flexDirection: "row",
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.divider,
  },
  thRowText: { fontSize: 12, color: colors.onSurface, fontWeight: "700" },

  nearbyRow: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.border,
    padding: 8, marginBottom: 6,
  },
  nearbyImg: { width: 52, height: 52, borderRadius: radius.sm, backgroundColor: colors.surfaceTertiary },
  nearbyAddr: { fontSize: 13, fontWeight: "700", color: colors.onSurface },
  nearbyMeta: { fontSize: 11, color: colors.muted, marginTop: 2 },
  muted: { color: colors.muted, fontSize: 12 },

  ctaBar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    flexDirection: "row", gap: 8,
    paddingHorizontal: spacing.lg, paddingTop: 10, paddingBottom: 24,
    backgroundColor: colors.surfaceSecondary,
    borderTopWidth: 1, borderTopColor: colors.border,
  },
  ctaSecondary: {
    flex: 1, height: 48, borderRadius: radius.md,
    backgroundColor: colors.surfaceTertiary,
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
  },
  ctaSecondaryText: { color: colors.onSurface, fontWeight: "800" },
  ctaPrimary: {
    flex: 1.4, height: 48, borderRadius: radius.md,
    backgroundColor: colors.brandPrimary,
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
  },
  ctaPrimaryText: { color: colors.onBrandPrimary, fontWeight: "800" },
});
