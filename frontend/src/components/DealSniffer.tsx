import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

// ─── Props ──────────────────────────────────────────────
interface DealSnifferProps {
  score?: number;          // 0-100 deal quality score
  size?: 'sm' | 'md' | 'lg';
  inline?: boolean;        // Compact inline version for cards
  analysis?: string;       // Chef's commentary on the deal
  showProfile?: boolean;   // Full profile card
}

// ─── Chef's Sayings (by score range) ────────────────────
const CHEF_SAYS: Record<string, string[]> = {
  hot: [
    "This one smells like victory! 🐾",
    "I'd fetch this deal all day! 🦴",
    "My tail's wagging — BUY BUY BUY! 🐕",
    "Sniffed it twice. It's GOLD. 🏆",
  ],
  warm: [
    "Not bad... not bad at all. 🤔",
    "I smell potential here. 🐾",
    "Worth a second sniff. 👃",
    "Could be a good one with some love. ❤️",
  ],
  cold: [
    "Hmm... something's off. 🤨",
    "My nose says pass on this one. 🚫",
    "Not worth fetching. 😕",
    "Sniff test failed. Next! 👋",
  ],
  default: [
    "Sniff sniff... let me analyze this... 🐕",
    "Give me a moment, I'm sniffing... 👃",
  ],
};

function getChefSaying(score?: number): string {
  if (score === undefined) {
    const d = CHEF_SAYS.default;
    return d[Math.floor(Math.random() * d.length)];
  }
  if (score >= 75) {
    const h = CHEF_SAYS.hot;
    return h[Math.floor(Math.random() * h.length)];
  }
  if (score >= 45) {
    const w = CHEF_SAYS.warm;
    return w[Math.floor(Math.random() * w.length)];
  }
  const c = CHEF_SAYS.cold;
  return c[Math.floor(Math.random() * c.length)];
}

function scoreColor(score?: number): string {
  if (!score) return '#8899aa';
  if (score >= 75) return '#059669';
  if (score >= 45) return '#d97706';
  return '#dc2626';
}

function scoreLabel(score?: number): string {
  if (!score) return 'Analyzing...';
  if (score >= 75) return '✅ Chef Approved!';
  if (score >= 45) return '👃 Worth a Sniff';
  return '🚫 Pass';
}

// ─── Component ──────────────────────────────────────────
export default function DealSniffer(props: DealSnifferProps) {
  const { score, size = 'md', inline, analysis, showProfile } = props;
  const saying = analysis || getChefSaying(score);

  // Inline badge for property cards
  if (inline) {
    return (
      <View style={[styles.inlineBadge, { backgroundColor: scoreColor(score) + '20', borderColor: scoreColor(score) + '40' }]}>
        <Text style={styles.inlineEmoji}>🐾</Text>
        <Text style={[styles.inlineLabel, { color: scoreColor(score) }]}>
          {scoreLabel(score)}
        </Text>
        {score != null && (
          <Text style={[styles.inlineScore, { color: scoreColor(score) }]}>
            {score}
          </Text>
        )}
      </View>
    );
  }

  // Full mascot display
  return (
    <View style={styles.container}>
      {/* Avatar */}
      <View style={[styles.avatar, size === 'sm' && styles.avatarSm, size === 'lg' && styles.avatarLg]}>
        <Text style={[styles.avatarEmoji, size === 'sm' && styles.emojiSm, size === 'lg' && styles.emojiLg]}>
          🐕‍🦺
        </Text>
      </View>

      {/* Name & Title */}
      <Text style={[styles.name, size === 'sm' && styles.nameSm]}>Chef Deal Sniffer</Text>
      <Text style={styles.title}>🍽️ Chief Investment Officer</Text>

      {/* Score Bar (if score provided) */}
      {score != null && (
        <View style={styles.scoreSection}>
          <View style={styles.scoreRow}>
            <Text style={styles.scoreLabel}>🐾 Deal Sniffer Score</Text>
            <Text style={[styles.scoreValue, { color: scoreColor(score) }]}>
              {score}
            </Text>
          </View>
          <View style={styles.scoreBarBg}>
            <View style={[styles.scoreBarFill, { width: `${score}%`, backgroundColor: scoreColor(score) }]} />
          </View>
          <View style={styles.scoreRow}>
            <Text style={styles.scoreSub}>{scoreLabel(score)}</Text>
            <Text style={styles.scoreSub}>🐕 Sniffed & Approved</Text>
          </View>
        </View>
      )}

      {/* Chef's Commentary */}
      <View style={styles.speechBubble}>
        <Text style={styles.speechIcon}>🐾</Text>
        <Text style={styles.speechText}>{saying}</Text>
      </View>

      {/* Profile Bio */}
      {showProfile && (
        <View style={styles.bio}>
          <Text style={styles.bioText}>
            Sniffing out the best deals in Fort Worth — one property at a time. 
            If Chef doesn’t approve it, neither should you.
          </Text>
          <Text style={styles.bioMemorial}>
            🐾 In loving memory of a very good dog who found the best deals 🐾
          </Text>
        </View>
      )}
    </View>
  );
}

// ─── Styles ──────────────────────────────────────────────
const styles = StyleSheet.create({
  container: { alignItems: 'center', paddingVertical: 12 },

  avatar: {
    width: 80, height: 80, borderRadius: 40,
    backgroundColor: '#fef3c7', justifyContent: 'center', alignItems: 'center',
    shadowColor: '#f59e0b', shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2, shadowRadius: 12, elevation: 4,
  },
  avatarSm: { width: 48, height: 48, borderRadius: 24 },
  avatarLg: { width: 120, height: 120, borderRadius: 60 },
  avatarEmoji: { fontSize: 40 },
  emojiSm: { fontSize: 24 },
  emojiLg: { fontSize: 60 },

  name: { fontSize: 20, fontWeight: '800', color: '#1a1a2e', marginTop: 8 },
  nameSm: { fontSize: 16 },
  title: { fontSize: 12, color: '#e94560', fontWeight: '600', textTransform: 'uppercase', letterSpacing: 1, marginTop: 2 },

  scoreSection: {
    width: '100%', paddingHorizontal: 20, marginTop: 12,
    backgroundColor: '#fffbeb', borderRadius: 12, padding: 14,
    borderWidth: 1, borderColor: '#fde68a',
  },
  scoreRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  scoreLabel: { fontSize: 11, fontWeight: '600', color: '#92400e', textTransform: 'uppercase', letterSpacing: 0.5 },
  scoreValue: { fontSize: 24, fontWeight: '800' },
  scoreBarBg: { height: 6, backgroundColor: '#fde68a', borderRadius: 3, marginTop: 6, overflow: 'hidden' },
  scoreBarFill: { height: '100%', borderRadius: 3 },
  scoreSub: { fontSize: 10, color: '#92400e', marginTop: 4 },

  speechBubble: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: '#fffbeb', borderRadius: 12, padding: 12,
    marginTop: 12, borderWidth: 1, borderColor: '#fde68a',
    marginHorizontal: 20,
  },
  speechIcon: { fontSize: 16 },
  speechText: { fontSize: 13, color: '#92400e', flex: 1, lineHeight: 18 },

  bio: { paddingHorizontal: 24, paddingTop: 12, alignItems: 'center' },
  bioText: { fontSize: 13, color: '#667', textAlign: 'center', lineHeight: 20 },
  bioMemorial: { fontSize: 12, color: '#8899aa', textAlign: 'center', marginTop: 12, fontStyle: 'italic' },

  // Inline badge for cards
  inlineBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8,
    borderWidth: 1, alignSelf: 'flex-start',
  },
  inlineEmoji: { fontSize: 11 },
  inlineLabel: { fontSize: 10, fontWeight: '600' },
  inlineScore: { fontSize: 11, fontWeight: '700' },
});
