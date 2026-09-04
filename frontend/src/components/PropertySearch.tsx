import React, { useState, useCallback, useEffect } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, FlatList,
  ScrollView, StyleSheet, ActivityIndicator, Alert, Modal
} from 'react-native';
import { adminRequestHeaders } from '@/src/lib/admin';

// ─── Types ───────────────────────────────────────────────
interface Property {
  id: string;
  address: string;
  city: string;
  state: string;
  zip: string;
  price?: number;
  beds?: number;
  baths?: number;
  sqft?: number;
  arv?: number;
  rent_estimate?: number;
  violation_count?: number;
  distress_score?: number;
  listing_type?: string;
  source?: string;
  investment_score?: number;
}

interface SavedSearch {
  id: string;
  name: string;
  query: string;
  filter_type: string;
  ai_query?: string;
  notes?: string;
  notify_on_new: boolean;
  created_at: string;
  last_run_at?: string;
  result_count?: number;
}

type FilterType =
  | 'all' | 'distressed' | 'foreclosure' | 'off-market'
  | 'vacant' | 'absentee' | 'tax-delinquent' | 'fixer-upper' | 'wholesale';

// ─── API Config ──────────────────────────────────────────
const API_BASE = 'https://investorflipapi-production-4970.up.railway.app';

// ─── Component ───────────────────────────────────────────
export default function PropertySearch() {
  const [query, setQuery] = useState('');
  const [aiQuery, setAiQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState<FilterType>('all');
  const [results, setResults] = useState<Property[]>([]);
  const [loading, setLoading] = useState(false);
  const [aiMode, setAiMode] = useState(false);

  // Saved searches state
  const [savedSearches, setSavedSearches] = useState<SavedSearch[]>([]);
  const [showSaved, setShowSaved] = useState(false);
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [saveNotes, setSaveNotes] = useState('');
  const [loadingSaved, setLoadingSaved] = useState(false);

  // Load saved searches on mount
  useEffect(() => { loadSavedSearches(); }, []);

  // ─── Filters ──────────────────────────────────────────
  const filters: { key: FilterType; label: string }[] = [
    { key: 'all', label: 'All' },
    { key: 'pre-foreclosure', label: '📋 Pre-Foreclosure' },
    { key: 'fsbo', label: '🏠 FSBO' },
    { key: 'distressed', label: '⚠️ Distressed' },
    { key: 'foreclosure', label: '🏛️ Foreclosure' },
    { key: 'off-market', label: '📦 Off-Market' },
    { key: 'vacant', label: '🚫 Vacant' },
    { key: 'absentee', label: '👤 Absentee' },
    { key: 'tax-delinquent', label: '💰 Tax Due' },
    { key: 'fixer-upper', label: '🔧 Fixer' },
    { key: 'wholesale', label: '📋 Wholesale' },
  ];

  // ─── API Calls ────────────────────────────────────────

  const search = useCallback(async () => {
    setLoading(true);
    try {
      if (aiMode && aiQuery.trim()) {
        const headers = await adminRequestHeaders({ 'Content-Type': 'application/json' });
        const res = await fetch(`${API_BASE}/api/ai/search`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ query: aiQuery, limit: 20 }),
        });
        const data = await res.json();
        setResults(data.properties ?? []);
      } else {
        const params = new URLSearchParams();
        if (query.trim()) params.set('search', query);
        if (activeFilter !== 'all') params.set('filter', activeFilter);
        params.set('limit', '50');
        const res = await fetch(`${API_BASE}/api/properties?${params}`);
        const data = await res.json();
        setResults(data.properties ?? []);
      }
    } catch (e) {
      console.error('Search failed', e);
    } finally {
      setLoading(false);
    }
  }, [query, activeFilter, aiMode, aiQuery]);

  const loadSavedSearches = useCallback(async () => {
    try {
      setLoadingSaved(true);
      const res = await fetch(`${API_BASE}/api/saved-searches`);
      if (res.ok) {
        const data = await res.json();
        setSavedSearches(data);
      }
    } catch (e) {
      // Saved searches endpoint might not be deployed yet
      console.log('Saved searches not available yet');
    } finally {
      setLoadingSaved(false);
    }
  }, []);

  const saveCurrentSearch = useCallback(async () => {
    if (!saveName.trim()) {
      Alert.alert('Name required', 'Give your search a name');
      return;
    }
    try {
      const headers = await adminRequestHeaders({ 'Content-Type': 'application/json' });
      const res = await fetch(`${API_BASE}/api/saved-searches`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          name: saveName.trim(),
          query: query.trim(),
          filter_type: activeFilter,
          ai_query: aiQuery.trim() || null,
          notes: saveNotes.trim() || null,
          notify_on_new: false,
        }),
      });
      if (res.ok) {
        setShowSaveModal(false);
        setSaveName('');
        setSaveNotes('');
        Alert.alert('Saved!', `"${saveName}" is ready to run anytime.`);
        loadSavedSearches();
      }
    } catch (e) {
      Alert.alert('Error', 'Could not save search');
    }
  }, [saveName, saveNotes, query, activeFilter, aiQuery, loadSavedSearches]);

  const deleteSavedSearch = useCallback(async (id: string, name: string) => {
    Alert.alert('Delete?', `Remove "${name}"?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete', style: 'destructive',
        onPress: async () => {
          const headers = await adminRequestHeaders();
          await fetch(`${API_BASE}/api/saved-searches/${id}`, { method: 'DELETE', headers });
          loadSavedSearches();
        },
      },
    ]);
  }, [loadSavedSearches]);

  const runSavedSearch = useCallback(async (saved: SavedSearch) => {
    setLoading(true);
    setShowSaved(false);
    try {
      const headers = await adminRequestHeaders();
      const res = await fetch(`${API_BASE}/api/saved-searches/${saved.id}/run?limit=30`, {
        method: 'POST',
        headers,
      });
      if (res.ok) {
        const data = await res.json();
        setResults(data.results ?? []);
        setQuery(saved.query);
        setActiveFilter(saved.filter_type as FilterType);
        if (saved.ai_query) {
          setAiQuery(saved.ai_query);
          setAiMode(true);
        }
      }
    } catch (e) {
      console.error('Run saved search failed', e);
    } finally {
      setLoading(false);
    }
  }, []);

  // ─── Render ──────────────────────────────────────────
  return (
    <View style={styles.container}>
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.headerRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.headerTitle}>🔍 Property Search</Text>
            <Text style={styles.headerSub}>3M+ properties • 9 data sources</Text>
          </View>
          <TouchableOpacity
            style={styles.savedBtn}
            onPress={() => setShowSaved(true)}
          >
            <Text style={styles.savedBtnText}>
              📁 {savedSearches.length > 0 ? savedSearches.length : ''}
            </Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* Search Bar */}
      <View style={[styles.searchBar, query ? styles.searchBarActive : null]}>
        <TextInput
          style={styles.searchInput}
          placeholder="Enter address, zip, or MLS #..."
          placeholderTextColor="#8899aa"
          value={query}
          onChangeText={setQuery}
          onSubmitEditing={search}
          returnKeyType="search"
        />
        <TouchableOpacity style={styles.searchBtn} onPress={search}>
          <Text style={styles.searchBtnText}>🔍</Text>
        </TouchableOpacity>
      </View>

      {/* Filter Chips */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chips}>
        {filters.map(f => (
          <TouchableOpacity
            key={f.key}
            style={[styles.chip, activeFilter === f.key && styles.chipActive]}
            onPress={() => { setActiveFilter(f.key); setAiMode(false); }}
          >
            <Text style={[styles.chipText, activeFilter === f.key && styles.chipTextActive]}>
              {f.label}
            </Text>
          </TouchableOpacity>
        ))}
        <TouchableOpacity
          style={[styles.chip, styles.chipAi, aiMode && styles.chipActive]}
          onPress={() => setAiMode(!aiMode)}
        >
          <Text style={[styles.chipText, aiMode && styles.chipTextActive]}>
            ✨ AI Search
          </Text>
        </TouchableOpacity>
      </ScrollView>

      {/* AI Search */}
      {aiMode && (
        <View style={styles.aiSection}>
          <Text style={styles.aiLabel}>✨ Quill AI Search</Text>
          <TextInput
            style={styles.aiInput}
            placeholder='Ask naturally...'
            placeholderTextColor="#99a"
            value={aiQuery}
            onChangeText={setAiQuery}
            onSubmitEditing={search}
          />
          <Text style={styles.aiHint}>
            Try: “vacant 3bd/2ba under 250k near TCU”
          </Text>
        </View>
      )}

      {/* Save Search Button */}
      {(query.trim() || aiQuery.trim()) && (
        <TouchableOpacity
          style={styles.saveSearchBtn}
          onPress={() => setShowSaveModal(true)}
        >
          <Text style={styles.saveSearchBtnText}>💾 Save This Search</Text>
        </TouchableOpacity>
      )}

      {/* Loading */}
      {loading && <ActivityIndicator size="large" color="#4f46e5" style={{ margin: 20 }} />}

      {/* Results */}
      <FlatList
        data={results}
        keyExtractor={item => item.id}
        contentContainerStyle={styles.resultsList}
        ListHeaderComponent={
          results.length > 0 ? (
            <View style={styles.resultsHeader}>
              <Text style={styles.resultsCount}>
                {results.length} result{results.length !== 1 ? 's' : ''}
              </Text>
              <TouchableOpacity onPress={search}>
                <Text style={styles.refreshBtn}>🔄 Refresh</Text>
              </TouchableOpacity>
            </View>
          ) : null
        }
        ListEmptyComponent={
          !loading ? (
            <View style={styles.empty}>
              <Text style={styles.emptyIcon}>🏠</Text>
              <Text style={styles.emptyText}>
                {query || aiQuery ? 'No properties found' : 'Enter an address or tap a filter'}
              </Text>
              {savedSearches.length > 0 && (
                <TouchableOpacity
                  style={styles.runSavedBtn}
                  onPress={() => setShowSaved(true)}
                >
                  <Text style={styles.runSavedBtnText}>
                    📁 Run a saved search ({savedSearches.length})
                  </Text>
                </TouchableOpacity>
              )}
            </View>
          ) : null
        }
        renderItem={({ item }) => (
          <TouchableOpacity style={styles.card} activeOpacity={0.7}>
            <View style={styles.cardTop}>
              <Text style={styles.cardAddress} numberOfLines={2}>
                {item.address}, {item.city}
              </Text>
              {item.price && (
                <Text style={styles.cardPrice}>
                  ${item.price.toLocaleString()}
                </Text>
              )}
            </View>
            <View style={styles.cardDetails}>
              {item.beds && <Text>🛏️ {item.beds}</Text>}
              {item.baths && <Text>🛁 {item.baths}</Text>}
              {item.sqft && <Text>📐 {item.sqft.toLocaleString()}</Text>}
              {item.arv && <Text>📈 ${item.arv.toLocaleString()}</Text>}
            </View>
            <View style={styles.cardBadges}>
              {(item.violation_count ?? 0) > 0 && (
                <View style={styles.badgeDistressed}>
                  <Text style={styles.badgeDistressedText}>
                    ⚠️ {item.violation_count}
                  </Text>
                </View>
              )}
              {item.distress_score && item.distress_score > 50 && (
                <View style={styles.badgeWarning}>
                  <Text style={styles.badgeWarningText}>🔥 {item.distress_score}</Text>
                </View>
              )}
              {item.listing_type && (
                <View style={styles.badgeType}>
                  <Text style={styles.badgeTypeText}>{item.listing_type}</Text>
                </View>
              )}
            </View>
            {item.investment_score && (
              <View style={styles.scoreRow}>
                <Text style={styles.scoreText}>
                  Score: {item.investment_score}/100
                </Text>
              </View>
            )}
          </TouchableOpacity>
        )}
      />

      {/* ─── Saved Searches Modal ─────────────────────── */}
      <Modal visible={showSaved} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>📁 Saved Searches</Text>
              <TouchableOpacity onPress={() => setShowSaved(false)}>
                <Text style={styles.modalClose}>✕</Text>
              </TouchableOpacity>
            </View>

            {loadingSaved ? (
              <ActivityIndicator size="small" color="#4f46e5" style={{ margin: 20 }} />
            ) : savedSearches.length === 0 ? (
              <View style={styles.emptyModal}>
                <Text style={styles.emptyIcon}>📂</Text>
                <Text style={styles.emptyText}>No saved searches yet</Text>
                <Text style={styles.emptySubtext}>
                  Run a search then tap “Save This Search”
                </Text>
              </View>
            ) : (
              <FlatList
                data={savedSearches}
                keyExtractor={item => item.id}
                renderItem={({ item }) => (
                  <TouchableOpacity
                    style={styles.savedItem}
                    onPress={() => runSavedSearch(item)}
                  >
                    <View style={{ flex: 1 }}>
                      <Text style={styles.savedName}>{item.name}</Text>
                      <Text style={styles.savedQuery} numberOfLines={1}>
                        {item.query || item.ai_query || item.filter_type}
                      </Text>
                      <Text style={styles.savedMeta}>
                        {item.result_count != null
                          ? `📊 ${item.result_count} results`
                          : '🔄 Never run'}
                        {item.last_run_at && ' • ' + new Date(item.last_run_at).toLocaleDateString()}
                      </Text>
                    </View>
                    <TouchableOpacity
                      style={styles.deleteBtn}
                      onPress={() => deleteSavedSearch(item.id, item.name)}
                    >
                      <Text style={styles.deleteBtnText}>🗑️</Text>
                    </TouchableOpacity>
                  </TouchableOpacity>
                )}
              />
            )}
          </View>
        </View>
      </Modal>

      {/* ─── Save Search Modal ───────────────────────── */}
      <Modal visible={showSaveModal} animationType="fade" transparent>
        <View style={styles.modalOverlay}>
          <View style={styles.saveModal}>
            <Text style={styles.modalTitle}>💾 Save Search</Text>
            <TextInput
              style={styles.saveInput}
              placeholder="Name this search..."
              placeholderTextColor="#99a"
              value={saveName}
              onChangeText={setSaveName}
              autoFocus
            />
            <TextInput
              style={[styles.saveInput, styles.saveInputMultiline]}
              placeholder="Notes (optional)"
              placeholderTextColor="#99a"
              value={saveNotes}
              onChangeText={setSaveNotes}
              multiline
              numberOfLines={2}
            />
            <View style={styles.saveModalActions}>
              <TouchableOpacity
                style={styles.cancelBtn}
                onPress={() => setShowSaveModal(false)}
              >
                <Text style={styles.cancelBtnText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.confirmBtn}
                onPress={saveCurrentSearch}
              >
                <Text style={styles.confirmBtnText}>Save</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

// ─── Styles ──────────────────────────────────────────────
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f5f5' },

  header: { backgroundColor: '#1a1a2e', paddingTop: 60, paddingBottom: 16, paddingHorizontal: 20 },
  headerRow: { flexDirection: 'row', alignItems: 'center' },
  headerTitle: { fontSize: 22, fontWeight: '700', color: '#fff' },
  headerSub: { fontSize: 13, color: '#8899aa', marginTop: 4 },
  savedBtn: {
    backgroundColor: '#ffffff15', paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 10, borderWidth: 1, borderColor: '#ffffff20',
  },
  savedBtnText: { fontSize: 16, color: '#fff' },

  searchBar: {
    flexDirection: 'row', marginHorizontal: 16, marginTop: -20,
    backgroundColor: '#fff', borderRadius: 12,
    shadowColor: '#000', shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.08, shadowRadius: 20, elevation: 4,
    borderWidth: 2, borderColor: 'transparent',
  },
  searchBarActive: { borderColor: '#4f46e5' },
  searchInput: { flex: 1, paddingHorizontal: 16, paddingVertical: 14, fontSize: 15, color: '#333' },
  searchBtn: { paddingHorizontal: 16, justifyContent: 'center' },
  searchBtnText: { fontSize: 18 },

  chips: { paddingVertical: 12, paddingHorizontal: 16 },
  chip: {
    paddingHorizontal: 14, paddingVertical: 6, borderRadius: 20,
    borderWidth: 1.5, borderColor: '#e0e0e0', backgroundColor: '#fff',
    marginRight: 8,
  },
  chipActive: { backgroundColor: '#4f46e5', borderColor: '#4f46e5' },
  chipText: { fontSize: 13, color: '#444' },
  chipTextActive: { color: '#fff' },
  chipAi: { borderColor: '#667eea', backgroundColor: '#f0eeff' },

  aiSection: {
    marginHorizontal: 16, padding: 14, borderRadius: 12,
    backgroundColor: '#f8f6ff', borderWidth: 1, borderColor: '#667eea30',
  },
  aiLabel: { fontSize: 11, fontWeight: '600', color: '#667eea', textTransform: 'uppercase', letterSpacing: 1 },
  aiInput: { fontSize: 14, paddingVertical: 8, color: '#333', borderBottomWidth: 1, borderBottomColor: '#e0e0e0' },
  aiHint: { fontSize: 11, color: '#8899aa', marginTop: 6 },

  saveSearchBtn: {
    marginHorizontal: 16, marginTop: 8, marginBottom: 4,
    backgroundColor: '#ecfdf5', borderRadius: 10,
    paddingVertical: 10, alignItems: 'center',
    borderWidth: 1, borderColor: '#a7f3d0',
  },
  saveSearchBtnText: { fontSize: 14, fontWeight: '600', color: '#059669' },

  resultsHeader: {
    flexDirection: 'row', justifyContent: 'space-between',
    paddingHorizontal: 4, paddingTop: 12, paddingBottom: 6,
  },
  resultsCount: { fontSize: 15, fontWeight: '600', color: '#333' },
  refreshBtn: { fontSize: 13, color: '#4f46e5', fontWeight: '500' },
  resultsList: { paddingHorizontal: 16, paddingBottom: 100 },

  empty: { alignItems: 'center', paddingTop: 60 },
  emptyIcon: { fontSize: 48, marginBottom: 12 },
  emptyText: { fontSize: 15, color: '#8899aa', textAlign: 'center' },
  emptySubtext: { fontSize: 13, color: '#aab', textAlign: 'center', marginTop: 4 },

  runSavedBtn: {
    marginTop: 20, backgroundColor: '#f0f0ff', borderRadius: 12,
    paddingVertical: 12, paddingHorizontal: 20,
  },
  runSavedBtnText: { fontSize: 14, fontWeight: '600', color: '#4f46e5' },

  card: {
    backgroundColor: '#fff', borderRadius: 14, padding: 14,
    marginBottom: 10, borderWidth: 1, borderColor: '#eee',
  },
  cardTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  cardAddress: { fontSize: 14, fontWeight: '600', color: '#1a1a2e', flex: 1, marginRight: 8 },
  cardPrice: { fontSize: 16, fontWeight: '700', color: '#059669' },
  cardDetails: { flexDirection: 'row', gap: 12, marginTop: 6, marginBottom: 8 },
  cardBadges: { flexDirection: 'row', gap: 4, flexWrap: 'wrap' },

  badgeDistressed: { backgroundColor: '#fee2e2', borderRadius: 10, paddingHorizontal: 8, paddingVertical: 2 },
  badgeDistressedText: { fontSize: 10, fontWeight: '600', color: '#dc2626' },
  badgeWarning: { backgroundColor: '#fef3c7', borderRadius: 10, paddingHorizontal: 8, paddingVertical: 2 },
  badgeWarningText: { fontSize: 10, fontWeight: '600', color: '#d97706' },
  badgeType: { backgroundColor: '#ede9fe', borderRadius: 10, paddingHorizontal: 8, paddingVertical: 2 },
  badgeTypeText: { fontSize: 10, fontWeight: '600', color: '#7c3aed' },

  scoreRow: { marginTop: 6 },
  scoreText: { fontSize: 11, color: '#666', fontWeight: '500' },

  // Modal
  modalOverlay: {
    flex: 1, backgroundColor: '#00000060',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: '#fff', borderTopLeftRadius: 24, borderTopRightRadius: 24,
    maxHeight: '70%', paddingBottom: 40,
  },
  modalHeader: {
    flexDirection: 'row', justifyContent: 'space-between',
    padding: 20, borderBottomWidth: 1, borderBottomColor: '#eee',
  },
  modalTitle: { fontSize: 18, fontWeight: '700', color: '#1a1a2e' },
  modalClose: { fontSize: 22, color: '#8899aa' },

  savedItem: {
    flexDirection: 'row', alignItems: 'center',
    padding: 16, borderBottomWidth: 1, borderBottomColor: '#f0f0f0',
  },
  savedName: { fontSize: 15, fontWeight: '600', color: '#1a1a2e' },
  savedQuery: { fontSize: 13, color: '#667', marginTop: 2 },
  savedMeta: { fontSize: 11, color: '#99a', marginTop: 4 },
  deleteBtn: { padding: 8, marginLeft: 8 },
  deleteBtnText: { fontSize: 18 },

  emptyModal: { alignItems: 'center', padding: 40 },

  // Save modal
  saveModal: {
    backgroundColor: '#fff', margin: 24, borderRadius: 20, padding: 24,
  },
  saveInput: {
    borderWidth: 1, borderColor: '#e0e0e0', borderRadius: 10,
    paddingHorizontal: 14, paddingVertical: 12, fontSize: 15,
    color: '#333', marginTop: 12,
  },
  saveInputMultiline: { height: 60, textAlignVertical: 'top' },
  saveModalActions: {
    flexDirection: 'row', justifyContent: 'flex-end', gap: 10, marginTop: 16,
  },
  cancelBtn: { paddingHorizontal: 20, paddingVertical: 10, borderRadius: 10 },
  cancelBtnText: { fontSize: 15, color: '#667' },
  confirmBtn: {
    backgroundColor: '#4f46e5', paddingHorizontal: 24, paddingVertical: 10,
    borderRadius: 10,
  },
  confirmBtnText: { fontSize: 15, fontWeight: '600', color: '#fff' },
});
