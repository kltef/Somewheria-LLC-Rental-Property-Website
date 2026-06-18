import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { adminApi } from '../../../src/services/api';

interface UserEntry {
  email: string;
  name?: string;
  role: string;
}

const ROLE_COLORS: Record<string, string> = {
  renter: '#8b5cf6',
  admin: '#f59e0b',
  high_admin: '#ef4444',
  guest: '#6b7280',
};

export default function AdminUsersScreen() {
  const [users, setUsers] = useState<UserEntry[]>([]);
  const [filtered, setFiltered] = useState<UserEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');

  useEffect(() => {
    adminApi
      .users()
      .then((res) => {
        setUsers(res.users);
        setFiltered(res.users);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  function search(text: string) {
    setQuery(text);
    const q = text.toLowerCase();
    setFiltered(
      users.filter(
        (u) => u.email.toLowerCase().includes(q) || (u.name ?? '').toLowerCase().includes(q),
      ),
    );
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#f59e0b" />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.toolbar}>
        <TextInput
          style={styles.search}
          placeholder="Search by name or email..."
          placeholderTextColor="#6b7280"
          value={query}
          onChangeText={search}
        />
      </View>
      <FlatList
        data={filtered}
        keyExtractor={(item) => item.email}
        contentContainerStyle={{ padding: 12, gap: 8 }}
        renderItem={({ item }) => (
          <View style={styles.card}>
            <View style={styles.avatar}>
              <Text style={styles.avatarText}>{(item.name ?? item.email)[0].toUpperCase()}</Text>
            </View>
            <View style={{ flex: 1 }}>
              {item.name ? <Text style={styles.name}>{item.name}</Text> : null}
              <Text style={styles.email}>{item.email}</Text>
            </View>
            <View style={[styles.roleBadge, { backgroundColor: ROLE_COLORS[item.role] ?? '#6b7280' }]}>
              <Text style={styles.roleText}>{item.role.replace('_', ' ')}</Text>
            </View>
          </View>
        )}
        ListEmptyComponent={
          <View style={styles.center}>
            <Text style={styles.emptyText}>No users found.</Text>
          </View>
        }
      />
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
  card: {
    backgroundColor: '#1e2328',
    borderRadius: 12,
    padding: 14,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  avatar: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: '#374151',
    justifyContent: 'center',
    alignItems: 'center',
  },
  avatarText: { color: '#ffffff', fontSize: 17, fontWeight: '700' },
  name: { color: '#ffffff', fontSize: 14, fontWeight: '600' },
  email: { color: '#9ca3af', fontSize: 12, marginTop: 1 },
  roleBadge: { borderRadius: 9999, paddingHorizontal: 10, paddingVertical: 4 },
  roleText: { color: '#ffffff', fontSize: 11, fontWeight: '600', textTransform: 'capitalize' },
  emptyText: { color: '#6b7280', fontSize: 14 },
});
