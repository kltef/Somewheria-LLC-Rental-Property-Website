import { Ionicons } from '@expo/vector-icons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
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

export default function TicketDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState('');
  const [submittingNote, setSubmittingNote] = useState(false);

  useEffect(() => {
    if (!id) return;
    ticketsApi
      .get(id)
      .then((res) => setTicket(res.ticket))
      .catch((err) => Alert.alert('Error', err.message))
      .finally(() => setLoading(false));
  }, [id]);

  async function submitNote() {
    if (!note.trim() || !ticket) return;
    setSubmittingNote(true);
    try {
      const res = await ticketsApi.addNote(ticket.id, note);
      setTicket(res.ticket);
      setNote('');
    } catch (err: unknown) {
      Alert.alert('Error', err instanceof Error ? err.message : 'Failed to add note.');
    } finally {
      setSubmittingNote(false);
    }
  }

  async function toggleEmailUpdates(enabled: boolean) {
    if (!ticket) return;
    try {
      const res = await ticketsApi.setEmailUpdates(ticket.id, enabled);
      setTicket(res.ticket);
    } catch {
      // silent
    }
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#ffffff" />
      </View>
    );
  }

  if (!ticket) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>Ticket not found.</Text>
        <TouchableOpacity onPress={() => router.back()}>
          <Text style={styles.link}>Go Back</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20, paddingBottom: 40 }}>
      {/* Status header */}
      <View style={styles.statusHeader}>
        <View style={[styles.statusBadge, { backgroundColor: STATUS_COLORS[ticket.status] ?? '#6b7280' }]}>
          <Text style={styles.statusText}>{ticket.status.replace('_', ' ')}</Text>
        </View>
        <Text style={styles.refText}>Ref: {ticket.id.slice(0, 8)}</Text>
      </View>

      <Text style={styles.title}>{ticket.title}</Text>

      <View style={styles.metaGrid}>
        <MetaItem label="Category" value={ticket.category} />
        <MetaItem label="Priority" value={ticket.priority} />
        <MetaItem label="Property" value={ticket.property_name || '—'} />
        {ticket.assigned_to && <MetaItem label="Assigned to" value={ticket.assigned_to} />}
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Description</Text>
        <Text style={styles.descText}>{ticket.description}</Text>
      </View>

      {/* Email updates toggle */}
      <View style={styles.switchRow}>
        <Text style={styles.switchLabel}>Email updates</Text>
        <Switch
          value={ticket.email_updates}
          onValueChange={toggleEmailUpdates}
          trackColor={{ true: '#10b981' }}
        />
      </View>

      {/* Notes */}
      {ticket.notes.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Notes</Text>
          {ticket.notes.map((n, i) => (
            <View key={i} style={styles.noteCard}>
              <Text style={styles.noteAuthor}>{n.by} · {new Date(n.at).toLocaleDateString()}</Text>
              <Text style={styles.noteText}>{n.text}</Text>
            </View>
          ))}
        </View>
      )}

      {/* Add note */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Add a Note</Text>
        <TextInput
          style={[styles.input, { height: 90, textAlignVertical: 'top' }]}
          placeholder="Type your note..."
          placeholderTextColor="#6b7280"
          value={note}
          onChangeText={setNote}
          multiline
        />
        <TouchableOpacity
          style={[styles.noteBtn, (!note.trim() || submittingNote) && { opacity: 0.5 }]}
          onPress={submitNote}
          disabled={!note.trim() || submittingNote}
          activeOpacity={0.8}
        >
          {submittingNote ? (
            <ActivityIndicator color="#111518" size="small" />
          ) : (
            <>
              <Ionicons name="send-outline" size={16} color="#111518" />
              <Text style={styles.noteBtnText}>Add Note</Text>
            </>
          )}
        </TouchableOpacity>
      </View>

      <Text style={styles.timestamps}>
        Created: {new Date(ticket.created_at).toLocaleDateString()}{'\n'}
        Updated: {new Date(ticket.updated_at).toLocaleDateString()}
      </Text>
    </ScrollView>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metaItem}>
      <Text style={styles.metaLabel}>{label}</Text>
      <Text style={styles.metaValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#111518' },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#111518', gap: 12 },
  statusHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  statusBadge: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 9999 },
  statusText: { color: '#ffffff', fontSize: 13, fontWeight: '600', textTransform: 'capitalize' },
  refText: { color: '#9ca3af', fontSize: 13 },
  title: { color: '#ffffff', fontSize: 20, fontWeight: '700', marginBottom: 16 },
  metaGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginBottom: 20 },
  metaItem: { backgroundColor: '#1e2328', borderRadius: 10, padding: 10, minWidth: '45%', flex: 1 },
  metaLabel: { color: '#9ca3af', fontSize: 11, textTransform: 'uppercase', marginBottom: 3 },
  metaValue: { color: '#ffffff', fontSize: 14, textTransform: 'capitalize' },
  section: { marginBottom: 20, gap: 10 },
  sectionTitle: { color: '#ffffff', fontSize: 15, fontWeight: '600' },
  descText: { color: '#d1d5db', fontSize: 14, lineHeight: 21 },
  switchRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: '#1e2328', borderRadius: 10, padding: 14, marginBottom: 20 },
  switchLabel: { color: '#d1d5db', fontSize: 15 },
  noteCard: { backgroundColor: '#1e2328', borderRadius: 10, padding: 14, gap: 6 },
  noteAuthor: { color: '#9ca3af', fontSize: 12 },
  noteText: { color: '#d1d5db', fontSize: 14, lineHeight: 20 },
  input: { backgroundColor: '#1e2328', color: '#ffffff', borderRadius: 10, padding: 14, fontSize: 15 },
  noteBtn: { backgroundColor: '#ffffff', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, paddingVertical: 12, borderRadius: 9999 },
  noteBtnText: { color: '#111518', fontSize: 14, fontWeight: '600' },
  timestamps: { color: '#6b7280', fontSize: 12, lineHeight: 18 },
  errorText: { color: '#ef4444', fontSize: 15 },
  link: { color: '#9ca3af', fontSize: 14 },
});
