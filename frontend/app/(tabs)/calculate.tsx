import React, { useState, useCallback, useRef } from "react";
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  ScrollView,
  Pressable,
  ActivityIndicator,
  Keyboard,
  KeyboardAvoidingView,
  Platform,
  Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";
import { API_BASE } from "@/src/lib/api";

interface AddressSuggestion {
  address: string;
  city?: string;
  state?: string;
  zip?: string;
  zpid?: string;
  price?: number;
  beds?: number;
  baths?: number;
  sqft?: number;
  listing_type?: string;
  source?: string;
}

interface AnalysisResult {
  decision: string;
  listing_price: number;
  arv: number;
  repairs: number;
  rent: number;
  max_offer: number;
  estimated_profit: number;
  roi_pct: number;
  warning?: string;
}

type Step = "address" | "form" | "result";

export default function CalculateScreen() {
  const [step, setStep] = useState<Step>("address");
  const [addressInput, setAddressInput] = useState("");
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([]);
  const [suggesting, setSuggesting] = useState(false);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  const [selectedProperty, setSelectedProperty] = useState<AddressSuggestion | null>(null);
  
  // Form state
  const [purchasePrice, setPurchasePrice] = useState("");
  const [arv, setArv] = useState("");
  const [repairs, setRepairs] = useState("");
  const [beds, setBeds] = useState("");
  const [baths, setBaths] = useState("");
  const [sqft, setSqft] = useState("");
  const [monthlyRent, setMonthlyRent] = useState("");
  const [holdingMonths, setHoldingMonths] = useState("6");
  
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  
  const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ─── Address Search ─────────────────────────────────────────────────────────
  const searchAddress = useCallback(async (query: string) => {
    if (query.length < 5) {
      setSuggestions([]);
      setSuggesting(false);
      return;
    }
    setSuggesting(true);
    setLoadingSuggestions(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/address-suggestions?query=${encodeURIComponent(query)}&search_type=address`
      );
      const data = await res.json();
      const items: AddressSuggestion[] = data.items || [];
      // Deduplicate by address
      const seen = new Set<string>();
      const unique = items.filter((p) => {
        const key = (p.situs_address || "").toUpperCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      }).slice(0, 6);
      setSuggestions(unique);
    } catch {
      setSuggestions([]);
    } finally {
      setLoadingSuggestions(false);
    }
  }, []);

  const handleAddressChange = (text: string) => {
    setAddressInput(text);
    setStep("address");
    setResult(null);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    searchTimeout.current = setTimeout(() => searchAddress(text), 400);
  };

  const selectProperty = (prop: AddressSuggestion) => {
    setSelectedProperty(prop);
    const fullAddress = [prop.address, prop.city, prop.state, prop.zip].filter(Boolean).join(", ");
    setAddressInput(fullAddress || prop.address || "");
    setSuggestions([]);
    setSuggesting(false);
    
    // Auto-fill ARV from listing price (best available estimate)
    if (prop.price) setArv(String(prop.price));
    
    // Auto-fill beds/baths/sqft if available
    if (prop.beds) setBeds(String(prop.beds));
    if (prop.baths) setBaths(String(prop.baths));
    if (prop.sqft) setSqft(String(prop.sqft));
    
    setStep("form");
    Keyboard.dismiss();
  };

  const handleAnalyze = async () => {
    const price = parseFloat(purchasePrice);
    const arvVal = parseFloat(arv);
    const repairsVal = parseFloat(repairs) || 0;
    const rentVal = parseFloat(monthlyRent) || 0;
    
    if (!price || price <= 0) {
      Alert.alert("Missing Price", "Enter a purchase price to analyze the deal.");
      return;
    }
    if (!arvVal || arvVal <= 0) {
      Alert.alert("Missing ARV", "Enter an After Repair Value (ARV) to analyze the deal.");
      return;
    }
    
    setAnalyzing(true);
    setAnalysisError(null);
    setResult(null);
    
    try {
      const res = await fetch(`${API_BASE}/api/analyze/quick`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          address: addressInput,
          price,
          arv: arvVal,
          repairs: repairsVal,
          rent: rentVal,
        }),
      });
      
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json();
      setResult(data);
      setStep("result");
    } catch (err: any) {
      setAnalysisError(err.message || "Analysis failed. Try again.");
    } finally {
      setAnalyzing(false);
    }
  };

  const reset = () => {
    setStep("address");
    setAddressInput("");
    setSuggestions([]);
    setSelectedProperty(null);
    setPurchasePrice("");
    setArv("");
    setRepairs("");
    setMonthlyRent("");
    setResult(null);
    setAnalysisError(null);
  };

  // ─── Score Color ────────────────────────────────────────────────────────────
  const scoreColor = (decision: string) => {
    if (decision === "BUY") return colors.success;
    if (decision === "NEGOTIATE") return colors.warning;
    return colors.error;
  };

  const scoreEmoji = (decision: string) => {
    if (decision === "BUY") return "✅";
    if (decision === "NEGOTIATE") return "🤝";
    return "🚫";
  };

  // ─── Render ─────────────────────────────────────────────────────────────────
  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={{ flex: 1 }}
      >
        <ScrollView
          contentContainerStyle={styles.scroll}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {/* Header */}
          <View style={styles.header}>
            <Text style={styles.headerTitle}>🏠 Deal Calculator</Text>
            <Text style={styles.headerSub}>
              Enter any Fort Worth address to auto-fill property data
            </Text>
          </View>

          {/* ─── STEP 1: Address Search ─────────────────────────────── */}
          <View style={styles.searchSection}>
            <View style={styles.searchWrap}>
              <Ionicons name="search" size={18} color={colors.muted} style={styles.searchIcon} />
              <TextInput
                style={styles.searchInput}
                placeholder="123 Main St, Fort Worth TX"
                placeholderTextColor={colors.muted}
                value={addressInput}
                onChangeText={handleAddressChange}
                autoCapitalize="words"
                autoCorrect={false}
                returnKeyType="search"
              />
              {addressInput.length > 0 && (
                <Pressable onPress={reset} hitSlop={8}>
                  <Ionicons name="close-circle" size={18} color={colors.muted} />
                </Pressable>
              )}
            </View>

            {/* Suggestions */}
            {(suggesting && suggestions.length > 0) && (
              <View style={styles.suggestionsBox}>
                {loadingSuggestions ? (
                  <ActivityIndicator size="small" color={colors.brandPrimary} style={{ padding: 12 }} />
                ) : (
                  suggestions.map((prop, i) => (
                    <Pressable
                      key={i}
                      style={({ pressed }) => [
                        styles.suggestionItem,
                        pressed && styles.suggestionItemPressed,
                      ]}
                      onPress={() => selectProperty(prop)}
                    >
                      <View style={styles.suggestionIconWrap}>
                        <Ionicons name="location" size={16} color={colors.brandPrimary} />
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.suggestionAddress}>{prop.situs_address}</Text>
                        <View style={styles.suggestionMeta}>
                          {prop.assessed_value && (
                            <Text style={styles.suggestionMetaText}>
                              💰 {prop.assessed_value.toLocaleString()}
                            </Text>
                          )}
                          {prop.beds && (
                            <Text style={styles.suggestionMetaText}>
                              🛏 {prop.beds}bd
                            </Text>
                          )}
                          {prop.sqft && (
                            <Text style={styles.suggestionMetaText}>
                              📐 {prop.sqft.toLocaleString()}sf
                            </Text>
                          )}
                        </View>
                      </View>
                      <Ionicons name="chevron-forward" size={14} color={colors.muted} />
                    </Pressable>
                  ))
                )}
              </View>
            )}

            {suggesting && suggestions.length === 0 && !loadingSuggestions && addressInput.length >= 5 && (
              <View style={styles.noSuggestions}>
                <Text style={styles.noSuggestionsText}>
                  No TAD records found for "{addressInput}"
                </Text>
                <Text style={styles.noSuggestionsSub}>
                  Enter price + ARV manually below
                </Text>
              </View>
            )}
          </View>

          {/* ─── STEP 2: Property Info (if found) ──────────────────── */}
          {step !== "address" && selectedProperty && (
            <View style={styles.propertyCard}>
              <View style={styles.propertyCardHeader}>
                <Text style={styles.propertyCardTitle}>📍 Property Found</Text>
                <Pressable onPress={reset}>
                  <Text style={styles.changeLink}>Change</Text>
                </Pressable>
              </View>
              <View style={styles.propertyCardBody}>
                <Text style={styles.propertyAddress}>{[selectedProperty.address, selectedProperty.city].filter(Boolean).join(", ")}</Text>
                {selectedProperty.listing_type && (
                  <Text style={styles.propertyOwner}>
                    🏷️ {selectedProperty.listing_type || 'For Sale'}
                  </Text>
                )}
                <View style={styles.propertyStats}>
                  {selectedProperty.beds && (
                    <View style={styles.statPill}><Text style={styles.statPillText}>🛏 {selectedProperty.beds} bd</Text></View>
                  )}
                  {selectedProperty.baths && (
                    <View style={styles.statPill}><Text style={styles.statPillText}>🚿 {selectedProperty.baths} ba</Text></View>
                  )}
                  {selectedProperty.sqft && (
                    <View style={styles.statPill}><Text style={styles.statPillText}>📐 {selectedProperty.sqft.toLocaleString()} sf</Text></View>
                  )}
                  {selectedProperty.price && (
                    <View style={[styles.statPill, styles.statPillHighlight]}>
                      <Text style={styles.statPillHighlightText}>
                        💰 ${selectedProperty.price.toLocaleString()}
                      </Text>
                    </View>
                  )}
                </View>
              </View>
            </View>
          )}

          {/* ─── STEP 3: Input Form ─────────────────────────────────── */}
          <View style={styles.formSection}>
            <Text style={styles.formTitle}>💡 Enter Your Deal Numbers</Text>
            
            <View style={styles.inputGroup}>
              <Text style={styles.inputLabel}>Purchase Price *</Text>
              <View style={styles.inputWrap}>
                <Text style={styles.inputPrefix}>$</Text>
                <TextInput
                  style={styles.input}
                  placeholder="150000"
                  placeholderTextColor={colors.muted}
                  value={purchasePrice}
                  onChangeText={setPurchasePrice}
                  keyboardType="numeric"
                  returnKeyType="next"
                />
              </View>
            </View>

            <View style={styles.inputGroup}>
              <Text style={styles.inputLabel}>
                After Repair Value (ARV) *
                {selectedProperty && " (auto-filled from TAD)"}
              </Text>
              <View style={styles.inputWrap}>
                <Text style={styles.inputPrefix}>$</Text>
                <TextInput
                  style={[styles.input, selectedProperty && styles.inputPrefilled]}
                  placeholder="285000"
                  placeholderTextColor={colors.muted}
                  value={arv}
                  onChangeText={setArv}
                  keyboardType="numeric"
                  returnKeyType="next"
                />
              </View>
            </View>

            <View style={styles.inputGroup}>
              <Text style={styles.inputLabel}>Estimated Repairs</Text>
              <View style={styles.inputWrap}>
                <Text style={styles.inputPrefix}>$</Text>
                <TextInput
                  style={styles.input}
                  placeholder="25000"
                  placeholderTextColor={colors.muted}
                  value={repairs}
                  onChangeText={setRepairs}
                  keyboardType="numeric"
                  returnKeyType="next"
                />
              </View>
              <Text style={styles.inputHint}>
                Leave blank if buying as-is
              </Text>
            </View>

            <View style={styles.inputGroup}>
              <Text style={styles.inputLabel}>Monthly Rent (for ROI calc)</Text>
              <View style={styles.inputWrap}>
                <Text style={styles.inputPrefix}>$</Text>
                <TextInput
                  style={styles.input}
                  placeholder="1800"
                  placeholderTextColor={colors.muted}
                  value={monthlyRent}
                  onChangeText={setMonthlyRent}
                  keyboardType="numeric"
                  returnKeyType="done"
                />
                <Text style={styles.inputSuffix}>/mo</Text>
              </View>
            </View>

            {/* Analyze Button */}
            <Pressable
              style={({ pressed }) => [
                styles.analyzeBtn,
                pressed && styles.analyzeBtnPressed,
                analyzing && styles.analyzeBtnLoading,
              ]}
              onPress={handleAnalyze}
              disabled={analyzing}
            >
              {analyzing ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <>
                  <Ionicons name="calculator" size={18} color="#fff" />
                  <Text style={styles.analyzeBtnText}>ANALYZE DEAL</Text>
                </>
              )}
            </Pressable>

            {analysisError && (
              <View style={styles.errorBox}>
                <Ionicons name="warning" size={14} color={colors.error} />
                <Text style={styles.errorText}>{analysisError}</Text>
              </View>
            )}
          </View>

          {/* ─── STEP 4: Results ─────────────────────────────────────── */}
          {result && (
            <View style={styles.resultSection}>
              {/* Decision Banner */}
              <View style={[styles.decisionBanner, { backgroundColor: scoreColor(result.decision) + "18", borderColor: scoreColor(result.decision) + "40" }]}>
                <Text style={styles.decisionEmoji}>{scoreEmoji(result.decision)}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={[styles.decisionText, { color: scoreColor(result.decision) }]}>
                    {result.decision === "BUY" ? "Chef Says: BUY!" : 
                     result.decision === "NEGOTIATE" ? "Chef Says: NEGOTIATE" : "Chef Says: PASS"}
                  </Text>
                  <Text style={styles.decisionSub}>
                    {result.decision === "BUY" 
                      ? "This deal has the ingredients for profit!"
                      : result.decision === "NEGOTIATE"
                      ? "The price needs to come down — make an offer!"
                      : "The numbers don't work. Keep sniffing."}
                  </Text>
                </View>
              </View>

              {/* Numbers Grid */}
              <View style={styles.numbersGrid}>
                <View style={styles.numberCard}>
                  <Text style={styles.numberLabel}>Max Offer</Text>
                  <Text style={[styles.numberValue, { color: colors.success }]}>
                    {result.max_offer ? `$${result.max_offer.toLocaleString()}` : "—"}
                  </Text>
                </View>
                <View style={styles.numberCard}>
                  <Text style={styles.numberLabel}>Est. Profit</Text>
                  <Text style={[styles.numberValue, { color: result.estimated_profit >= 0 ? colors.success : colors.error }]}>
                    {result.estimated_profit != null 
                      ? `$${result.estimated_profit.toLocaleString()}`
                      : "—"}
                  </Text>
                </View>
                <View style={styles.numberCard}>
                  <Text style={styles.numberLabel}>ROI</Text>
                  <Text style={[styles.numberValue, { color: (result.roi_pct || 0) >= 0 ? colors.success : colors.error }]}>
                    {result.roi_pct != null ? `${result.roi_pct}%` : "—"}
                  </Text>
                </View>
              </View>

              {/* Detailed Breakdown */}
              <View style={styles.breakdown}>
                <Text style={styles.breakdownTitle}>📊 Deal Breakdown</Text>
                <View style={styles.breakdownRow}>
                  <Text style={styles.breakdownLabel}>Purchase Price</Text>
                  <Text style={styles.breakdownValue}>${result.listing_price?.toLocaleString() || "—"}/</Text>
                </View>
                <View style={styles.breakdownRow}>
                  <Text style={styles.breakdownLabel}>Repairs</Text>
                  <Text style={styles.breakdownValue}>+${result.repairs?.toLocaleString() || "0"}/</Text>
                </View>
                <View style={[styles.breakdownRow, styles.breakdownTotal]}>
                  <Text style={styles.breakdownTotalLabel}>Total Cost</Text>
                  <Text style={styles.breakdownTotalValue}>
                    ${((result.listing_price || 0) + (result.repairs || 0)).toLocaleString()}
                  </Text>
                </View>
                <View style={styles.breakdownRow}>
                  <Text style={styles.breakdownLabel}>ARV</Text>
                  <Text style={styles.breakdownValue}>${result.arv?.toLocaleString() || "—"}/</Text>
                </View>
                <View style={[styles.breakdownRow, styles.breakdownTotal]}>
                  <Text style={[styles.breakdownTotalLabel, { color: colors.success }]}>
                    Gross Profit
                  </Text>
                  <Text style={[styles.breakdownTotalValue, { color: colors.success }]}>
                    {result.estimated_profit != null ? `$${result.estimated_profit.toLocaleString()}` : "—"}
                  </Text>
                </View>
              </View>

              {/* Warning */}
              {result.warning && (
                <View style={styles.warningBox}>
                  <Ionicons name="warning" size={14} color={colors.warning} />
                  <Text style={styles.warningText}>{result.warning}</Text>
                </View>
              )}

              {/* Analyze Another */}
              <Pressable style={styles.anotherBtn} onPress={() => { setStep("address"); setResult(null); }}>
                <Ionicons name="refresh" size={16} color={colors.brandPrimary} />
                <Text style={styles.anotherBtnText}>Analyze Another Deal</Text>
              </Pressable>
            </View>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  scroll: { padding: spacing.lg, paddingBottom: 120 },

  header: { marginBottom: spacing.lg },
  headerTitle: { fontSize: 26, fontWeight: "800", color: colors.onSurface, marginBottom: 4 },
  headerSub: { fontSize: 14, color: colors.muted },

  // Search
  searchSection: { marginBottom: spacing.lg, zIndex: 100 },
  searchWrap: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1.5,
    borderColor: colors.border,
    paddingHorizontal: spacing.md,
    height: 50,
    gap: 8,
  },
  searchIcon: { flexShrink: 0 },
  searchInput: { flex: 1, fontSize: 16, color: colors.onSurface, fontWeight: "600" },

  suggestionsBox: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    marginTop: 4,
    overflow: "hidden",
    zIndex: 200,
    elevation: 4,
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
  },
  suggestionItem: {
    flexDirection: "row",
    alignItems: "center",
    padding: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
    gap: 10,
  },
  suggestionItemPressed: { backgroundColor: colors.surfaceTertiary },
  suggestionIconWrap: {
    width: 28, height: 28, borderRadius: 14,
    backgroundColor: colors.brandPrimary + "18",
    justifyContent: "center", alignItems: "center",
  },
  suggestionAddress: { fontSize: 14, fontWeight: "700", color: colors.onSurface },
  suggestionMeta: { flexDirection: "row", gap: 8, marginTop: 2, flexWrap: "wrap" },
  suggestionMetaText: { fontSize: 11, color: colors.muted },

  noSuggestions: { padding: spacing.md, backgroundColor: colors.surfaceSecondary, borderRadius: radius.md, marginTop: 4 },
  noSuggestionsText: { fontSize: 13, color: colors.muted, fontWeight: "600" },
  noSuggestionsSub: { fontSize: 12, color: colors.muted, marginTop: 2 },

  // Property Card
  propertyCard: {
    backgroundColor: colors.brandPrimary + "0d",
    borderRadius: radius.md,
    borderWidth: 1.5,
    borderColor: colors.brandPrimary + "30",
    padding: spacing.md,
    marginBottom: spacing.lg,
  },
  propertyCardHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 8 },
  propertyCardTitle: { fontSize: 12, fontWeight: "800", color: colors.brandPrimary, textTransform: "uppercase", letterSpacing: 0.6 },
  changeLink: { fontSize: 13, color: colors.brandPrimary, fontWeight: "600" },
  propertyCardBody: {},
  propertyAddress: { fontSize: 15, fontWeight: "800", color: colors.onSurface, marginBottom: 4 },
  propertyOwner: { fontSize: 13, color: colors.muted, marginBottom: 8 },
  propertyStats: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  statPill: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: 20,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  statPillText: { fontSize: 11, color: colors.onSurface, fontWeight: "600" },
  statPillHighlight: { backgroundColor: colors.success + "18" },
  statPillHighlightText: { fontSize: 11, color: colors.success, fontWeight: "700" },

  // Form
  formSection: { marginBottom: spacing.lg },
  formTitle: { fontSize: 16, fontWeight: "800", color: colors.onSurface, marginBottom: spacing.md },

  inputGroup: { marginBottom: spacing.md },
  inputLabel: { fontSize: 12, fontWeight: "700", color: colors.onSurfaceTertiary, marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.5 },
  inputWrap: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.sm,
    borderWidth: 1.5,
    borderColor: colors.border,
    paddingHorizontal: spacing.md,
    height: 48,
  },
  inputPrefix: { fontSize: 16, color: colors.muted, fontWeight: "700", marginRight: 4 },
  inputSuffix: { fontSize: 14, color: colors.muted, fontWeight: "600", marginLeft: 4 },
  input: { flex: 1, fontSize: 16, color: colors.onSurface, fontWeight: "700", ...tabularNums },
  inputPrefilled: { color: colors.success },

  inputHint: { fontSize: 11, color: colors.muted, marginTop: 4 },

  analyzeBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: colors.brandPrimary,
    borderRadius: radius.md,
    height: 52,
    marginTop: spacing.sm,
    shadowColor: colors.brandPrimary,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 4,
  },
  analyzeBtnPressed: { opacity: 0.85 },
  analyzeBtnLoading: { backgroundColor: colors.muted },
  analyzeBtnText: { fontSize: 16, fontWeight: "800", color: "#fff", letterSpacing: 0.5 },

  errorBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.error + "12",
    borderRadius: radius.sm,
    padding: spacing.sm,
    marginTop: spacing.sm,
  },
  errorText: { fontSize: 13, color: colors.error, flex: 1 },

  // Results
  resultSection: { marginTop: spacing.md },
  decisionBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    borderRadius: radius.md,
    borderWidth: 1.5,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  decisionEmoji: { fontSize: 32 },
  decisionText: { fontSize: 20, fontWeight: "900", marginBottom: 2 },
  decisionSub: { fontSize: 13, color: colors.onSurfaceTertiary },

  numbersGrid: {
    flexDirection: "row",
    gap: spacing.sm,
    marginBottom: spacing.md,
  },
  numberCard: {
    flex: 1,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.sm,
    padding: spacing.sm,
    alignItems: "center",
  },
  numberLabel: { fontSize: 10, fontWeight: "700", color: colors.muted, textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4 },
  numberValue: { fontSize: 16, fontWeight: "900", ...tabularNums },

  breakdown: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  breakdownTitle: { fontSize: 13, fontWeight: "800", color: colors.onSurfaceTertiary, textTransform: "uppercase", letterSpacing: 0.6, marginBottom: spacing.sm },
  breakdownRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  breakdownLabel: { fontSize: 14, color: colors.onSurface },
  breakdownValue: { fontSize: 14, color: colors.onSurface, fontWeight: "700", ...tabularNums },
  breakdownTotal: { borderBottomWidth: 0, paddingTop: 8, marginTop: 4 },
  breakdownTotalLabel: { fontSize: 15, fontWeight: "800", color: colors.onSurface },
  breakdownTotalValue: { fontSize: 18, fontWeight: "900", ...tabularNums },

  warningBox: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 6,
    backgroundColor: colors.warning + "12",
    borderRadius: radius.sm,
    padding: spacing.sm,
    marginBottom: spacing.md,
  },
  warningText: { fontSize: 13, color: colors.warning, flex: 1, lineHeight: 18 },

  anotherBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderWidth: 1.5,
    borderColor: colors.brandPrimary,
    borderRadius: radius.md,
    height: 46,
  },
  anotherBtnText: { fontSize: 15, fontWeight: "700", color: colors.brandPrimary },
});
