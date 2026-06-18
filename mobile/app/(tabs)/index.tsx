import { Ionicons } from '@expo/vector-icons';
import { Image } from 'expo-image';
import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { propertiesApi } from '../../src/services/api';
import type { Property } from '../../src/types';

export default function PropertyListScreen() {
  const [properties, setProperties] = useState<Property[]>([]);
  const [filtered, setFiltered] = useState<Property[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const router = useRouter();

  useEffect(() => {
    propertiesApi
      .list()
      .then((res) => {
        setProperties(res.properties);
        setFiltered(res.properties);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!search.trim()) {
      setFiltered(properties);
      return;
    }
    const q = search.toLowerCase();
    setFiltered(
      properties.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          p.address.toLowerCase().includes(q) ||
          p.blurb.toLowerCase().includes(q),
      ),
    );
  }, [search, properties]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#ffffff" />
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>{error}</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.searchBar}>
        <Ionicons name="search-outline" size={18} color="#9ca3af" style={styles.searchIcon} />
        <TextInput
          style={styles.searchInput}
          placeholder="Search properties..."
          placeholderTextColor="#6b7280"
          value={search}
          onChangeText={setSearch}
        />
      </View>
      <FlatList
        data={filtered}
        keyExtractor={(item) => item.id}
        contentContainerStyle={{ paddingBottom: 24 }}
        renderItem={({ item }) => (
          <TouchableOpacity
            style={styles.card}
            onPress={() => router.push(`/(tabs)/properties/${item.id}`)}
            activeOpacity={0.8}
          >
            {item.thumbnail ? (
              <Image
                source={{ uri: item.thumbnail }}
                style={styles.thumbnail}
                contentFit="cover"
              />
            ) : (
              <View style={[styles.thumbnail, styles.placeholderThumbnail]}>
                <Ionicons name="home-outline" size={32} color="#6b7280" />
              </View>
            )}
            <View style={styles.cardBody}>
              <Text style={styles.propertyName} numberOfLines={1}>
                {item.name}
              </Text>
              <Text style={styles.address} numberOfLines={1}>
                {item.address}
              </Text>
              <View style={styles.meta}>
                <Text style={styles.rent}>
                  {item.rent !== 'N/A' ? `$${item.rent}/mo` : 'Call for pricing'}
                </Text>
                <View style={styles.badges}>
                  {item.bedrooms !== 'N/A' && (
                    <Text style={styles.badge}>{item.bedrooms} bd</Text>
                  )}
                  {item.bathrooms !== 'N/A' && (
                    <Text style={styles.badge}>{item.bathrooms} ba</Text>
                  )}
                  {item.pets_allowed === 'Yes' && (
                    <Text style={styles.badge}>Pets OK</Text>
                  )}
                </View>
              </View>
            </View>
          </TouchableOpacity>
        )}
        ListEmptyComponent={
          <View style={styles.center}>
            <Text style={styles.emptyText}>No properties found.</Text>
          </View>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#111518' },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 24 },
  searchBar: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#1e2328',
    margin: 16,
    borderRadius: 12,
    paddingHorizontal: 12,
  },
  searchIcon: { marginRight: 8 },
  searchInput: { flex: 1, color: '#ffffff', paddingVertical: 12, fontSize: 15 },
  card: {
    backgroundColor: '#1e2328',
    marginHorizontal: 16,
    marginBottom: 12,
    borderRadius: 12,
    overflow: 'hidden',
  },
  thumbnail: { width: '100%', height: 180 },
  placeholderThumbnail: {
    backgroundColor: '#374151',
    justifyContent: 'center',
    alignItems: 'center',
  },
  cardBody: { padding: 16 },
  propertyName: { color: '#ffffff', fontSize: 17, fontWeight: '600', marginBottom: 4 },
  address: { color: '#9ca3af', fontSize: 13, marginBottom: 10 },
  meta: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  rent: { color: '#ffffff', fontSize: 15, fontWeight: '600' },
  badges: { flexDirection: 'row', gap: 6 },
  badge: {
    backgroundColor: '#374151',
    color: '#d1d5db',
    fontSize: 12,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 9999,
  },
  errorText: { color: '#ef4444', fontSize: 15, textAlign: 'center' },
  emptyText: { color: '#9ca3af', fontSize: 15 },
});
