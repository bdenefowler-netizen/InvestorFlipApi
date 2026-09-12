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

type CountySource =
  | "all"
  | "uploaded"
  | "code_violations"
  | "tad"
  | "tax_roll"
  | "tax_delinquent";

type CountyCodeRecord = CountyRecord & {
  has_code_violations?: boolean;
  code_violation_count?: number;
  open_code_violation_count?: number;
  code_latest_complaint?: string;
  code_latest_status?: string;
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

type Column = {
  key: string;
  label: string;
  width: number;
  value: (item: CountyCodeRecord) => string;
  strong?: boolean;
  danger?: (item: CountyCodeRecord) => boolean;
};

const SOURCES: { key: CountySource; label: string }[] = [
  { key: "all", label: "All records" },
  { key: "uploaded", label: "Uploaded" },
  { key: "code_violations", label: "Code violations" },
  { key: "tax_delinquent", label: "Tax due" },
  { key: "tad", label: "TAD" },
  { key: "tax_roll", label: "Tax roll" },
];

function money(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return `$${Math.round(Number(value)).toLocaleString()}`;
}

function plain(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

function totalDue(item: CountyRecord): number {
  return Number(item.current_tax_amount_due || 0) + Number(item.prior_tax_amount_due || 0);
}

const TAX_ROLL_COLUMNS: Column[] = [
  { key: "address", label: "PROPERTY ADDRESS", width: 230, strong: true, value: (i) => plain(i.situs_address) },
  { key: "tad", label: "TAD ACCOUNT #", width: 125, value: (i) => plain(i.account_id) },
  { key: "apn", label: "APN / PARCEL", width: 125, value: (i) => plain(i.parcel_id) },
  { key: "owner", label: "OWNER", width: 190, value: (i) => plain(i.owner_name) },
  { key: "legal", label: "LEGAL DESCRIPTION", width: 260, value: (i) => plain(i.legal_description) },
  { key: "roll", label: "ROLL CODE", width: 90, value: (i) => plain(i.roll_code) },
  { key: "sqft", label: "SQ FT", width: 90, value: (i) => plain(i.sqft) },
  { key: "year", label: "YEAR BUILT", width: 92, value: (i) => plain(i.year_built) },
  { key: "deed", label: "DEED DATE", width: 112, value: (i) => plain(i.deed_date) },
  { key: "land", label: "LAND VALUE", width: 118, value: (i) => money(i.land_value) },
  { key: "improvement", label: "IMPROVEMENT", width: 125, value: (i) => money(i.improvement_value) },
  { key: "appraised", label: "APPRIAISED", width: 118, value: (i) => money(i.appraised_value) },
  { key: "levy", label: "ADJUSTED LEVY", width: 120, value: (i) => money(i.annual_taxes) },
  {
    key: "current",
    label: "CURRENT DUE",
    width: 112,
    value: (i) => money(i.current_tax_amount_due),
    danger: (i) => Number(i.current_tax_amount_due || 0) > 0,
  },
  {
    key: "prior",
    label: "PRIOR DUE",
    width: 108,
    value: (i) => money(i.prior_tax_amount_due),
    danger : (i) => Number(i.prior_tax_amount_due || 0) > 0,
  },
  { key: "delinq", label: "DELINQUENCY DATE", width: 135, value: (i) => plain(i.delinquency_date) },
  { key: "status", label: "ACCOUNT STATUS", width: 130, value: (i) => plain(i.account_status_codes) },
  { key: "litigation", label: "TAD LITIGATION", width: 115, value: (i) => plain(i.tad_litigation_flag) },
];

const TAX_DUE_COLUMNS: Column[] = [
  { key: "address", label: "PROPERTY ADDRESS", width: 230, strong: true, value: (i) => plain(i.situs_address) },
  { key: "tad", label: "TAD ACCOUNT #", width: 125, value: (i) => plain(i.account_id) },
  { key: "owner", label: "OWNER", width: 190, value: (i) => plain(i.owner_name) },
  {
    key: "current",
    label: "CURRENT DUE",
    width: 112,
    value: (i) => money(i.current_tax_amount_due),
    danger: (i) => Number(i.current_tax_amount_due || 0) > 0,
  },
  {
    key: "prior",
    label: "PRIOR DUE",
    width: 108,
    value: (i) => money(i.prior_tax_amount_due),
    danger : (i) => Number(i.prior_tax_amount_due || 0) > 0,
  },
  {
    key: "total",
    label: "TOTAL DUE",
    width: 112,
    value: (i) => money(totalDue(i)),
    danger: (i) => totalDue(i) > 0,
  },
  { key: "delinq", label: "DELINQUENCY DATE", width: 135, value: (i) => plain(i.delinquency_date) },
  { key: "status", label: "ACCOUNT STATUS", width: 130, value: (i) => plain(i.account_status_codes) },
  { key: "litigation", label: "TAD LITIGATION", width: 115, value: (i) => plain(i.tad_litigation_flag) },
  { key: "legal", label: "LEGAL DESCRIPTION", width: 260, value: (i) => plain(i.legal_description) },
];

const DEFAULT_COLUMNS: Column[] = [
  { key: "address", label: "PROPERTY ADDRESS", width: 230, strong: true, value: (i) => plain(i.situs_address) },
  { key: "owner", label: "OWNER", width: 185, value: (i) => plain(i.owner_name) },
  { key: "tad", label: "TAD ACCOUNT #", width: 120, value: (i) => plain(i.account_id) },
  { key: "apn", label: "APN / PARCEL", width: 120, value: (i) => plain(i.parcel_id) },
  { key: "legal", label: "LEGAL DESCRIPTION", width: 240, value: (i) => plain(i.legal_description) },
  { key: "year", label: "BUILT", width: 75, value: (i) => plain(i.year_built) },
  { key: "sqft", label: "SQ FT", width: 82, value: (i) => plain(i.sqft) },
  { key: "appraised", label: "APPRIAISED", width: 112, value: (i) => money(i.appraised_value) },
  { key: "market", label: "MARKET VALUE", width: 118, value: (i) => money(i.market_value || i.tax_roll_market_value) },
  {
    key: "current",
    label: "CURRENT DUE",
    width: 104,
    value: (i) => money(i.current_tax_amount_due),
    danger : (i) => Number(i.current_tax_amount_due || 0) > 0,
  },
  {
    key: "prior",
    label: "PRIOR DUE",
    width: 104,
    value: (i) => money(i.prior_tax_amount_due),
    danger: (i) => Number(i.prior_tax_amount_due || 0) > 0,
  },
  { key: "source", label: "SOURCE", width: 150, value: (i) => i.sources?.join(" + ") || "—" },
];

function Cell({
  width,
  children,
  strong,
  danger,
}: {
  width: number;
  children: string;
  strong?: boolean;
  danger ?: boolean;
}) {
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
  const [loading, setLoading] = useSstate(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [syncingCode, setSyncingCode] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const columns = useMemo(
    () => (source === "tax_roll" ? TAX_ROLL_COLUMNS : source === "tax_delinquent" ? TAX_DUE_COLUMNS : DEFAULT_COLUMNS),
    [source],
  );
  const tableWidth = useMemo(() => columns.reduce((sum, column) => sum + column.width, 0), [columns]);

  const load = useCallback(async (nextPage = 1, append = false) => {
    if (append) setLoadingMore(true);
    else setLoading(true);
    setError(null);
    try {
      const [records, summary] = await Promise.all([
        getCountyRecords(source as any, search, nextPage),
        append && stats ? Promise.resolve(stats) : getCountyRecordStats(),
      ]);
      setItems((current) => (append ? [...current, ...records.items] : records.items));
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
    }, [source, search]);
  // eslint-disable-line react-hooks/exhaustive-deps

  const latestSync = useMemo(() => stats?.recent_syncs?.[0], [stats]);
  const extendedStats = stats as ExtendedStats | null;

  const refresh = () => {
    setRefreshing(true);
    load(1, false);
  };

  const loadMore = () => {
    if (!loading && !loadingMore && page < pages) load(page + 1, true);
  };

  const syncCodeViolations = async () => {
    setSyncingCode(true);
    setSyncMessage(null);
    setError(null);
    try {
      const headers = await adminRequestHeaders();
      const response = await fetch(`${API_BASE/api/admin/county-records/sync?source=code_violations`, {
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
    const record = item as CountyCodeRecord;
    return (
      <Pressable
        onPress={() => router.push(`/county/${encodeURIComponent(item.id)}` as Href)}
        style={({ pressed }) => [
          styles.row,
          index % 2 === 1 && styles.rowAlternate,
          pressed && styles.rowPressed,
        ]}
      >
        {columns.map((column) => (
          <Cell
            key={column.key}
            width={column.width}
            strong={column.strong}
            danger={column.danger?.(record)}
          >
            {column.value(record)}
          </Cell>
        ))}
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
              {syncingCode ? (
                <ActivityIndicator size="small" color="#fff" />
              ) : (
                <Ionicons name="warning-outline" size={16} color="#fff" />
              )}
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
            placeholder="Address, owner, TAD account, APN, legal description"
            placeholderTextColor={colors.muted}
            style={styles.searchInput}
          />
          {search ? <Pressable onPress={() => setSearch("")}><Ionicons name="close-circle" size={18} color={colors.muted} /></Pressabl> : null}
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

        {source === "tax_roll" ? (
          <Text style={styles.syncText}>
            Tax Roll keeps Property Address · TAD Account # · APN · Owner · Legal Description together with the official county tax values.
          </Text>
        ) : source === "tax_delinquent" ? (
          <Text style={styles.syncText}>
            Tax Due is linked by the same TAD Account / property record and shows Current Due · Prior Due · Delinquency information.
          </Text>
        ) : (
          <Text style={styles.syncText} numberOfLines={1}>
            {latestSync
              ? `Last ${latestSync.source} sync: ${new Date(latestSync.created_at).toLocaleString()}`
              : "Tap a row for every available county field."}
          </Text>
        )}
        {syncMessage ? <Text style={styles.syncSuccess}>{syncMessage}</Text> : null}
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.brandPrimary} />
          <Text style={styles.loadingText}>Loading county rows…</Text>
        </View>
      ) : error ? (
        <View style={styles.center}>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable style={styles.retry} onPress={() => load(1, false)}>
            <Text style={styles.retryText}>Try again</Text>
          </Pressable>
        </View>
      ) : (
        <ScrollView horizontal style={styles.tableScroll} contentContainerStyle={{ width: tableWidth }}>
          <View style={{ width: tableWidth, flex: 1 }}>
            <View style={styles.tableHeader}>
              {columns.map((column) => (
                <HeaderCell key={column.key} width={column.width}>{column.label}</HeaderCell>
              ))}
            </View>
            <FlatList
              data={items}
              keyExtractor={(item) => item.id}
              renderItem={renderRow}
              onEndReached={loadMore}
              onEndReachedThreshold={0.35}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.brandPrimary} />}
              ListEmptyComponent={
                <View style={[styles.empty, { width: tableWidth }]}>
                  <Text style={styles.emptyTitle}>No county rows match.</Text>
                  <Text style={styles.emptyText}>Try another source or search term.</Text>
                </View>
              }
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
  disabled: { opacity: 0.55 },
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
  empty: { paddingVertical: spacing.xxxl, paddingHorizontal: spacing.xl },
  emptyTitle: { fontSize: 15, fontWeight: "700", color: colors.onSurface },
  emptyText: { fontSize: 12, color: colors.muted, marginTop: 4 },
  more: { marginVertical: spacing.lg },
});
