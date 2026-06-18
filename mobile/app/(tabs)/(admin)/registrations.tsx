import { Ionicons } from '@expo/vector-icons';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { adminApi } from '../../../src/services/api';
import type { PendingRegistration } from '../../../src/types';

export default function AdminRegistrationsScreen() {
  const [regs, setRegs] = useState<PendingRegistration[]>([]);
  const [loading, setLoading] = useState(true);
  const [actioning, setActioning] = useState<string | null>(null);

  function load() {
    setLoading(true);
    adminApi
      .registrations()
      .then((res) => setRegs(res.registrations))
      .catch(console.error)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  async function approve(email: string) {
    setActioning(email);
    try {
      await adminApi.approveRegistration(email);
      setRegs((prev) => prev.filter((r) => r.email !== email));
    } catch (err: unknown) {
      Alert.alert('Error', err instanceof Error ? err.message : 'Failed to approve.');
    } finally {
      setActioning(null);
    }
  }

  function confirmReject(email: string, name: string) {
    Alert.alert('Reject Registration', `Reject registration for ${name}?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Reject',
        style: 'destructive',
        onPress: () => reject(email),
      },
    ]);
  }

  async function reject(email: string) {
    setActioning(email);
    try {
      await adminApi.rejectRegistration(email);
      setRegs((prev) => prev.filter((r) => r.email !== email));
    } catch (err: unknown) {
      Alert.alert('Error', err instanceof Error ? err.message : 'Failed to reject.');
    } finally {
      setActioning(null);
    }
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#f59e0b" />
      </View>
    );
  }

  return (
    <FlatList
      style={styles.container}
      data={regs}
      keyExtractor={(item) => item.email}
      contentContainerStyle={{ padding: 14, gap: 10 }}
      renderItem={({ item }) => (
        <View style={styles.card}>
          <View style={styles.cardHeader}>
            <View style={styles.avatar}>
              <Text style={styles.avatarText}>{(item.name ?? item.email)[0].toUpperCase()}</Text>
            </View>
            <View style={{ flex: 1 }}>
              {item.name ? <Text style={styles.name}>{item.name}</Text> : null}
              <Text style={styles.email}>{item.email}</Text>
              {item.submitted_at ? (
                <Text style={styles.date}>
                  {new Date(item.submitted_at).toLocaleDateString()}
                </Text>
              ) : null}
            </View>
          </View>

          {item.message ? (
            <View style={styles.messageBox}>
              <Text style={styles.messageText}>{item.message}</Text>
            </View>
          ) : null}

          {actioning === item.email ? (
            <View style={styles.actioningRow}>
              <ActivityIndicator color="#f59e0b" />
            </View>
          ) : (
            <View style={styles.actions}>
              <TouchableOpacity
                style={styles.approveBtn}
                onPress={() => approve(item.email)}
                activeOpacity={0.8}
              >
                <Ionicons name="checkmark-circle-outline" size={16} color="#111518" />
                <Text style={styles.approveBtnText}>Approve</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.rejectBtn}
                onPress={() => confirmReject(item.email, item.name ?? item.email)}
                activeOpacity={0.8}
              >
                <Ionicons name="close-circle-outline" size={16} color="#ef4444" />
                <Text style={styles.rejectBtnText}>Reject</Text>
              </TouchableOpacity>
            </View>
          )}
        </View>
      )}
      ListEmptyComponent={
        <View style={[styles.center, { paddingTop: 80 }]}>
          <Ionicons name="checkmark-done-outline" size={48} color="#374151" />
          <Text style={styles.emptyText}>No pending registrations.</Text>
        </View>
      }
    />
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0d1117' },
  center: { justifyContent: 'center', alignItems: 'center', gap: 10 },
  card: { backgroundColor: '#1e2328', borderRadius: 12, padding: 16, gap: 12 },
  cardHeader: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  avatar: {
    width: 46,
    height: 46,
    borderRadius: 23,
    backgroundColor: '#374151',
    justifyContent: 'center',
    alignItems: 'center',
  },
  avatarText: { color: '#ffffff', fontSize: 18, fontWeight: '700' },
  name: { color: '#ffffff', fontSize: 14, fontWeight: '600' },
  email: { color: '#9ca3af', fontSize: 12, marginTop: 1 },
  date: { color: '#6b7280', fontSize: 11, marginTop: 2 },
  messageBox: { backgroundColor: '#111518', borderRadius: 8, padding: 12 },
  messageText: { color: '#d1d5db', fontSize: 13, lineHeight: 19 },
  actioningRow: { alignItems: 'center', paddingVertical: 8 },
  actions: { flexDirection: 'row', gap: 10 },
  approveBtn: {
    flex: 1,
    backgroundColor: '#10b981',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 11,
    borderRadius: 9999,
  },
  approveBtnText: { color: '#111518', fontSize: 14, fontWeight: '700' },
  rejectBtn: {
    flex: 1,
    borderWidth: 1,
    borderColor: '#ef4444',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 11,
    borderRadius: 9999,
  },
  rejectBtnText: { color: '#ef4444', fontSize: 14, fontWeight: '600' },
  emptyText: { color: '#9ca3af', fontSize: 15 },
});
