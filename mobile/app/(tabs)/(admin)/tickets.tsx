import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Modal,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { adminApi } from '../../../src/services/api';
import type { Ticket } from '../../../src/types';

const STATUS_OPTIONS = ['', 'open', 'in_progress', 'awaiting_parts', 'resolved', 'closed'];
const PRIORITY_OPTIONS = ['', 'low', 'normal', 'high', 'urgent'];

const STATUS_COLORS: Record<string, string> = {
  open: '#f59e0b',
  in_progress: '#3b82f6',
  awaiting_parts: '#8b5cf6',
  resolved: '#10b981',
  closed: '#6b7280',
};

export default function AdminTicketsScreen() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('');
  const [selected, setSelected] = useState<Ticket | null>(null);
  const [updating, setUpdating] = useState(false);
  const [newStatus, setNewStatus] = useState('');
  const [newPriority, setNewPriority] = useState('');
  const [assignedTo, setAssignedTo] = useState('');

  function load() {
    setLoading(true);
    adminApi
      .tickets({ status: statusFilter as never, priority: priorityFilter as never, q: query })
      .then((res) => setTickets(res.tickets))
      .catch(console.error)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, [statusFilter, priorityFilter]);

  function openDetail(t: Ticket) {
    setSelected(t);
    setNewStatus(t.status);
    setNewPriority(t.priority);
    setAssignedTo(t.assigned_to ?? '');
  }

  async function saveUpdate() {
    if (!selected) return;
    setUpdating(true);
    try {
      await adminApi.updateTicket(selected.id, {
        status: newStatus,
        priority: newPriority,
        assigned_to: assignedTo || undefined,
      });
      setSelected(null);
      load();
    } catch (err) {
      console.error(err);
    } finally {
      setUpdating(false);
    }
  }

  return (
    <View style={styles.container}>
      {/* Search + filter bar */}
      <View style={styles.toolbar}>
        <TextInput
          style={styles.search}
          placeholder="Search tickets..."
          placeholderTextColor="#6b7280"
          value={query}
          onChangeText={setQuery}
          onSubmitEditing={load}
          returnKeyType="search"
        />
      </View>

      <View style={styles.filterRow}>
        {STATUS_OPTIONS.map((s) => (
          <TouchableOpacity
            key={s || 'all'}
            style={[styles.filterChip, statusFilter === s && styles.filterChipActive]}
            onPress={() => setStatusFilter(s)}
          >
            <Text style={[styles.filterChipText, statusFilter === s && styles.filterChipTextActive]}>
              {s || 'All'}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color="#f59e0b" />
        </View>
      ) : (
        <FlatList
          data={tickets}
          keyExtractor={(item) => item.id}
          contentContainerStyle={{ padding: 12, gap: 8 }}
          renderItem={({ item }) => (
            <TouchableOpacity style={styles.card} onPress={() => openDetail(item)} activeOpacity={0.8}>
              <View style={styles.cardHeader}>
                <View style={[styles.statusDot, { backgroundColor: STATUS_COLORS[item.status] ?? '#6b7280' }]} />
                <Text style={styles.cardTitle} numberOfLines={1}>{item.title}</Text>
              </View>
              <Text style={styles.cardMeta}>
                {item.property_name || 'No property'} · {item.category} · {item.priority}
              </Text>
              <Text style={styles.cardSub}>
                {item.status.replace('_', ' ')}{item.assigned_to ? ` · ${item.assigned_to}` : ''}
              </Text>
            </TouchableOpacity>
          )}
          ListEmptyComponent={
            <View style={styles.center}>
              <Text style={styles.emptyText}>No tickets found.</Text>
            </View>
          }
        />
      )}

      {/* Edit modal */}
      <Modal visible={!!selected} animationType="slide" transparent>
        <View style={styles.overlay}>
          <View style={styles.sheet}>
            <Text style={styles.sheetTitle} numberOfLines={2}>{selected?.title}</Text>

            <Text style={styles.sheetLabel}>Status</Text>
            <View style={styles.chipRow}>
              {STATUS_OPTIONS.filter(Boolean).map((s) => (
                <TouchableOpacity
                  key={s}
                  style={[styles.chip, newStatus === s && styles.chipActive]}
                  onPress={() => setNewStatus(s)}
                >
                  <Text style={[styles.chipText, newStatus === s && styles.chipTextActive]}>{s.replace('_', ' ')}</Text>
                </TouchableOpacity>
              ))}
            </View>

            <Text style={styles.sheetLabel}>Priority</Text>
            <View style={styles.chipRow}>
              {PRIORITY_OPTIONS.filter(Boolean).map((p) => (
                <TouchableOpacity
                  key={p}
                  style={[styles.chip, newPriority === p && styles.chipActive]}
                  onPress={() => setNewPriority(p)}
                >
                  <Text style={[styles.chipText, newPriority === p && styles.chipTextActive]}>{p}</Text>
                </TouchableOpacity>
              ))}
            </View>

            <Text style={styles.sheetLabel}>Assigned To</Text>
            <TextInput
              style={styles.input}
              value={assignedTo}
              onChangeText={setAssignedTo}
              placeholder="Name or email"
              placeholderTextColor="#6b7280"
            />

            <View style={styles.sheetActions}>
              <TouchableOpacity style={styles.cancelBtn} onPress={() => setSelected(null)}>
                <Text style={styles.cancelBtnText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.saveBtn, updating && { opacity: 0.6 }]}
                onPress={saveUpdate}
                disabled={updating}
              >
                {updating ? (
                  <ActivityIndicator color="#111518" size="small" />
                ) : (
                  <Text style={styles.saveBtnText}>Save</Text>
                )}
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0d1117' },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingTop: 40 },
  toolbar: { padding: 12 },
  search: {
    backgroundColor: '#1e2328',
    color: '#ffffff',
    borderRadius: 10,
    padding: 12,
    fontSize: 14,
  },
  filterRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, paddingHorizontal: 12, paddingBottom: 8 },
  filterChip: {
    backgroundColor: '#1e2328',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 9999,
    borderWidth: 1,
    borderColor: '#374151',
  },
  filterChipActive: { backgroundColor: '#f59e0b', borderColor: '#f59e0b' },
  filterChipText: { color: '#9ca3af', fontSize: 12, textTransform: 'capitalize' },
  filterChipTextActive: { color: '#111518', fontWeight: '600' },
  card: { backgroundColor: '#1e2328', borderRadius: 12, padding: 14, gap: 4 },
  cardHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  statusDot: { width: 8, height: 8, borderRadius: 4, flexShrink: 0 },
  cardTitle: { color: '#ffffff', fontSize: 14, fontWeight: '600', flex: 1 },
  cardMeta: { color: '#9ca3af', fontSize: 12, textTransform: 'capitalize' },
  cardSub: { color: '#6b7280', fontSize: 12, textTransform: 'capitalize' },
  emptyText: { color: '#6b7280', fontSize: 14 },
  overlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'flex-end' },
  sheet: {
    backgroundColor: '#1e2328',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 24,
    gap: 14,
  },
  sheetTitle: { color: '#ffffff', fontSize: 16, fontWeight: '700' },
  sheetLabel: { color: '#9ca3af', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    backgroundColor: '#111518',
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 9999,
    borderWidth: 1,
    borderColor: '#374151',
  },
  chipActive: { backgroundColor: '#ffffff', borderColor: '#ffffff' },
  chipText: { color: '#9ca3af', fontSize: 13, textTransform: 'capitalize' },
  chipTextActive: { color: '#111518', fontWeight: '600' },
  input: { backgroundColor: '#111518', color: '#ffffff', borderRadius: 8, padding: 12, fontSize: 14 },
  sheetActions: { flexDirection: 'row', gap: 10, marginTop: 4 },
  cancelBtn: {
    flex: 1,
    backgroundColor: '#111518',
    paddingVertical: 13,
    borderRadius: 9999,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#374151',
  },
  cancelBtnText: { color: '#9ca3af', fontSize: 14, fontWeight: '600' },
  saveBtn: {
    flex: 2,
    backgroundColor: '#f59e0b',
    paddingVertical: 13,
    borderRadius: 9999,
    alignItems: 'center',
  },
  saveBtnText: { color: '#111518', fontSize: 14, fontWeight: '700' },
});
