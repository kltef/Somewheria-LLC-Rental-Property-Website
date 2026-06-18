import { Ionicons } from '@expo/vector-icons';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { adminApi } from '../../../src/services/api';
import type { AdminDashboard } from '../../../src/types';

export default function AdminDashboardScreen() {
  const [data, setData] = useState<AdminDashboard | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminApi
      .dashboard()
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#f59e0b" />
      </View>
    );
  }

  if (!data) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>Failed to load dashboard.</Text>
      </View>
    );
  }

  const statusCounts = data.ticket_status_counts ?? {};

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20, gap: 20 }}>
      <Text style={styles.heading}>Admin Dashboard</Text>

      {/* Ticket summary */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Tickets</Text>
        <View style={styles.kpiGrid}>
          <KpiCard label="Total" value={data.tickets.total} color="#9ca3af" icon="construct-outline" />
          <KpiCard label="Open" value={data.tickets.open} color="#f59e0b" icon="alert-circle-outline" />
          <KpiCard label="Urgent" value={data.tickets.urgent} color="#ef4444" icon="flame-outline" />
        </View>
      </View>

      {/* Status breakdown */}
      {Object.keys(statusCounts).length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>By Status</Text>
          <View style={styles.kpiGrid}>
            {Object.entries(statusCounts).map(([status, count]) => (
              <KpiCard
                key={status}
                label={status.replace('_', ' ')}
                value={count}
                color={STATUS_COLORS[status] ?? '#6b7280'}
                icon="ellipse-outline"
              />
            ))}
          </View>
        </View>
      )}

      {/* Other KPIs */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Overview</Text>
        <View style={styles.kpiGrid}>
          <KpiCard label="Properties" value={data.property_count} color="#3b82f6" icon="home-outline" />
          <KpiCard label="Pending Regs" value={data.pending_registrations} color="#f59e0b" icon="person-add-outline" />
        </View>
      </View>
    </ScrollView>
  );
}

const STATUS_COLORS: Record<string, string> = {
  open: '#f59e0b',
  in_progress: '#3b82f6',
  awaiting_parts: '#8b5cf6',
  resolved: '#10b981',
  closed: '#6b7280',
};

function KpiCard({
  label,
  value,
  color,
  icon,
}: {
  label: string;
  value: number;
  color: string;
  icon: keyof typeof Ionicons.glyphMap;
}) {
  return (
    <View style={[styles.kpiCard, { borderLeftColor: color }]}>
      <Ionicons name={icon} size={18} color={color} />
      <Text style={styles.kpiValue}>{value}</Text>
      <Text style={styles.kpiLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0d1117' },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#0d1117' },
  heading: { color: '#ffffff', fontSize: 22, fontWeight: '700' },
  section: { backgroundColor: '#1e2328', borderRadius: 12, padding: 16, gap: 12 },
  sectionTitle: { color: '#ffffff', fontSize: 15, fontWeight: '600' },
  kpiGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  kpiCard: {
    flex: 1,
    minWidth: '45%',
    backgroundColor: '#111518',
    borderRadius: 10,
    padding: 14,
    gap: 6,
    borderLeftWidth: 3,
  },
  kpiValue: { color: '#ffffff', fontSize: 26, fontWeight: '700' },
  kpiLabel: { color: '#9ca3af', fontSize: 11, textTransform: 'capitalize' },
  errorText: { color: '#ef4444', fontSize: 15 },
});
