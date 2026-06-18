import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { useAuth } from '../../src/context/AuthContext';
import { contractsApi, ticketsApi } from '../../src/services/api';
import type { Contract, Ticket } from '../../src/types';

export default function DashboardScreen() {
  const { user } = useAuth();
  const router = useRouter();
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([ticketsApi.list(), contractsApi.list()])
      .then(([t, c]) => {
        setTickets(t.tickets);
        setContracts(c.contracts);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const openTickets = tickets.filter((t) =>
    ['open', 'in_progress', 'awaiting_parts'].includes(t.status),
  );
  const activeContracts = contracts.filter((c) => c.status_class === 'active');

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#ffffff" />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20, gap: 20 }}>
      <Text style={styles.greeting}>Hello, {user?.name?.split(' ')[0] ?? 'there'} 👋</Text>

      {/* Quick stats */}
      <View style={styles.statsRow}>
        <StatCard label="Open Tickets" value={String(openTickets.length)} color="#f59e0b" icon="construct-outline" />
        <StatCard label="Active Contracts" value={String(activeContracts.length)} color="#10b981" icon="document-text-outline" />
      </View>

      {/* Recent tickets */}
      <View style={styles.section}>
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>Recent Tickets</Text>
          <TouchableOpacity onPress={() => router.push('/(tabs)/tickets')}>
            <Text style={styles.seeAll}>See all</Text>
          </TouchableOpacity>
        </View>
        {tickets.slice(0, 3).map((t) => (
          <TouchableOpacity
            key={t.id}
            style={styles.ticketRow}
            onPress={() => router.push(`/(tabs)/tickets/${t.id}`)}
            activeOpacity={0.8}
          >
            <View style={[styles.statusDot, { backgroundColor: statusColor(t.status) }]} />
            <View style={{ flex: 1 }}>
              <Text style={styles.ticketTitle} numberOfLines={1}>{t.title}</Text>
              <Text style={styles.ticketMeta}>{t.status.replace('_', ' ')} · {t.priority}</Text>
            </View>
            <Ionicons name="chevron-forward" size={16} color="#6b7280" />
          </TouchableOpacity>
        ))}
        {tickets.length === 0 && (
          <Text style={styles.emptyText}>No tickets yet.</Text>
        )}
      </View>

      {/* Contracts */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Contracts</Text>
        {contracts.map((c) => (
          <View key={c.id} style={styles.contractRow}>
            <Ionicons name="document-text-outline" size={18} color="#9ca3af" style={{ marginRight: 12 }} />
            <View style={{ flex: 1 }}>
              <Text style={styles.contractName}>{c.property_name}</Text>
              <Text style={styles.contractDates}>
                {c.start_date} → {c.end_date}
              </Text>
            </View>
            <View style={[styles.statusBadge, { backgroundColor: contractStatusColor(c.status_class) }]}>
              <Text style={styles.statusBadgeText}>{c.status_class}</Text>
            </View>
          </View>
        ))}
        {contracts.length === 0 && (
          <Text style={styles.emptyText}>No contracts on file.</Text>
        )}
      </View>

      <TouchableOpacity
        style={styles.newTicketBtn}
        onPress={() => router.push('/(tabs)/tickets/new')}
        activeOpacity={0.8}
      >
        <Ionicons name="add-circle-outline" size={20} color="#111518" />
        <Text style={styles.newTicketBtnText}>Submit a Repair Ticket</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

function StatCard({ label, value, color, icon }: { label: string; value: string; color: string; icon: keyof typeof Ionicons.glyphMap }) {
  return (
    <View style={[styles.statCard, { borderLeftColor: color }]}>
      <Ionicons name={icon} size={22} color={color} />
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

function statusColor(status: string): string {
  const map: Record<string, string> = {
    open: '#f59e0b',
    in_progress: '#3b82f6',
    awaiting_parts: '#8b5cf6',
    resolved: '#10b981',
    closed: '#6b7280',
  };
  return map[status] ?? '#6b7280';
}

function contractStatusColor(status: string): string {
  const map: Record<string, string> = {
    active: '#10b981',
    pending: '#f59e0b',
    ended: '#6b7280',
  };
  return map[status] ?? '#6b7280';
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#111518' },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#111518' },
  greeting: { color: '#ffffff', fontSize: 22, fontWeight: '700' },
  statsRow: { flexDirection: 'row', gap: 12 },
  statCard: { flex: 1, backgroundColor: '#1e2328', borderRadius: 12, padding: 16, gap: 6, borderLeftWidth: 4 },
  statValue: { color: '#ffffff', fontSize: 28, fontWeight: '700' },
  statLabel: { color: '#9ca3af', fontSize: 12 },
  section: { backgroundColor: '#1e2328', borderRadius: 12, padding: 16, gap: 12 },
  sectionHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  sectionTitle: { color: '#ffffff', fontSize: 16, fontWeight: '600' },
  seeAll: { color: '#9ca3af', fontSize: 13 },
  ticketRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: '#374151' },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
  ticketTitle: { color: '#ffffff', fontSize: 14, fontWeight: '500' },
  ticketMeta: { color: '#9ca3af', fontSize: 12, marginTop: 2, textTransform: 'capitalize' },
  contractRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: '#374151' },
  contractName: { color: '#ffffff', fontSize: 14 },
  contractDates: { color: '#9ca3af', fontSize: 12, marginTop: 2 },
  statusBadge: { borderRadius: 9999, paddingHorizontal: 10, paddingVertical: 4 },
  statusBadgeText: { color: '#ffffff', fontSize: 11, fontWeight: '600', textTransform: 'capitalize' },
  newTicketBtn: { backgroundColor: '#ffffff', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 14, borderRadius: 9999 },
  newTicketBtnText: { color: '#111518', fontSize: 15, fontWeight: '600' },
  emptyText: { color: '#6b7280', fontSize: 14, textAlign: 'center', paddingVertical: 8 },
});
