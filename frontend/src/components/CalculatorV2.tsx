import React,{useRef,useState}from"react";
import{ActivityIndicator,Alert,Keyboard,Pressable,ScrollView,StyleSheet,Text,TextInput,View}from"react-native";
import{SafeAreaView}from"react-native-safe-area-context";
import{Ionicons}from"@expo/vector-icons";
import{API_BASE}from"@/src/lib/api";
import{lookupCalculatorDeal}from"@/src/lib/calculatorLookup";
import{colors,radius,spacing,tabularNums}from"@/src/theme/tokens";

type S={title:string;street_address:string;city?:string;state?:string;zip?:string};
type R={decision:string;max_offer?:number|null;estimated_profit?:number|null;roi_pct?:number|null;warning?:string|null;deal_reason?:string|null;deal_sniffer_score?:number|null;chef_verdict?:string|null;arv_explanation?:string|null;risk_flags?:string[]};

const n=(v:string)=>Number(v.replace(/[^0-9.]/g,""))||0;
const money=(v:number)=>`$${Math.round(v).toLocaleString()}`;

export default function CalculatorV2(){
 const[address,setAddress]=useState(""),[suggestions,setSuggestions]=useState<S[]>([]),[searching,setSearching]=useState(false),[lookupBusy,setLookupBusy]=useState(false);
 const[price,setPrice]=useState(""),[arv,setArv]=useState(""),[sqft,setSqft]=useState(""),[rehabRate,setRehabRate]=useState(""),[rent,setRent]=useState("");
 const[sources,setSources]=useState<string[]>([]),[owner,setOwner]=useState(""),[arvSource,setArvSource]=useState(""),[arvConfidence,setArvConfidence]=useState(""),[compCount,setCompCount]=useState(0);
 const[busy,setBusy]=useState(false),[result,setResult]=useState<R|null>(null),[error,setError]=useState<string|null>(null);
 const timer=useRef<ReturnType<typeof setTimeout>|null>(null);
 const purchase=n(price),area=n(sqft),rate=n(rehabRate),arvN=n(arv),rehab=Math.round(area*rate),basis=purchase+rehab,gross=arvN-basis,roi=basis?gross/basis*100:0;

 const changeAddress=(v:string)=>{
  setAddress(v);setResult(null);setError(null);if(timer.current)clearTimeout(timer.current);
  timer.current=setTimeout(async()=>{if(v.trim().length<5){setSuggestions([]);return}setSearching(true);try{const r=await fetch(`${API_BASE}/api/address-suggestions?query=${encodeURIComponent(v)}&limit=6`);const d=await r.json();setSuggestions((d.items||[]).slice(0,6))}catch{setSuggestions([])}finally{setSearching(false)}},350)
 };
 const pick=async(s:S)=>{
  const full=[s.street_address||s.title,s.city,s.state,s.zip].filter(Boolean).join(", ");setAddress(full);setSuggestions([]);setLookupBusy(true);setError(null);Keyboard.dismiss();
  try{const x=await lookupCalculatorDeal(full);if(x.purchase_price)setPrice(String(Math.round(x.purchase_price)));if(x.living_area)setSqft(String(Math.round(x.living_area)));if(x.screening_arv)setArv(String(Math.round(x.screening_arv)));setSources(x.sources);setOwner(x.county_record?.owner_name||"");setArvSource(x.arv_source);setArvConfidence(x.arv_confidence);setCompCount(x.comparable_count)}
  catch(e:any){setError(e?.message||"Lookup failed. You can still enter the deal manually.")}finally{setLookupBusy(false)}
 };
 const analyze=async()=>{
  if(!purchase)return Alert.alert("Missing Purchase Price","Enter the price you expect to pay.");
  if(!arvN)return Alert.alert("Missing ARV","Enter or verify an After Repair Value.");
  if(rate>0&&!area)return Alert.alert("Missing Square Feet","Square footage is required for rehab math.");
  setBusy(true);setResult(null);setError(null);
  try{const r=await fetch(`${API_BASE}/api/analyze/quick`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({address,price:purchase,arv:arvN,repairs:rehab,rent:n(rent)})});const d=await r.json();if(!r.ok||d.error)throw new Error(d.error||`Analysis failed (${r.status})`);setResult(d)}
  catch(e:any){setError(e?.message||"Quill could not analyze this deal.")}finally{setBusy(false)}
 };
 const reset=()=>{setAddress("");setSuggestions([]);setPrice("");setArv("");setSqft("");setRehabRate("");setRent("");setSources([]);setOwner("");setArvSource("");setArvConfidence("");setCompCount(0);setResult(null);setError(null)};

 return <SafeAreaView style={st.safe} edges={["top"]}><ScrollView contentContainerStyle={st.page} keyboardShouldPersistTaps="handled">
  <Text style={st.title}>Deal Calculator</Text><Text style={st.sub}>Property API + TAD lookup · rehab by square foot · Quill deal analysis</Text>
  <View style={st.search}><Ionicons name="search" size={18} color={colors.muted}/><TextInput style={st.searchInput} value={address} onChangeText={changeAddress} placeholder="123 Main St, Fort Worth TX" placeholderTextColor={colors.muted}/></View>
  {searching&&<ActivityIndicator style={st.loader} color={colors.brandPrimary}/>}
  {!!suggestions.length&&<View style={st.list}>{suggestions.map((s,i)=><Pressable key={`${s.street_address}-${i}`} style={st.srow} onPress={()=>pick(s)}><Ionicons name="location-outline" size={16} color={colors.brandPrimary}/><View style={{flex:1}}><Text style={st.saddr}>{s.street_address||s.title}</Text><Text style={st.smeta}>{[s.city,s.state,s.zip].filter(Boolean).join(" ")}</Text></View></Pressable>)}</View>}
  {(lookupBusy||sources.length>0||owner)&&<View style={st.match}>{lookupBusy?<Text style={st.matchText}>Searching Real Estate API + TAD…</Text>:<><Text style={st.matchTitle}>Property matched</Text><Text style={st.matchText}>Sources: {sources.join(" + ")||"manual"}</Text>{!!owner&&<Text style={st.matchText}>TAD owner: {owner}</Text>}</>}</View>}

  <Text style={st.section}>Deal Numbers</Text>
  <Field label="Purchase Price" value={price} onChange={setPrice} placeholder="150000"/>
  <Field label="After Repair Value (ARV)" value={arv} onChange={setArv} placeholder="285000"/>
  {!!arvSource&&<View style={st.note}><Text style={st.noteTitle}>ARV starting benchmark · {arvConfidence} confidence</Text><Text style={st.noteText}>{arvSource}</Text>{compCount>0&&<Text style={st.noteText}>{compCount} similar-home prices returned.</Text>}<Text style={st.warn}>Screening only — verify ARV with sold comps before making an offer.</Text></View>}

  <View style={st.rehab}><Text style={st.rehabTitle}>Rehab Calculator</Text><Text style={st.help}>Rehab $/sq ft × living sq ft = repair budget.</Text>
   <View style={st.twocol}><Field label="Rehab $ / Sq Ft" value={rehabRate} onChange={setRehabRate} placeholder="35" compact/><Field label="Living Sq Ft" value={sqft} onChange={setSqft} placeholder="1456" compact noDollar/></View>
   <View style={st.formula}><Text style={st.noteText}>{money(rate)} × {area.toLocaleString()} sf</Text><Text style={st.formulaValue}>{money(rehab)}</Text></View>
  </View>

  <View style={st.basis}><Line label="Purchase Price" value={money(purchase)}/><Line label="Rehab Budget" value={`+ ${money(rehab)}`}/><View style={st.div}/><Line label="TOTAL BASIS" value={money(basis)} strong/></View>
  <Field label="Monthly Rent (optional)" value={rent} onChange={setRent} placeholder="1800"/>
  <Pressable style={st.button} onPress={analyze} disabled={busy}>{busy?<ActivityIndicator color="#fff"/>:<Ionicons name="paw-outline" size={19} color="#fff"/>}<Text style={st.buttonText}>{busy?"QUILL IS SNIFFING…":"ANALYZE WITH QUILL"}</Text></Pressable>
  {!!error&&<Text style={st.error}>{error}</Text>}

  {result&&<View style={st.results}><View style={st.verdict}><Text style={st.decision}>{result.decision}</Text>{result.deal_sniffer_score!=null&&<Text style={st.matchText}>Quill Deal Sniffer: {result.deal_sniffer_score}/100</Text>}<Text style={st.reason}>{result.deal_reason||result.chef_verdict||"Quill finished the numbers."}</Text></View>
   <View style={st.grid}><Metric label="MAX OFFER" value={result.max_offer?money(result.max_offer):"—"}/><Metric label="REHAB" value={money(rehab)}/><Metric label="TOTAL BASIS" value={money(basis)}/></View>
   <View style={st.grid}><Metric label="ARV" value={money(arvN)}/><Metric label="SIMPLE GROSS PROFIT" value={money(gross)}/><Metric label="SIMPLE ROI" value={`${roi.toFixed(1)}%`}/></View>
   <Text style={st.disclaimer}>Simple profit = ARV − purchase − rehab. Closing, holding, financing, commissions and selling costs are not included yet.</Text>
   {!!result.arv_explanation&&<View style={st.note}><Text style={st.noteTitle}>ARV check</Text><Text style={st.noteText}>{result.arv_explanation}</Text></View>}
   {!!result.risk_flags?.length&&<View style={st.note}><Text style={st.noteTitle}>Quill flags</Text>{result.risk_flags.map((x,i)=><Text key={`${x}-${i}`} style={st.noteText}>• {x}</Text>)}</View>}
   <Pressable onPress={reset} style={st.reset}><Text style={st.resetText}>Analyze another property</Text></Pressable>
  </View>}
 </ScrollView></SafeAreaView>
}

function Field({label,value,onChange,placeholder,compact,noDollar}:{label:string;value:string;onChange:(v:string)=>void;placeholder:string;compact?:boolean;noDollar?:boolean}){return <View style={[st.field,compact&&{flex:1}]}><Text style={st.label}>{label.toUpperCase()}</Text><View style={st.inputWrap}>{!noDollar&&<Text style={st.prefix}>$</Text>}<TextInput style={st.input} value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={colors.muted} keyboardType="decimal-pad"/></View></View>}
function Line({label,value,strong}:{label:string;value:string;strong?:boolean}){return <View style={st.line}><Text style={[st.lineText,strong&&st.strong]}>{label}</Text><Text style={[st.lineText,strong&&st.strong]}>{value}</Text></View>}
function Metric({label,value}:{label:string;value:string}){return <View style={st.metric}><Text style={st.metricLabel}>{label}</Text><Text style={st.metricValue}>{value}</Text></View>}

const st=StyleSheet.create({
 safe:{flex:1,backgroundColor:colors.surface},page:{padding:spacing.lg,paddingBottom:140},title:{fontSize:27,fontWeight:"900",color:colors.onSurface},sub:{marginTop:5,marginBottom:spacing.lg,fontSize:13,lineHeight:19,color:colors.muted},
 search:{height:50,flexDirection:"row",alignItems:"center",gap:8,paddingHorizontal:spacing.md,borderWidth:1.5,borderColor:colors.border,borderRadius:radius.md,backgroundColor:colors.surfaceSecondary},searchInput:{flex:1,fontSize:15,fontWeight:"600",color:colors.onSurface},loader:{padding:10},
 list:{borderWidth:1,borderColor:colors.border,borderRadius:radius.md,overflow:"hidden",backgroundColor:colors.surfaceSecondary},srow:{flexDirection:"row",alignItems:"center",gap:9,padding:spacing.md,borderBottomWidth:StyleSheet.hairlineWidth,borderBottomColor:colors.border},saddr:{fontSize:13,fontWeight:"800",color:colors.onSurface},smeta:{fontSize:11,color:colors.muted,marginTop:2},
 match:{marginTop:spacing.md,padding:spacing.md,borderRadius:radius.md,borderWidth:1,borderColor:colors.brandPrimary+"35",backgroundColor:colors.brandPrimary+"0d"},matchTitle:{fontSize:13,fontWeight:"900",color:colors.brandPrimary},matchText:{fontSize:12,color:colors.muted,marginTop:3},
 section:{fontSize:17,fontWeight:"900",color:colors.onSurface,marginTop:spacing.lg,marginBottom:spacing.md},field:{marginBottom:spacing.md},label:{marginBottom:6,fontSize:11,fontWeight:"800",letterSpacing:.5,color:colors.onSurfaceTertiary},inputWrap:{height:48,flexDirection:"row",alignItems:"center",paddingHorizontal:spacing.md,borderWidth:1.5,borderColor:colors.border,borderRadius:radius.sm,backgroundColor:colors.surfaceSecondary},prefix:{fontSize:16,fontWeight:"800",color:colors.muted,marginRight:4},input:{flex:1,fontSize:16,fontWeight:"800",color:colors.onSurface,...tabularNums},
 note:{marginBottom:spacing.md,padding:spacing.sm,borderRadius:radius.sm,backgroundColor:colors.surfaceSecondary},noteTitle:{fontSize:11,fontWeight:"900",color:colors.onSurface},noteText:{fontSize:11,color:colors.muted,marginTop:3},warn:{fontSize:11,fontWeight:"700",color:colors.warning,marginTop:4},
 rehab:{padding:spacing.md,marginBottom:spacing.md,borderWidth:1.5,borderColor:colors.brandPrimary+"35",borderRadius:radius.md,backgroundColor:colors.brandPrimary+"09"},rehabTitle:{fontSize:15,fontWeight:"900",color:colors.onSurface},help:{fontSize:11,color:colors.muted,marginTop:3,marginBottom:spacing.md},twocol:{flexDirection:"row",gap:10},formula:{flexDirection:"row",justifyContent:"space-between",alignItems:"center",borderTopWidth:StyleSheet.hairlineWidth,borderTopColor:colors.border,paddingTop:10},formulaValue:{fontSize:18,fontWeight:"900",color:colors.brandPrimary,...tabularNums},
 rehabTitle:{fontSize:15,fontWeight:"900",color:colors.onSurface},help:{fontSize:11,color:colors.muted,marginTop:3,marginBottom:spacing.md},twocol:{flexDirection:"row",gap:10},formula:{flexDirection:"row",justifyContent:"space-between",alignItems:"center",borderTopWidth:StyleSheet.hairlineWidth,borderTopColor:colors.border,paddingTop:10},formulaValue:{fontSize:18,fontWeight:"900",color:colors.brandPrimary,...tabularNums},
 rehabTitle:{fontSize:15,fontWeight:"900",color:colors.onSurface},help:{fontSize:11,color:colors.muted,marginTop:3,marginBottom:spacing.md},twocol:{flexDirection:"row",gap:10},formula:{flexDirection:"row",justifyContent:"space-between",alignItems:"center",borderTopWidth:StyleSheet.hairlineWidth,borderTopColor:colors.border,paddingTop:10},formulaValue:{fontSize:18,fontWeight:"900",color:colors.brandPrimary,...tabularNums},
 basis:{padding:spacing.md,marginBottom:spacing.md,borderWidth:1,borderColor:colors.border,borderRadius:radius.md,backgroundColor:colors.surfaceSecondary},line:{flexDirection:"row",justifyContent:"space-between",paddingVertical:5},lineText:{fontSize:13,color:colors.muted,...tabularNums},strong:{fontWeight:"900",color:colors.onSurface},div:{height:StyleSheet.hairlineWidth,backgroundColor:colors.border,marginVertical:4},
 button:{height:53,flexDirection:"row",alignItems:"center",justifyContent:"center",gap:8,borderRadius:radius.md,backgroundColor:colors.brandPrimary},buttonText:{fontSize:15,fontWeight:"900",color:"#fff"},error:{marginTop:spacing.sm,fontSize:12,fontWeight:"700",color:colors.error},
 results:{marginTop:spacing.lg,gap:spacing.md},verdict:{padding:spacing.md,borderWidth:1.5,borderColor:colors.brandPrimary,borderRadius:radius.md},decision:{fontSize:23,fontWeight:"900",color:colors.brandPrimary},reason:{marginTop:7,fontSize:13,lineHeight:19,color:colors.onSurfaceTertiary},grid:{flexDirection:"row",gap:8},metric:{flex:1,minHeight:78,justifyContent:"center",alignItems:"center",padding:8,borderWidth:1,borderColor:colors.border,borderRadius:radius.sm,backgroundColor:colors.surfaceSecondary},metricLabel:{textAlign:"center",fontSize:9,fontWeight:"800",color:colors.muted},metricValue:{marginTop:5,textAlign:"center",fontSize:15,fontWeight:"900",color:colors.onSurface,...tabularNums},disclaimer:{fontSize:10,lineHeight:15,color:colors.muted},reset:{height:45,alignItems:"center",justifyContent:"center",borderWidth:1.5,borderColor:colors.brandPrimary,borderRadius:radius.md},resetText:{fontSize:13,fontWeight:"800",color:colors.brandPrimary}
});
