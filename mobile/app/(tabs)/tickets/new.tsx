import { useRouter } from 'expo-router';
import { useState } from 'react';
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
import type { TicketCategory, TicketPriority } from '../../../src/types';

const CATEGORIES: TicketCategory[] = ['plumbing', 'electrical', 'appliance', 'hvac', 'structural', 'pest', 'other'];
const PRIORITIES: TicketPriority[] = ['low', 'normal', 'high', 'urgent'];

export default function NewTicketScreen() {
  const router = useRouter();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState<TicketCategory>('other');
  const [priority, setPriority] = useState<TicketPriority>('normal');
  const [contact, setContact] = useState('');
  const [emailUpdates, setEmailUpdates] = useState(true);
  const [loading, setLoading] = useState(false);

  async function submit() {
    if (!title.trim() || !description.trim()) {
      Alert.alert('Missing fields', 'Title and description are required.');
      return;
    }
    setLoading(true);
    try {
      const res = await ticketsApi.create({
        title,
        description,
        category,
        priority,
        contact,
        email_updates: emailUpdates,
      });
      Alert.alert('Ticket Submitted', `Reference: ${res.ticket.id.slice(0, 8)}`, [
        { text: 'OK', onPress: () => router.back() },
      ]);
    } catch (err: unknown) {
      Alert.alert('Error', err instanceof Error ? err.message : 'Failed to submit ticket.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20, gap: 16 }}>
      <Text style={styles.heading}>New Repair Ticket</Text>

      <Field label="Title *">
        <TextInput
          style={styles.input}
          placeholder="Brief description of the issue"
          placeholderTextColor="#6b7280"
          value={title}
          onChangeText={setTitle}
          maxLength={120}
        />
      </Field>

      <Field label="Description *">
        <TextInput
          style={[styles.input, styles.textarea]}
          placeholder="Describe the problem in detail"
          placeholderTextColor="#6b7280"
          value={description}
          onChangeText={setDescription}
          multiline
          numberOfLines={5}
          maxLength={4000}
        />
      </Field>

      <Field label="Category">
        <View style={styles.chipRow}>
          {CATEGORIES.map((c) => (
            <TouchableOpacity
              key={c}
              style={[styles.chip, category === c && styles.chipActive]}
              onPress={() => setCategory(c)}
            >
              <Text style={[styles.chipText, category === c && styles.chipTextActive]}>
                {c}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      </Field>

      <Field label="Priority">
        <View style={styles.chipRow}>
          {PRIORITIES.map((p) => (
            <TouchableOpacity
              key={p}
              style={[styles.chip, priority === p && styles.chipActive]}
              onPress={() => setPriority(p)}
            >
              <Text style={[styles.chipText, priority === p && styles.chipTextActive]}>{p}</Text>
            </TouchableOpacity>
          ))}
        </View>
      </Field>

      <Field label="Contact Info">
        <TextInput
          style={styles.input}
          placeholder="Phone or email for follow-up"
          placeholderTextColor="#6b7280"
          value={contact}
          onChangeText={setContact}
        />
      </Field>

      <View style={styles.switchRow}>
        <Text style={styles.switchLabel}>Email updates</Text>
        <Switch value={emailUpdates} onValueChange={setEmailUpdates} trackColor={{ true: '#10b981' }} />
      </View>

      <TouchableOpacity
        style={[styles.submitBtn, loading && { opacity: 0.6 }]}
        onPress={submit}
        disabled={loading}
        activeOpacity={0.8}
      >
        {loading ? (
          <ActivityIndicator color="#111518" />
        ) : (
          <Text style={styles.submitBtnText}>Submit Ticket</Text>
        )}
      </TouchableOpacity>
    </ScrollView>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={{ gap: 6 }}>
      <Text style={styles.label}>{label}</Text>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#111518' },
  heading: { color: '#ffffff', fontSize: 22, fontWeight: '700', marginBottom: 4 },
  label: { color: '#d1d5db', fontSize: 13, fontWeight: '500', textTransform: 'uppercase', letterSpacing: 0.5 },
  input: { backgroundColor: '#1e2328', color: '#ffffff', borderRadius: 10, padding: 14, fontSize: 15 },
  textarea: { height: 120, textAlignVertical: 'top' },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { backgroundColor: '#1e2328', paddingHorizontal: 14, paddingVertical: 8, borderRadius: 9999, borderWidth: 1, borderColor: '#374151' },
  chipActive: { backgroundColor: '#ffffff', borderColor: '#ffffff' },
  chipText: { color: '#9ca3af', fontSize: 13, textTransform: 'capitalize' },
  chipTextActive: { color: '#111518', fontWeight: '600' },
  switchRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: '#1e2328', borderRadius: 10, padding: 14 },
  switchLabel: { color: '#d1d5db', fontSize: 15 },
  submitBtn: { backgroundColor: '#ffffff', paddingVertical: 16, borderRadius: 9999, alignItems: 'center', marginTop: 8 },
  submitBtnText: { color: '#111518', fontSize: 16, fontWeight: '600' },
});
