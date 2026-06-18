import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { ticketsApi } from '../../../src/services/api';
import type { Ticket } from '../../../src/types';

const STATUS_COLORS: Record<string, string> = {
  open: '#f59e0b',
  in_progress: '#3b82f6',
  awaiting_parts: '#8b5cf6',
  resolved: '#10b981',
  closed: '#6b7280',
};

export default function TicketListScreen() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    ticketsApi
      .list()
      .then((res) => setTickets(res.tickets))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#ffffff" />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <FlatList
        data={tickets}
        keyExtractor={(item) => item.id}
        contentContainerStyle={{ padding: 16, gap: 10 }}
        renderItem={({ item }) => (
          <TouchableOpacity
            style={styles.card}
            onPress={() => router.push(`/(tabs)/tickets/${item.id}`)}
            activeOpacity={0.8}
          >
            <View style={styles.cardHeader}>
              <View style={[styles.statusDot, { backgroundColor: STATUS_COLORS[item.status] ?? '#6b7280' }]} />
              <Text style={styles.title} numberOfLines={1}>{item.title}</Text>
            </View>
            <Text style={styles.meta}>
              {item.property_name || 'No property'} · {item.category} · {item.priority}
            </Text>
            <Text style={styles.status}>{item.status.replace('_', ' ')}</Text>
          </TouchableOpacity>
        )}
        ListEmptyComponent={
          <View style={styles.center}>
            <Ionicons name="construct-outline" size={40} color="#374151" />
            <Text style={styles.emptyText}>No tickets yet.</Text>
            <TouchableOpacity
              style={styles.newBtn}
              onPress={() => router.push('/(tabs)/tickets/new')}
            >
              <Text style={styles.newBtnText}>Submit a Ticket</Text>
            </TouchableOpacity>
          </View>
        }
      />
      <TouchableOpacity
        style={styles.fab}
        onPress={() => router.push('/(tabs)/tickets/new')}
        activeOpacity={0.8}
      >
        <Ionicons name="add" size={28} color="#111518" />
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#111518' },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 12, paddingTop: 60 },
  card: { backgroundColor: '#1e2328', borderRadius: 12, padding: 16, gap: 6 },
  cardHeader: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  statusDot: { width: 8, height: 8, borderRadius: 4, flexShrink: 0 },
  title: { color: '#ffffff', fontSize: 15, fontWeight: '600', flex: 1 },
  meta: { color: '#9ca3af', fontSize: 12, textTransform: 'capitalize' },
  status: { color: '#d1d5db', fontSize: 13, textTransform: 'capitalize' },
  emptyText: { color: '#9ca3af', fontSize: 15 },
  newBtn: { backgroundColor: '#1e2328', paddingVertical: 10, paddingHorizontal: 24, borderRadius: 9999 },
  newBtnText: { color: '#ffffff', fontSize: 14 },
  fab: {
    position: 'absolute',
    right: 20,
    bottom: 24,
    backgroundColor: '#ffffff',
    width: 56,
    height: 56,
    borderRadius: 28,
    justifyContent: 'center',
    alignItems: 'center',
    elevation: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 4,
  },
});
