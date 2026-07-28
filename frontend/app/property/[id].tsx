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
  propertyImageUrl,
  propertyPhotoUrls,
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

function maybeMoney(value?: number | null): string {
  return typeof value === "number" ? `$${value.toLocaleString()}` : "Needs data";
}

function maybeDate(value?: string | null): string {
  if (!value) return "Needs data";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function ComparisonRow({
  label,
  tad,
  api,
  fmt,
  isString = false,
}: {
  label: string;
  tad: number | string;
  api: number | string;
  fmt: (v: number | string) => string;
  isString?: boolean;
}) {
  // Variance detection
  let variance = false;
  if (isString) {
    const a = String(tad).trim().toUpperCase();
    const b = String(api).trim().toUpperCase();
    variance = !!a && !!b && a !== b && !a.includes(b.split(",")[0]) && !b.includes(a.split(",")[0]);
  } else {
    const t = Number(tad) || 0;
    const r = Number(api) || 0;
    if (t > 0 && r > 0) {
      const diff = Math.abs(t - r) / Math.max(t, r);
      variance = diff > 0.1;
    } else if (t === 0 && r > 0) {
      variance = true; // TAD missing, API has data
    }
  }
  return (
    <View style={cmpStyles.row} testID={`cmp-${label.toLowerCase().replace(/\s/g, "-")}`}>
      <Text style={[cmpStyles.label, { flex: 1 }]}>{label}</Text>
      <Text style={[cmpStyles.value, { flex: 1.2, textAlign: "right" }, tabularNums]} numberOfLines={2}>
        {fmt(tad)}
      </Text>
      <View style={[cmpStyles.apiCell, { flex: 1.2 }]}>
        <Text
          style={[
            cmpStyles.value,
            { textAlign: "right", color: variance ? colors.error : colors.success },
            tabularNums,
          ]}
          numberOfLines={2}
        >
          {fmt(api)}
        </Text>
        {variance ? (
          <Ionicons name="warning" size={11} color={colors.warning} style={{ marginLeft: 4 }} />
        ) : null}
      </View>
    </View>
  );
}

const cmpStyles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.divider,
  },
  label: { fontSize: 12, fontWeight: "700", color: colors.onSurfaceTertiary },
  value: { fontSize: 13, fontWeight: "700", color: colors.onSurface },
  apiCell: { flexDirection: "row", alignItems: "center", justifyContent: "flex-end" },
});
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
  const [selectedPhoto, setSelectedPhoto] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [p, n, s] = await Promise.all([getProperty(id), getNearby(id), getSavedIds()]);
      setProp(p);
      setSelectedPhoto(null);
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
      .then(async (e) => {
        if (cancelled) return;
        setEnrich(e);
        // After enrichment, refetch property to reflect beds/baths/sqft/year_built updates
        if (e.found) {
          try {
            const fresh = await getProperty(id);
            if (!cancelled) setProp(fresh);
          } catch {}
        }
        try {
          const t = await getTaxHistory(id);
          if (!cancelled) setTaxHistory(t.tax_history || []);
        } catch {}
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setEnrichLoading(false); });
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

  const taxBase = prop.tax_roll_market_value || prop.assessed_value;
  const taxRate = prop.annual_taxes && taxBase
    ? `${((prop.annual_taxes / taxBase) * 100).toFixed(2)}%`
    : "Needs data";

  const beds = enrich?.beds ?? prop.beds;
  const baths = enrich?.baths ?? prop.baths;
  const sqft = enrich?.sqft ?? prop.sqft;
  const yearBuilt = enrich?.year_built ?? prop.year_built;
  const sourcePhotos = propertyPhotoUrls(prop);
  const photos = [
    ...(enrich?.hi_res_image ? [enrich.hi_res_image] : []),
    ...(enrich?.photos || []),
    ...sourcePhotos,
  ].filter((url, index, all) => url && all.indexOf(url) === index);
  const heroPhoto = selectedPhoto || photos[0] || propertyImageUrl(prop);
  const enrichedAddress = enrich?.found && enrich.rapidapi_address
    ? `${enrich.rapidapi_address}, ${enrich.rapidapi_city}, ${enrich.rapidapi_state} ${enrich.rapidapi_zip}`
    : null;

  return (
    <View style={styles.safe}>
      <ScrollView contentContainerStyle={{ paddingBottom: 120 }} testID="property-detail-scroll">
        {/* Hero */}
        <View style={styles.hero}>
          <Image source={heroPhoto ? { uri: heroPhoto } : undefined} style={styles.heroImg} contentFit="cover" />
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
            <View style={styles.listingChip}>
              <Text style={styles.listingChipText}>
                {(prop.opportunity_signals?.[0] || prop.listing_type).toUpperCase()}
              </Text>
            </View>
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

        {prop.opportunity_signals?.length ? (
          <View style={styles.section} testID="section-opportunity-signals">
            <Text style={styles.sectionTitle}>WHY THIS MATCHED</Text>
            <View style={styles.tagWrap}>
              {prop.opportunity_signals.map((signal) => (
                <View key={signal} style={[styles.featureTag, styles.opportunityTag]}>
                  <Text style={[styles.featureTagText, styles.opportunityTagText]}>{signal}</Text>
                </View>
              ))}
            </View>
            {prop.opportunity_evidence?.map((evidence) => (
              <View key={evidence} style={styles.opportunityEvidenceRow}>
                <Ionicons name="checkmark-circle" size={14} color={colors.success} />
                <Text style={styles.opportunityEvidenceText}>{evidence}</Text>
              </View>
            ))}
          </View>
        ) : null}

        {photos.length > 1 ? (
          <View style={styles.section} testID="section-photos">
            <Text style={styles.sectionTitle}>LISTING PHOTOS · {photos.length}</Text>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={styles.photoStrip}
            >
              {photos.map((photo, index) => (
                <Pressable
                  key={`${photo}-${index}`}
                  onPress={() => setSelectedPhoto(photo)}
                  style={[
                    styles.photoThumbWrap,
                    heroPhoto === photo && styles.photoThumbSelected,
                  ]}
                  testID={`photo-${index}`}
                >
                  <Image source={{ uri: photo }} style={styles.photoThumb} contentFit="cover" />
                </Pressable>
              ))}
            </ScrollView>
          </View>
        ) : null}

        <View style={styles.section} testID="section-listing-details">
          <Text style={styles.sectionTitle}>LISTING DETAILS</Text>
          <View style={styles.card}>
            <KeyValue
              k="Property Type"
              v={(prop.property_type || prop.home_type || "Needs data").replace(/_/g, " ")}
              mono={false}
            />
            <KeyValue k="Status" v={prop.listing_status || prop.listing_type || "Needs data"} mono={false} />
            <KeyValue
              k="Lot Size"
              v={prop.lot_size_sqft ? `${prop.lot_size_sqft.toLocaleString()} sqft` : "Needs data"}
            />
            <KeyValue k="Listed" v={maybeDate(prop.listing_date)} />
            <KeyValue k="MLS" v={prop.source_mls || "Needs data"} mono={false} />
            <KeyValue k="MLS ID" v={prop.mls_id || "Needs data"} />
            <KeyValue
              k="HOA"
              v={prop.hoa_fee != null ? `$${prop.hoa_fee.toLocaleString()}` : "Needs data"}
            />
            <KeyValue k="Listing Agent" v={prop.listing_agent_name || "Needs data"} mono={false} />
            <KeyValue k="Contact" v={prop.listing_agent_phone || "Needs data"} />
            {prop.listing_agent_email ? <KeyValue k="Agent Email" v={prop.listing_agent_email} mono={false} /> : null}
            {prop.listing_agent_rating != null ? (
              <KeyValue
                k="Agent Rating"
                v={`${prop.listing_agent_rating.toFixed(1)} · ${prop.listing_agent_review_count ?? 0} reviews`}
              />
            ) : null}
            <KeyValue k="Broker" v={prop.broker_name || "Needs data"} mono={false} />
            <KeyValue
              k="Coordinates"
              v={
                prop.latitude != null && prop.longitude != null
                  ? `${prop.latitude.toFixed(5)}, ${prop.longitude.toFixed(5)}`
                  : "Needs data"
              }
            />
          </View>
          {prop.listing_description ? (
            <View style={[styles.card, styles.descriptionCard]}>
              <Text style={styles.descriptionLabel}>PROPERTY DESCRIPTION</Text>
              <Text style={styles.descriptionText}>{prop.listing_description}</Text>
            </View>
          ) : null}
          {prop.listing_tags?.length ? (
            <View style={styles.tagWrap}>
              {prop.listing_tags.slice(0, 12).map((tag) => (
                <View key={tag} style={styles.featureTag}>
                  <Text style={styles.featureTagText}>{tag.replace(/_/g, " ")}</Text>
                </View>
              ))}
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

        {prop.agent_listings?.length ? (
          <View style={styles.section} testID="section-agent-listings">
            <Text style={styles.sectionTitle}>MORE LISTINGS BY THIS AGENT</Text>
            <View style={styles.card}>
              {prop.agent_listings.map((listing) => (
                <View key={listing.id} style={styles.agentListingRow}>
                  {listing.image_url ? (
                    <Image source={{ uri: listing.image_url }} style={styles.agentListingImage} contentFit="cover" />
                  ) : null}
                  <View style={{ flex: 1 }}>
                    <Text style={styles.agentListingAddress} numberOfLines={2}>
                      {listing.address || "Address unavailable"}
                    </Text>
                    <Text style={[styles.agentListingMeta, tabularNums]}>
                      {listing.price ? `$${listing.price.toLocaleString()}` : "Price unavailable"}
                      {listing.beds ? ` · ${listing.beds} bd` : ""}
                      {listing.baths ? ` · ${listing.baths} ba` : ""}
                    </Text>
                  </View>
                </View>
              ))}
            </View>
            <Text style={styles.sourceNote}>Source: {prop.agent_listings_source || "Realty in US"}</Text>
          </View>
        ) : null}

        {/* AI Deal Scoring */}
        <View style={styles.section} testID="section-scoring">
          <Text style={styles.sectionTitle}>AI DEAL SCORING</Text>
          <View style={styles.card}>
            <Text style={styles.sourceNote}>
              {prop.score_kind || "Preliminary screening"} · Confidence: {prop.score_confidence || "insufficient"}
            </Text>
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
                  <Text style={{ fontSize: 12, color: colors.muted, fontWeight: "600" }}>Value Spread</Text>
                  <Text style={[{ fontSize: 18, fontWeight: "800", color: colors.success }, tabularNums]}>
                    {prop.discount_to_benchmark_pct != null ? `${prop.discount_to_benchmark_pct.toFixed(1)}%` : "—"}
                  </Text>
                </View>
              </View>
            </View>
            {prop.score_missing_inputs?.length ? (
              <Text style={styles.sourceNote}>Needs: {prop.score_missing_inputs.join(" · ")}</Text>
            ) : null}
          </View>
        </View>

        {/* Tax Roll vs API Comparison */}
        {enrich?.found ? (
          <View style={styles.section} testID="section-compare">
            <Text style={styles.sectionTitle}>TAX ROLL vs LISTING API</Text>
            <View style={styles.card}>
              <View style={styles.cmpHeader}>
                <Text style={[styles.cmpHeaderCell, { flex: 1 }]}>FIELD</Text>
                <Text style={[styles.cmpHeaderCell, { flex: 1.2, textAlign: "right" }]}>TAX ROLL</Text>
                <Text style={[styles.cmpHeaderCell, { flex: 1.2, textAlign: "right" }]}>API</Text>
              </View>
              <ComparisonRow
                label="Beds"
                tad={prop.beds || 0}
                api={enrich.beds || 0}
                fmt={(v) => (v ? String(v) : "—")}
              />
              <ComparisonRow
                label="Baths"
                tad={prop.baths || 0}
                api={enrich.baths || 0}
                fmt={(v) => (v ? String(v) : "—")}
              />
              <ComparisonRow
                label="SqFt"
                tad={prop.sqft || 0}
                api={enrich.sqft || 0}
                fmt={(v) => (v ? Number(v).toLocaleString() : "—")}
              />
              <ComparisonRow
                label="Year Built"
                tad={prop.year_built || 0}
                api={enrich.year_built || 0}
                fmt={(v) => (v ? String(v) : "—")}
              />
              <ComparisonRow
                label="Assessed Value"
                tad={prop.assessed_value || 0}
                api={enrich.tax_assessed_value || 0}
                fmt={(v) => (v ? `$${Number(v).toLocaleString()}` : "—")}
              />
              <ComparisonRow
                label="Market Value"
                tad={prop.market_value || 0}
                api={enrich.zestimate || enrich.list_price || 0}
                fmt={(v) => (v ? `$${Number(v).toLocaleString()}` : "—")}
              />
              <ComparisonRow
                label="Address"
                tad={(prop.situs_address || "").replace(/, Tarrant County.*$/i, "")}
                api={enrichedAddress || enrich.rapidapi_address || ""}
                fmt={(v) => (v ? String(v) : "—")}
                isString
              />
            </View>
            <Text style={styles.sourceNote}>
              Variance flagged when tax roll &amp; API disagree by &gt;10% or address mismatch.
            </Text>
          </View>
        ) : null}

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
            <KeyValue k="Value Benchmark" v={maybeMoney(prop.value_benchmark)} />
            <KeyValue k="Benchmark Source" v={prop.value_benchmark_source || "Needs verified comps"} mono={false} />
            <KeyValue k="Tax-Roll Market Value" v={maybeMoney(prop.tax_roll_market_value)} />
            <KeyValue k="Assessed Value" v={maybeMoney(prop.assessed_value)} />
            <KeyValue k="Annual Taxes" v={maybeMoney(prop.annual_taxes)} />
            <KeyValue k="Effective Tax Rate" v={taxRate} />
            <KeyValue k="Value Spread" v={maybeMoney(prop.value_spread)} />
            <KeyValue k="Owner Equity" v={prop.equity_estimate != null ? maybeMoney(prop.equity_estimate) : "Unknown · mortgage required"} />
            <KeyValue k="Estimated ROI" v={prop.est_roi_pct != null ? `${prop.est_roi_pct.toFixed(1)}%` : "Unknown · full deal costs required"} />
            <KeyValue k="Legal Description" v={prop.legal_description} mono={false} />
            <KeyValue k="ZIP / County" v={`${prop.zip} · ${prop.county}`} />
            <KeyValue k="Tax Delinquent" v={prop.tax_delinquent ? "YES" : "NO"} />
            <KeyValue k="Vacant" v={prop.vacant ? "YES" : "NO"} />
            {enrich?.zestimate ? <KeyValue k="Zestimate" v={`$${enrich.zestimate.toLocaleString()}`} /> : null}
            {enrich?.rent_zestimate ? <KeyValue k="Rent Zestimate (mo)" v={`$${enrich.rent_zestimate.toLocaleString()}`} /> : null}
            {enrich?.tax_assessed_value ? <KeyValue k="Tax Assessed (Realtor)" v={`$${enrich.tax_assessed_value.toLocaleString()}`} /> : null}
            {enrich?.mls_id ? <KeyValue k="MLS ID" v={enrich.mls_id} /> : null}
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
                  <Image source={propertyImageUrl(n) ? { uri: propertyImageUrl(n) } : undefined} style={styles.nearbyImg} contentFit="cover" />
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
                  <Image source={propertyImageUrl(n) ? { uri: propertyImageUrl(n) } : undefined} style={styles.nearbyImg} contentFit="cover" />
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
  photoStrip: { gap: 10, paddingRight: spacing.lg },
  photoThumbWrap: {
    width: 112,
    height: 78,
    borderRadius: radius.md,
    overflow: "hidden",
    borderWidth: 2,
    borderColor: "transparent",
  },
  photoThumbSelected: { borderColor: colors.brandPrimary },
  photoThumb: { width: "100%", height: "100%" },
  descriptionCard: { marginTop: spacing.sm },
  descriptionLabel: {
    fontSize: 10,
    color: colors.muted,
    fontWeight: "800",
    letterSpacing: 0.8,
    marginBottom: 8,
  },
  descriptionText: {
    fontSize: 14,
    lineHeight: 21,
    color: colors.onSurface,
  },
  tagWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: spacing.sm,
  },
  featureTag: {
    backgroundColor: colors.surfaceTertiary,
    borderRadius: radius.pill,
    paddingHorizontal: 9,
    paddingVertical: 5,
  },
  featureTagText: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    fontWeight: "700",
  },
  opportunityTag: { backgroundColor: "#E7DDD0" },
  opportunityTagText: { color: "#5C3C17" },
  opportunityEvidenceRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
    marginTop: 8,
  },
  opportunityEvidenceText: {
    flex: 1,
    color: colors.onSurface,
    fontSize: 12,
    lineHeight: 17,
  },
  agentListingRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 9,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.divider,
  },
  agentListingImage: { width: 58, height: 44, borderRadius: radius.sm },
  agentListingAddress: { color: colors.onSurface, fontSize: 13, fontWeight: "700" },
  agentListingMeta: { color: colors.muted, fontSize: 11, marginTop: 3 },
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

  cmpHeader: {
    flexDirection: "row",
    paddingBottom: 8,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
    marginBottom: 4,
  },
  cmpHeaderCell: { fontSize: 10, fontWeight: "800", color: colors.muted, letterSpacing: 0.5 },

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
