import { Image } from 'expo-image';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  StyleSheet,
  Switch,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { adminApi } from '../../../src/services/api';
import type { Property } from '../../../src/types';

export default function AdminListingsScreen() {
  const [listings, setListings] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);

  function load() {
    setLoading(true);
    adminApi
      .listings()
      .then((res) => setListings(res.properties))
      .catch(console.error)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  async function toggleSale(id: string, currentState: boolean) {
    setToggling(id);
    try {
      await adminApi.toggleSale(id);
      setListings((prev) =>
        prev.map((p) => (p.id === id ? { ...p, for_sale: !currentState } : p)),
      );
    } catch (err: unknown) {
      Alert.alert('Error', err instanceof Error ? err.message : 'Failed to toggle.');
    } finally {
      setToggling(null);
    }
  }

  function confirmDelete(id: string, name: string) {
    Alert.alert(
      'Delete Listing',
      `Remove "${name}" from the listings? This cannot be undone.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: () => deleteListing(id),
        },
      ],
    );
  }

  async function deleteListing(id: string) {
    try {
      await adminApi.deleteListing(id);
      setListings((prev) => prev.filter((p) => p.id !== id));
    } catch (err: unknown) {
      Alert.alert('Error', err instanceof Error ? err.message : 'Failed to delete.');
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
      data={listings}
      keyExtractor={(item) => item.id}
      contentContainerStyle={{ padding: 14, gap: 10 }}
      renderItem={({ item }) => (
        <View style={styles.card}>
          {item.thumbnail && (
            <Image
              source={{ uri: `data:image/jpeg;base64,${item.thumbnail}` }}
              style={styles.thumbnail}
              contentFit="cover"
            />
          )}
          <View style={styles.cardBody}>
            <Text style={styles.cardTitle} numberOfLines={1}>
              {item.address || item.id.slice(0, 8)}
            </Text>
            <Text style={styles.cardMeta}>
              {item.bedrooms ?? '?'} bd · {item.bathrooms ?? '?'} ba
              {item.rent ? ` · ${item.rent}/mo` : ''}
            </Text>

            <View style={styles.row}>
              <Text style={styles.toggleLabel}>For Sale</Text>
              {toggling === item.id ? (
                <ActivityIndicator size="small" color="#f59e0b" />
              ) : (
                <Switch
                  value={!!item.for_sale}
                  onValueChange={(v) => toggleSale(item.id, !v)}
                  trackColor={{ true: '#f59e0b' }}
                />
              )}
            </View>

            <TouchableOpacity
              style={styles.deleteBtn}
              onPress={() => confirmDelete(item.id, item.address || item.id.slice(0, 8))}
              activeOpacity={0.8}
            >
              <Text style={styles.deleteBtnText}>Remove Listing</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}
      ListEmptyComponent={
        <View style={styles.center}>
          <Text style={styles.emptyText}>No listings found.</Text>
        </View>
      }
    />
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0d1117' },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingTop: 60 },
  card: { backgroundColor: '#1e2328', borderRadius: 12, overflow: 'hidden' },
  thumbnail: { width: '100%', height: 160 },
  cardBody: { padding: 14, gap: 8 },
  cardTitle: { color: '#ffffff', fontSize: 15, fontWeight: '600' },
  cardMeta: { color: '#9ca3af', fontSize: 13 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  toggleLabel: { color: '#d1d5db', fontSize: 14 },
  deleteBtn: {
    borderWidth: 1,
    borderColor: '#ef4444',
    paddingVertical: 10,
    borderRadius: 9999,
    alignItems: 'center',
    marginTop: 4,
  },
  deleteBtnText: { color: '#ef4444', fontSize: 13, fontWeight: '600' },
  emptyText: { color: '#6b7280', fontSize: 14 },
});
