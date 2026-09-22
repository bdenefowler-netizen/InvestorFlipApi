import { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  Pressable,
  ActivityIndicator,
  Linking,
  Platform,
  TextInput,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { colors, radius, spacing, tabularNums } from "@/src/theme/tokens";
import { API_BASE } from "@/src/lib/api";
import { getStoredAdminKey, saveAdminKey } from "@/src/lib/admin";
import { exportUrl } from "@/src/lib/feeds";

type CoreSource = "tad" | "tax_roll" | "code_violations";
type RunState = "idle" | "running" | "ok" | "error";

type SourceRun = {
  state: RunState;
  message: string;
};

const CORE: { key: CoreSource; label: string; note: string }[] = [
  {
    key: "tad",
    label: "TAD",
    note: "Tarrant Appraisal District parcel/property records",
  },
  {
    key: "tax_roll",
    label: "Tax Roll",
    note: "Official Tarrant County weekly current + delinquent tax roll",
  },
  {
    key: "code_violations",
    label: "Code Violations",
    note: "Fort Worth open code cases",
  },
];

const initialRuns = (): Record<CoreSource, SourceRun> => ({
  tad: { state: "idle", message: "Ready" },
  tax_roll: { state: "idle", message: "Ready" },
  code_violations: { state: "idle", message: "Ready" },
});

function compact(value: any): string {
  if (!value) return "Completed";
  const candidates = [
    value?.result?.inserted,
    value?.result?.updated,
    value?.result?.matched,
    value?.inserted,
    value?.updated,
    value?.matched,
  ].filter((v) => typeof v === "number");
  if (candidates.length) {
    return `Completed · ${candidates.map((v) => Number(v).toLocaleString()).join(" / ")}`;
  }
  try {
    const raw = JSON.stringify(value);
    return raw.length > 190 ? `${raw.slice(0, 187)}...` : raw;
  } catch {
    return "Completed";
  }
}

export default function SettingsScreen() {
  const [adminKey, setAdminKey] = useState("");
  const [adminSaved, setAdminSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [health, setHealth] = useState<Record<string, any>>({});
  const [brightReady, setBrightReady] = useState<boolean | null>(null);
  const [runs, setRuns] = useState<Record<CoreSource, SourceRun>>(initialRuns());
  const [labMessage, setLabMessage] = useState("");

  useEffect(() => {
    getStoredAdminKey().then((key) => {
      setAdminKey(key);
      setAdminSaved(Boolean(key));
    });
  }, []);

  const saveKey = async () => {
    const ok = await saveAdminKey(adminKey);
    setAdminSaved(ok && Boolean(adminKey.trim()));
    setLabMessage(ok ? "Admin key saved on this device." : "Could not save admin key.");
  };

  const testSources = useCallback(async () => {
    setTesting(true);
    setLabMessage("");
    try {
      const [sourceResp, brightResp] = await Promise.all([
        fetch(`${API_BASE}/api/data-sources/status`),
        fetch(`${API_BASE}/api/brightdata-mcp/status`),
      ]);
      const sourceData = await sourceResp.json().catch(() => ({}));
      const brightData = await brightResp.json().catch(() => ({}));
      setHealth(sourceData || {});
      setBrightReady(Boolean(
        brightData?.brightdata_mcp_configured ||
        brightData?.configured ||
        brightData?.token_configured
      ));
      setLabMessage("Source check finished. Nothing was written to the database.");
    } catch (e: any) {
      setLabMessage(e?.message || "Source check failed.");
    } finally {
      setTesting(false);
    }
  }, []);

  useEffect(() => {
    testSources();
  }, [testSources]);

  const runCoreSource = async (source: CoreSource) => {
    const key = (await getStoredAdminKey().trim();
    if (!key) {
      setLabMessage("Save the Railway admin key first so Serenity can run protected imports.");
      return;
    }

    setRuns(((prev) => ({
      ...prev,
      [source]: { state: "running", message: "Pulling…" },
    }));
    setLabMessage("");

    const params = new URLSearchParams({ source });
    if (source === "tad") params.set("tad_records", "2000");
    if (source === "code_violations") params.set("code_records", "5000");

    try {
      const resp = await fetch(
        `${API_BASE}/api/admin/county-records/sync?${params.toString()}`,
        {
          method: "POST",
          headers: { "x-admin-key": key },
        },
      );
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data?.detail || data?.error || `HTTP ${resp.status}`);
      }
      setRuns(((prev) => ({
        ...prev,
        [source]: { state: "ok", message: compact(data?.results?.[source] || data) },
      }));
    } catch (e: any) {
      setRuns(((prev) => ({
        ...prev,
        [source]: { state: "error", message: e?.message || "Import failed" },
      }));
    }
  };

  const runSafeCore = async () => {
    // Intentionally leave Tax Roll as its own click: the official archive is huge.
    await runCoreSource("tad");
    await runCoreSource("code_violations");
  };

  const openExport = async (format: "csv" | "xlsx") => {
    const url = exportUrl(format, "all");
    if (Platform.OS === "web") window.open(url, "_blank");
    else await Linking.openURJ(url);
  };

  const statusText = (key: string) => {
    const item = health?.[key];
    if (!item) return "Not tested";
    return item.available ? "SOURCE ONLINE" : "SOURCE DID NOT ANSWE"";
  };

  const statusGood = (key: string) => Boolean(health?.[key]?.available);

  return (
    <SafeAreaView style={s.safe} edges={["top"]}>
      <View style={s.header}>
        <Text style={s.eyebrow}>ACCOUNT » DATA</Text>
        <Text style={s.title}>Settings</Text>
      </View>

      <ScrollView contentContainerStyle={s.scroll}>
        <View style={s.card}>
          <Text style={s.section}>PRIVATE OPERATIONS</Text>
          <Text style={s.help}>
            Serenity uses the admin key only for protected imports that can write data or consume provider credits.
          </Text>
          <TextInput
            value={adminKey}
            onChangeText={(v) => {
              setAdminKey(v);
              setAdminSaved(false);
            }}
            secureTextEntry
            autoCapitalize="none"
            autoCorrect={false}
            placeholder="Railway admin key"
            placeholderTextColor={colors.muted}
            style={s.input}
          />
          <Pressable onPress={saveKey} style={s.button}>
            <Ionicons name={adminSaved ? "checkmark-circle" : "lock-closed"} size={16} color="#fff" />
            <Text style={s.buttonText}>{adminSaved ? "Key Saved" : "Save Securely"}</Text>
          </Pressable>
        </View>

        <View style={s.card} testID="serenity-data-lab">
          <View style={s.rowBetween}>
            <View style={{ flex: 1 }}>
              <Text style={s.section}>SERENITY DATA LAB</Text>
              <Text style={s.labTitle}>Live County Sources</Text>
            </View>
            <View style={s.labPill}>
              <Text style={s.labPillText}>LIVE</Text>
            </View>
          </View>

          <Text style={s.help}>
            Test the source first, then pull it straight into County Records / Property Master. Excel stays available as backup and one-off research intake.
          </Text>

          <Pressable onPress={testSources} disabled={testing} style={s.outlineButton}>
            {testing ? (
              <ActivityIndicator />
            ) : (
              <Ionicons name="pulse-outline" size={17} color={colors.brandPrimary} />
            )}
            <Text style={s.outlineText}>{testing ? "Testing sources…" : "Test All Sources"}</Text>
          </Pressable>

          {CORE.map(({ key, label, note }) => {
            const run = runs[key];
            return (
              <View key={key} style={s.sourceCard}>
                <View style={s.rowBetween}>
                  <View style={{ flex: 1 }}>
                    <Text style={s.sourceName}>{label}</Text>
                    <Text style={s.help}>{note}</Text>
                  </View>
                  <Text style={[s.health, statusGood(key) && s.healthGood]}>
                    {statusText(key)}
                  </Text>
                </View>
                <View style={s.sourceActions}>
                  <Pressable
                    onPress={() => runCoreSource(key)}
                    disabled={run.state === "running"}
                    style={[s.smallButton, run.state === "running" && { opacity: 0.55 }]}
                  >
                    {run.state === "running" ? (
                      <ActivityIndicator color="#fff" size="small" />
                    ) : (
                      <Ionicons name="cloud-download-outline" size={16} color="#fff" />
                    )}
                    <Text style={s.buttonText}>
                      {run.state === "running" ? "Pulling…" : `Pull ${label}`}
                    </Text>
                  </Pressable>
                  <Text
                    style={[
                      s.runMessage,
                      run.state === "ok" && s.ok,
                      run.state === "error" && s.bad,
                    ]}
                    numberOfLines={3}
                  >
                    {run.message}
                  </Text>
                </View>
              </View>
            );
          })}

          <Pressable onPress={runSafeCore} style={s.experiment}>
            <Ionicons name="flask-outline" size={18} color="#fff" />
            <Text style={s.buttonText}>Pull TAD + Code Now</Text>
          </Pressable>
          <Text style={s.previewOnly}>
            TAX ROLL STAYS SEPARATE · OFFICIAL FILE IS R2 MILLION RECORDS
          </Text>

          <View style={s.divider} />

          <Text style={s.section}>BRIGHT DATA SOURCES</Text>

          <View style={s.sourceCard}>
            <View style={s.rowBetween}>
              <View style={{ flex: 1 }}>
                <Text style={s.sourceName}>Pre-Foreclosure</Text>
                <Text style={s.help}>Tarrant County Official Records / Lis Pendens</Text>
              </View>
              <Text style={[s.health, s