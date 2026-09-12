import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Linking,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter, type Href } from "expo-router";

import {
  API_BASE,
  countyRecordsCsvUrl,
  getCountyRecords,
  getCountyRecordStats,
  type CountyRecord,
  type CountyRecordStats,
} from "@/src/lib/api";
import { adminRequestHeaders } from "@/src/lib/admin";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";


type CountySource = "all" | "uploaded" | "code_violations" | "tad" | "tax_roll" | "tax_delinquent";
type CountyCodeRecord = CountyRecord & {
  has_code_violations?: boolean;
  code_violation_count?: number;
  open_code_violation_count?: number;
  code_latest_complaint?: string;
  code_latest_status?: string;
  code_oldest_open_date?: string;
  code_latest_update?: string;
  code_next_activity_due?: string;
  code_geocode_score?: number;
  pre_foreclosure?: boolean;
  listing_type?: string;
  listing_status?: string;
  sale_status?: string;
  auction_date?: string;
  data_source?: string;
  source_platform?: string;
};
type ExtendedStats = CountyRecordStats & {
  uploaded?: number;
  with_code_violations?: number;
  open_code_violations?: number;
};

const SOURCES: { key: CountySource; label: string }[] = [
  { key: "all", label: "All records" },
  { key: "uploaded", label: "Uploaded" },
  { key: "code_violations", label: "Code violations" },
  { key: "tax_delinquent", label: "Tax due" },
  { key: "tad", label: "TAD" },
  { key: "tax_roll", label: "Tax roll" },
];

const COLUMNS = {
  address: 230,
  owner: 185,
  account: 115,
  preForeclosure: 112,
  foreclosure: 104,
  openCode: 82,
  complaint: 180,
  codeStatus: 125,
  year: 72,
  sqft: 82,
  appraised: 112,
  market: 112,
  currentDue: 104,
  priorDue: 104,
  source: 145,
  quality: 82,
};

const TABLE_WIDTH = Object.values(COLUMNS).reduce((sum, width) => sum + width, 0);

function money(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return `$${Math.round(Number(value)).toLocaleString()}`;
}

function plain(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

function Cell({ width, children, strong, danger }: { width: number; children: string; strong?: boolean; danger?: boolean }) {
  return (
    <View style={[styles.cell, { width }]}>
      <Text
        numberOfLines={2}
        style={[styles.cellText, strong && styles.cellStrong, danger && styles.cellDanger, tabularNums]}
      >
        {children}
      </Text>
    </View>
  );
}

function HeaderCell({ width, children }: { width: number; children: string }) {
  return (
    <View style={[styles.headerCell, { width }]}>
      <Text style={styles.headerCellText}>{children}</Text>
    </View>
  );
}

export default function CountyRecordsScreen() {
  const router = useRouter();
  const [source, setSource] = useState<CountySource>("all");
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<CountyRecord[]>([]);
  const [stats, setStats] = useState<CountyRecordStats | null>(null);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [syncingCode, setSyncingCode] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (nextPage = 1, append = false) => {
    if (append) setLoadingMore(true);
    else setLoading(true);
    setError(null);
    try {
      const [records, summary] = await Promise.all([
        getCountyRecords(source as any, search, nextPage),
        append && stats ? Promise.resolve(stats) : getCountyRecordStats(),
      ]);
      setItems((current) => append ? [...current, ...records.items] : records.items);
      setStats(summary);
      setPage(records.page);
      setPages(records.pages);
      setTotal(records.total);
    } catch (e: any) {
      setError(e?.message || "County records could not be loaded.");
    } finally {
      setLoading(false);
      setLoadingMore(false);
      setRefreshing(false);
    }
  }, [search, source, stats]);

  useEffect(() => {
    const timer = setTimeout(() => load(1, false), 300);
    return () => clearTimeout(timer);
  }, [source, search]); // eslint-disable-line react-hooks/exhaustive-deps

  const latestSync = useMemo(() => stats?.recent_syncs?.[0], [stats]);
  const extendedStats = stats as ExtendedStats | null;
  const loadMore = () => {
    if (!loading && !loadingMore && page < pages) load(page + 1, true);
  };
  const refresh = () => {
    setRefreshing(true);
    load(1, false);
  };

  const syncCodeViolations = async () => {
    setSyncingCode(true);
    setSyncMessage(null);
    setError(null);
    try {
      const headers = await adminRequestHeaders();
      const response = await fetch(`${API_BASE}/api/admin/county-records/sync?source=code_violations`, {
        method: "POST",
        headers,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload?.detail || `Code sync failed (${response.status})`);
      const result = payload?.results?.code_violations || {};
      setSyncMessage(`${Number(result.fetched || 0).toLocaleString()} violations pulled · ${Number(result.properties || 0).toLocaleString()} properties matched/created`);
      setSource("code_violations");
      await load(1, false);
    } catch (e: any) {
      setError(e?.message || "Fort Worth code violations could not be synced.");
    } finally {
      setSyncingCode(false);
    }
  };

  const renderRow = ({ item, index }: { item: CountyRecord; index: number }) => {
    const code = item as CountyCodeRecord;
    const distressText = [
      code.listing_type,
      code.listing_status,
      code.sale_status,
      code.data_source,
      code.source_platform,
    ].filter(Boolean).join(" ").toLowerCase();
    const preForeclosure = Boolean(
      code.pre_foreclosure || distressText.includes("pre-foreclos") || distressText.includes("preforeclos")
    );
    const foreclosure = Boolean(
      !preForeclosure && (
        distressText.includes("foreclos") ||
        distressText.includes("bank owned") ||
        distressText.includes("reo") ||
        code.auction_date
      )
    );
    return (
      <Pressable
        onPress={() => router.push(`/county/${encodeURIComponent(item.id)}` as Href)}
        style={({ pressed }) => [
          styles.row,
          index % 2 === 1 && styles.rowAlternate,
          pressed && styles.rowPressed,
        ]}
      >
        <Cell width={COLUMNS.address} strong>{plain(item.situs_address)}</Cell>
        <Cell width={COLUMNS.owner}>{plain(item.owner_name)}</Cell>
        <Cell width={COLUMNS.account}>{plain(item.account_id || item.parcel_id)}</Cell>
        <Cell width={COLUMNS.preForeclosure} danger={preForeclosure}>{preForeclosure ? "YES" : "—"}</Cell>
        <Cell width={COLUMNS.foreclosure} danger={foreclosure}>{foreclosure ? "YES" : "—"}</Cell>
        <Cell width={COLUMNS.openCode} danger={Boolean(code.open_code_violation_count)}>{plain(code.open_code_violation_count)}</Cell>
        <Cell width={COLUMNS.complaint}>{plain(code.code_latest_complaint)}</Cell>
        <Cell width={COLUMNS.codeStatus} danger={Boolean(code.open_code_violation_count)}>{plain(code.code_latest_status)}</Cell>
        <Cell width={COLUMNS.year}>{plain(item.year_built)}</Cell>
        <Cell width={COLUMNS.sqft}>{plain(item.sqft)}</Cell>
        <Cell width={COLUMNS.appraised}>{money(item.appraised_value)}</Cell>
        <Cell width={COLUMNS.market}>{money(item.market_value || item.tax_roll_market_value)}</Cell>
        <Cell width={COLUMNS.currentDue} danger={Boolean(item.current_tax_amount_due)}>{money(item.current_tax_amount_due)}</Cell>
        <Cell width={COLUMNS.priorDue} danger={Boolean(item.prior_tax_amount_due)}>{money(item.prior_tax_amount_due)}</Cell>
        <Cell width={COLUMNS.source}>{item.sources?.join(" + ") || "—"}</Cell>
        <Cell width={COLUMNS.quality}>{item.completeness_score != null ? `${item.completeness_score}%` : "—"}</Cell>
      </Pressable>
    );
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.top}>
        <View style={styles.titleRow}>
          <View>
            <Text style={styles.eyebrow}>TARRANT COUNTY · FORT WORTH PUBLIC RECORDS</Text>
            <Text style={styles.title}>County Records</Text>
          </View>
          <View style={styles.titleActions}>
            <Pressable
              style={styles.officialButton}
              onPress={() => Linking.openURL("https://tarrant.tx.publicsearch.us/")}
              testID="county-official-search"
            >
              <Ionicons name="search" size={16} color={colors.onSurface} />
            </Pressable>
            <Pressable
              style={[styles.codeSyncButton, syncingCode && styles.disabled]}
              disabled={syncingCode}
              onPress={syncCodeViolations}
              testID="sync-code-violations"
            >
              {syncingCode ? <ActivityIndicator size="small" color="#fff" /> : <Ionicons name="warning-outline" size={16} color="#fff" />}
              <Text style={styles.exportText}>{syncingCode ? "Syncing…" : "Sync Code"}</Text>
            </Pressable>
            <Pressable style={styles.exportButton} onPress={() => Linking.openURL(countyRecordsCsvUrl(source as any))}>
              <Ionicons name="download-outline" size={17} color={colors.onBrandPrimary} />
              <Text style={styles.exportText}>CSV</Text>
            </Pressable>
          </View>
        </View>

        <View style={styles.statsRow}>
          <View style={styles.stat}><Text style={styles.statValue}>{extendedStats?.uploaded ?? "—"}</Text><Text style={styles.statLabel}>Uploaded</Text></View>
          <View style={styles.stat}><Text style={styles.statValue}>{stats?.with_tad ?? "—"}</Text><Text style={styles.statLabel}>TAD</Text></View>
          <View style={styles.stat}><Text style={styles.statValue}>{stats?.with_tax_roll ?? "—"}</Text><Text style={styles.statLabel}>Tax roll</Text></View>
          <View style={styles.stat}><Text style={[styles.statValue, styles.due]}>{stats?.tax_delinquent ?? "—"}</Text><Text style={styles.statLabel}>Tax due</Text></View>
          <View style={styles.stat}><Text style={[styles.statValue, styles.due]}>{extendedStats?.with_code_violations ?? "—"}</Text><Text style={styles.statLabel}>Code props</Text></View>
          <View style={styles.stat}><Text style={styles.statValue}>{total}</Text><Text style={styles.statLabel}>Shown set</Text></View>
        </View>

        <View style={styles.searchBox}>
          <Ionicons name="search" size={17} color={colors.muted} />
          <TextInput
            value={search}
            onChangeText={setSearch}
            placeholder="Address, owner, account, foreclosure, case, violation, complaint"
            placeholderTextColor={colors.muted}
            style={styles.searchInput}
          />
          {search ? <Pressable onPress={() => setSearch("")}><Ionicons name="close-circle" size={18} color={colors.muted} /></Pressable> : null}
        </View>

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filters}>
          {SOURCES.map((option) => (
            <Pressable
              key={option.key}
              onPress={() => setSource(option.key)}
              style={[styles.filter, source === option.key && styles.filterActive]}
            >
              <Text style={[styles.filterText, source === option.key && styles.filterTextActive]}>{option.label}</Text>
            </Pressable>
          ))}
        </ScrollView>
        {syncMessage ? <Text style={styles.syncSuccess}>{syncMessage}</Text> : null}
        <Text style={styles.syncText} numberOfLines={1}>
          {source === "uploaded"
            ? "Uploaded rows are shown immediately; distress, TAD, tax, and code fields appear as matching data becomes available."
            : latestSync
              ? `Last ${latestSync.source} sync: ${new Date(latestSync.created_at).toLocaleString()} · tap any row for every source field`
              : "Sync Code pulls the Fort Worth ArcGIS violation feed; tap any row for every source field."}
        </Text>
      </View>

      {loading ? (
        <View style={styles.center}><ActivityIndicator color={colors.brandPrimary} /><Text style={styles.loadingText}>Loading county rows…</Text></View>
      ) : error ? (
        <View style={styles.center}>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable style={styles.retry} onPress={() => load(1, false)}><Text style={styles.retryText}>Try again</Text></Pressable>
        </View>
      ) : (
        <ScrollView horizontal style={styles.tableScroll} contentContainerStyle={{ width: TABLE_WIDTH }}>
          <View style={{ width: TABLE_WIDTH, flex: 1 }}>
            <View style={styles.tableHeader}>
              <HeaderCell width={COLUMNS.address}>PROPERTY ADDRESS</HeaderCell>
              <HeaderCell width={COLUMNS.owner}>OWNER</HeaderCell>
              <HeaderCell width={COLUMNS.account}>ACCOUNT / PARCEL</HeaderCell>
              <HeaderCell width={COLUMNS.preForeclosure}>PRE-FORECLOSURE</HeaderCell>
              <HeaderCell width={COLUMNS.foreclosure}>FORECLOSURE</HeaderCell>
              <HeaderCell width={COLUMNS.openCode}>OPEN CODE</HeaderCell>
              <HeaderCell width={COLUMNS.complaint}>LATEST COMPLAINT</HeaderCell>
              <HeaderCell width={COLUMNS.codeStatus}>CODE STATUS</HeaderCell>
              <HeaderCell width={COLUMNS.year}>BUILT</HeaderCell>
              <HeaderCell width={COLUMNS.sqft}>SQFT</HeaderCell>
              <HeaderCell width={COLUMNS.appraised}>APPRAISED</HeaderCell>
              <HeaderCell width={COLUMNS.market}>MARKET VALUE</HeaderCell>
              <HeaderCell width={COLUMNS.currentDue}>CURRENT DUE</HeaderCell>
              <HeaderCell width={COLUMNS.priorDue}>PRIOR DUE</HeaderCell>
              <HeaderCell width={COLUMNS.source}>SOURCE</HeaderCell>
              <HeaderCell width={COLUMNS.quality}>COMPLETE</HeaderCell>
            </View>
            <FlatList
              data={items}
              keyExtractor={(item) => item.id}
              renderItem={renderRow}
              onEndReached={loadMore}
              onEndReachedThreshold={0.35}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.brandPrimary} />}
              ListEmptyComponent={<View style={styles.empty}><Text style={styles.emptyTitle}>{source === "uploaded" ? "No uploaded rows yet." : "No county rows match."}</Text><Text style={styles.emptyText}>{source === "uploaded" ? "Upload the Caroline test spreadsheet from Add Property, then return here." : "Try another source or run Sync Code."}</Text></View>}
              ListFooterComponent={loadingMore ? <ActivityIndicator style={styles.more} color={colors.brandPrimary} /> : null}
            />
          </View>
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  top: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.sm, borderBottomWidth: 1, borderBottomColor: colors.border },
  titleRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  eyebrow: { fontSize: 9, fontWeight: "800", letterSpacing: 1.1, color: colors.muted },
  title: { fontSize: 24, fontWeight: "800", color: colors.onSurface, marginTop: 2 },
  titleActions: { flexDirection: "row", alignItems: "center", gap: 8 },
  officialButton: { width: 38, height: 38, borderRadius: radius.md, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  codeSyncButton: { flexDirection: "row", alignItems: "center", gap: 5, backgroundColor: colors.error, paddingHorizontal: 11, paddingVertical: 8, borderRadius: radius.md },
  exportButton: { flexDirection: "row", alignItems: "center", gap: 5, backgroundColor: colors.brandPrimary, paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.md },
  exportText: { color: colors.onBrandPrimary, fontWeight: "800", fontSize: 12 },
  statsRow: { flexDirection: "row", gap: 7, marginTop: spacing.md },
  stat: { flex: 1, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, borderRadius: radius.sm, paddingVertical: 7, paddingHorizontal: 8 },
  statValue: { fontSize: 14, fontWeight: "800", color: colors.onSurface, ...tabularNums },
  statLabel: { fontSize: 9, color: colors.muted, marginTop: 1 },
  due: { color: colors.error },
  searchBox: { height: 42, flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, paddingHorizontal: 12, marginTop: spacing.md },
  searchInput: { flex: 1, color: colors.onSurface, fontSize: 13, paddingVertical: 0 },
  filters: { gap: 7, paddingTop: spacing.sm, paddingRight: spacing.lg },
  filter: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.borderStrong, backgroundColor: colors.surfaceSecondary },
  filterActive: { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary },
  filterText: { fontSize: 11, fontWeight: "700", color: colors.onSurfaceTertiary },
  filterTextActive: { color: colors.onBrandPrimary },
  syncText: { fontSize: 10, color: colors.muted, marginTop: 7 },
  syncSuccess: { fontSize: 10, color: colors.success, fontWeight: "700", marginTop: 7 },
  disabled: { opacity: 0.55 },
  tableScroll: { flex: 1 },
  tableHeader: { height: 42, flexDirection: "row", backgroundColor: colors.brandPrimary, borderBottomWidth: 1, borderBottomColor: colors.borderStrong },
  headerCell: { justifyContent: "center", paddingHorizontal: 8, borderRightWidth: StyleSheet.hairlineWidth, borderRightColor: "rgba(255,255,255,0.22)" },
  headerCellText: { color: colors.onBrandPrimary, fontSize: 9, fontWeight: "800", letterSpacing: 0.35 },
  row: { minHeight: 54, flexDirection: "row", backgroundColor: colors.surfaceSecondary, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  rowAlternate: { backgroundColor: colors.surface },
  rowPressed: { backgroundColor: colors.brandTertiary },
  cell: { justifyContent: "center", paddingHorizontal: 8, paddingVertical: 6, borderRightWidth: StyleSheet.hairlineWidth, borderRightColor: colors.border },
  cellText: { color: colors.onSurfaceTertiary, fontSize: 11 },
  cellStrong: { color: colors.onSurface, fontWeight: "700" },
  cellDanger: { color: colors.error, fontWeight: "800" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  loadingText: { color: colors.muted, fontSize: 12, marginTop: spacing.sm },
  errorText: { color: colors.error, fontSize: 13, textAlign: "center" },
  retry: { marginTop: spacing.md, backgroundColor: colors.brandPrimary, borderRadius: radius.md, paddingHorizontal: 16, paddingVertical: 9 },
  retryText: { color: colors.onBrandPrimary, fontWeight: "700" },
  empty: { width: TABLE_WIDTH, paddingVertical: spacing.xxxl, paddingHorizontal: spacing.xl },
  emptyTitle: { fontSize: 15, fontWeight: "700", color: colors.onSurface },
  emptyText: { fontSize: 12, color: colors.muted, marginTop: 4 },
  more: { marginVertical: spacing.lg },
});