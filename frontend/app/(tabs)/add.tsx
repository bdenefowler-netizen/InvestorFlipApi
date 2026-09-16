import { useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as DocumentPicker from "expo-document-picker";
import { useRouter } from "expo-router";
import { uploadCountyFile, type CountyUploadSource, type PublicUploadAsset, type PublicUploadResult } from "@/src/lib/publicUpload";
import { colors, radius, spacing } from "@/src/theme/tokens";

const SOURCES: {key: CountyUploadSource; label: string; hint: string}[] = [
  { key: "tad", label: "TAD", hint: "Appraisal / TAD property records" },
  { key: "tax", label: "Tax", hint: "Tax Roll or Tax Due" },
  { key: "pre_foreclosure", label: "Pre-Foreclosure", hint: "Foreclosure / auction records" },
  { key: "probate", label: "Probate", hint: "Decedent Property Address is primary" },
  { key: "code_violations", label: "Code Violations", hint: "Municipal code / complaint records" },
  { key: "owner", label: "Owner", hint: "Owner name, phone, email, mailing" },
];

export default function AddScreen() {
  const router = useRouter();
  const [source, setSource] = useState<CountyUploadSource>("tad");
  const [file, setFile] = useState<PublicUploadAsset | null>(null);
  const [result, setResult] = useState<PublicUploadResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const choose = async () => {
    setError(null); setResult(null);
    const picked = await DocumentPicker.getDocumentAsync({
      type: ["text/csv","application/csv","application/vnd.ms-excel","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","application/zip","application/x-zip-compressed"],
      copyToCacheDirectory: true, multiple: false,
    });
    if (picked.canceled || !picked.assets?.length) return;
    const a = picked.assets[0];
    if ((a.size || 0) > 300 * 1024 * 1024) return setError("File is over 300 MB. Split it into smaller uploads.");
    setFile({ uri: a.uri, name: a.name, mimeType: a.mimeType, size: a.size, file: (a as any).file });
  };

  const upload = async () => {
    if (!file) return;
    setBusy(true); setError(null); setResult(null);
    try { setResult(await uploadCountyFile(file, source)); }
    catch (e: any) { setError(e?.message || "Upload failed."); }
    finally { setBusy(false); }
  };

  const active = SOURCES.find(x => x.key === source)!;

  return <SafeAreaView style={s.safe} edges={["top"]}>
    <View style={s.header}>
      <Text style={s.eyebrow}>ADD · IMPORT · MATCH</Text>
      <Text style={s.title}>Upload County Data</Text>
      <Text style={s.sub}>Choose the source first. InvestorFlip keeps that identity, matches the property, then enriches missing details.</Text>
    </View>
    <ScrollView contentContainerStyle={s.content}>
      <View style={s.card}>
        <Text style={s.step}>1 · CHOOSE SOURCE</Text>
        {SOURCES.map(o => {
          const on = source === o.key;
          return <Pressable key={o.key} onPress={() => {setSource(o.key); setResult(null); setError(null);}} style={[s.source, on && s.sourceOn]}>
            <Text style={[s.sourceTitle, on && s.white]}>{o.label}</Text>
            <Text style={[s.hint, on && s.whiteSoft]}>{o.hint}</Text>
          </Pressable>;
        })}
      </View>

      <View style={s.card}>
        <Text style={s.step}>2 · {active.label.toUpperCase()} FILE</Text>
        <Pressable disabled={busy} onPress={choose} style={s.choose}>
          <Ionicons name="folder-open-outline" size={18} color={colors.brandPrimary}/>
          <Text style={s.chooseText}>Choose File</Text>
        </Pressable>

        {file ? <View style={s.file}>
          <Text style={s.fileName}>{file.name || "Selected file"}</Text>
          <Text style={s.hint}>{file.size ? `${(file.size/1024/1024).toFixed(2)} MB` : "Ready"} · {active.label}</Text>
        </View> : null}

        <Pressable disabled={!file || busy} onPress={upload} style={[s.upload, (!file || busy) && s.disabled]}>
          {busy ? <ActivityIndicator color="#fff"/> : <Ionicons name="cloud-upload-outline" size={18} color="#fff"/>}
          <Text style={s.uploadText}>{busy ? `Uploading ${active.label}…` : `Upload ${active.label}`}</Text>
        </Pressable>

        {result ? <View style={s.ok}>
          <Text style={s.okTitle}>✓ {active.label} accepted</Text>
          <Text style={s.hint}>{result.accepted} accepted · {result.inserted} new · {result.updated} updated · {result.rejected} rejected</Text>
          <Pressable onPress={() => router.push("/county" as any)}><Text style={s.link}>Open County Records →</Text></Pressable>
        </View> : null}
        {error ? <View style={s.err}><Text style={s.errText}>{error}</Text></View> : null}
      </View>

      <View style={s.card}>
        <Text style={s.step}>MATCHING RULES</Text>
        <Text style={s.ruleStrong}>TAD Account # or APN exact = verified match.</Text>
        <Text style={s.rule}>If missing: normalized Property Address → Legal Description.</Text>
        <Text style={s.rule}>No match: keep the row and enrich later.</Text>
        <Text style={s.rule}>Owner phone/email enrich only after the property is identified.</Text>
      </View>
    </ScrollView>
  </SafeAreaView>;
}

const s = StyleSheet.create({
  safe:{flex:1,backgroundColor:colors.surface},
  header:{padding:spacing.lg,borderBottomWidth:1,borderBottomColor:colors.border},
  eyebrow:{fontSize:10,fontWeight:"800",letterSpacing:1.1,color:colors.muted},
  title:{fontSize:27,fontWeight:"800",color:colors.onSurface,marginTop:4},
  sub:{fontSize:12,lineHeight:18,color:colors.muted,marginTop:5},
  content:{padding:spacing.lg,paddingBottom:120,gap:spacing.md},
  card:{backgroundColor:colors.surfaceSecondary,borderWidth:1,borderColor:colors.border,borderRadius:radius.lg,padding:spacing.lg,gap:10},
  step:{fontSize:10,fontWeight:"800",letterSpacing:1,color:colors.muted},
  source:{borderWidth:1,borderColor:colors.border,borderRadius:radius.md,padding:12,backgroundColor:colors.surface},
  sourceOn:{backgroundColor:colors.brandPrimary,borderColor:colors.brandPrimary},
  sourceTitle:{fontSize:13,fontWeight:"800",color:colors.onSurface},
  hint:{fontSize:10,color:colors.muted,marginTop:2},
  white:{color:"#fff"},whiteSoft:{color:"rgba(255,255,255,.8)"},
  choose:{minHeight:48,borderWidth:1,borderColor:colors.brandPrimary,borderRadius:radius.md,flexDirection:"row",alignItems:"center",justifyContent:"center",gap:8},
  chooseText:{fontSize:13,fontWeight:"800",color:colors.brandPrimary},
  file:{padding:11,borderWidth:1,borderColor:colors.border,borderRadius:radius.md,backgroundColor:colors.surface},
  fileName:{fontSize:12,fontWeight:"800",color:colors.onSurface},
  upload:{minHeight:50,borderRadius:radius.md,backgroundColor:colors.brandPrimary,flexDirection:"row",alignItems:"center",justifyContent:"center",gap:8},
  uploadText:{fontSize:13,fontWeight:"800",color:"#fff"},disabled:{opacity:.5},
  ok:{padding:12,borderRadius:radius.md,backgroundColor:"#E8F0EB",borderWidth:1,borderColor:"#C9DACE"},
  okTitle:{fontSize:13,fontWeight:"800",color:colors.onSurface},
  link:{marginTop:8,fontSize:11,fontWeight:"800",color:colors.brandPrimary},
  err:{padding:12,borderRadius:radius.md,backgroundColor:"#F8E9E7",borderWidth:1,borderColor:"#ECC7C3"},
  errText:{fontSize:12,fontWeight:"700",color:colors.error},
  ruleStrong:{fontSize:12,fontWeight:"800",color:colors.onSurface},
  rule:{fontSize:11,lineHeight:17,color:colors.muted},
});
