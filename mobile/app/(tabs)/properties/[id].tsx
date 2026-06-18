import { Ionicons } from '@expo/vector-icons';
import { Image } from 'expo-image';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { propertiesApi } from '../../../src/services/api';
import type { Property } from '../../../src/types';

export default function PropertyDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [property, setProperty] = useState<Property | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showSchedule, setShowSchedule] = useState(false);
  const router = useRouter();

  useEffect(() => {
    if (!id) return;
    propertiesApi
      .get(id)
      .then(setProperty)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#ffffff" />
      </View>
    );
  }
  if (error || !property) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>{error || 'Property not found.'}</Text>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <Text style={styles.backBtnText}>Go Back</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ paddingBottom: 40 }}>
      {/* Photos */}
      {property.photos.length > 0 && (
        <FlatList
          horizontal
          pagingEnabled
          data={property.photos}
          keyExtractor={(_, i) => String(i)}
          renderItem={({ item }) => (
            <Image source={{ uri: item }} style={styles.photo} contentFit="cover" />
          )}
          showsHorizontalScrollIndicator={false}
        />
      )}

      <View style={styles.body}>
        <Text style={styles.title}>{property.name}</Text>
        <Text style={styles.address}>{property.address}</Text>

        <View style={styles.infoRow}>
          {property.rent !== 'N/A' && (
            <View style={styles.infoChip}>
              <Ionicons name="cash-outline" size={14} color="#9ca3af" />
              <Text style={styles.infoChipText}>${property.rent}/mo</Text>
            </View>
          )}
          {property.bedrooms !== 'N/A' && (
            <View style={styles.infoChip}>
              <Ionicons name="bed-outline" size={14} color="#9ca3af" />
              <Text style={styles.infoChipText}>{property.bedrooms} bed</Text>
            </View>
          )}
          {property.bathrooms !== 'N/A' && (
            <View style={styles.infoChip}>
              <Ionicons name="water-outline" size={14} color="#9ca3af" />
              <Text style={styles.infoChipText}>{property.bathrooms} bath</Text>
            </View>
          )}
          {property.sqft !== 'N/A' && (
            <View style={styles.infoChip}>
              <Text style={styles.infoChipText}>{property.sqft} sqft</Text>
            </View>
          )}
        </View>

        {property.blurb ? <Text style={styles.blurb}>{property.blurb}</Text> : null}
        {property.description ? (
          <Text style={styles.description}>{property.description}</Text>
        ) : null}

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Details</Text>
          <DetailRow label="Lease" value={property.lease_length} />
          <DetailRow label="Pets" value={property.pets_allowed} />
          <DetailRow label="ADA" value={property.ada_accessible} />
          {property.deposit ? <DetailRow label="Deposit" value={`$${property.deposit}`} /> : null}
        </View>

        {property.included_amenities.length > 0 && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Included Amenities</Text>
            {property.included_amenities.map((a, i) => (
              <Text key={i} style={styles.amenity}>• {a}</Text>
            ))}
          </View>
        )}

        <TouchableOpacity
          style={styles.scheduleBtn}
          onPress={() => setShowSchedule(true)}
          activeOpacity={0.8}
        >
          <Ionicons name="calendar-outline" size={18} color="#111518" />
          <Text style={styles.scheduleBtnText}>Schedule a Viewing</Text>
        </TouchableOpacity>
      </View>

      {showSchedule && (
        <ScheduleModal propertyId={property.id} onClose={() => setShowSchedule(false)} />
      )}
    </ScrollView>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.detailRow}>
      <Text style={styles.detailLabel}>{label}</Text>
      <Text style={styles.detailValue}>{value}</Text>
    </View>
  );
}

function ScheduleModal({ propertyId, onClose }: { propertyId: string; onClose: () => void }) {
  const [name, setName] = useState('');
  const [date, setDate] = useState('');
  const [contactMethod, setContactMethod] = useState('email');
  const [contactInfo, setContactInfo] = useState('');
  const [loading, setLoading] = useState(false);

  async function submit() {
    if (!name || !date || !contactInfo) {
      Alert.alert('Missing fields', 'Name, date, and contact info are required.');
      return;
    }
    setLoading(true);
    try {
      await propertiesApi.schedule(propertyId, {
        name,
        date,
        contact_method: contactMethod,
        contact_info: contactInfo,
      });
      Alert.alert('Request Sent', 'Your viewing request has been submitted!');
      onClose();
    } catch (err: unknown) {
      Alert.alert('Error', err instanceof Error ? err.message : 'Failed to submit request.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <View style={styles.modal}>
      <Text style={styles.modalTitle}>Schedule a Viewing</Text>
      <TextInput style={styles.input} placeholder="Your name" placeholderTextColor="#6b7280" value={name} onChangeText={setName} />
      <TextInput style={styles.input} placeholder="Date (YYYY-MM-DD)" placeholderTextColor="#6b7280" value={date} onChangeText={setDate} />
      <TextInput style={styles.input} placeholder="Contact method (email/phone)" placeholderTextColor="#6b7280" value={contactMethod} onChangeText={setContactMethod} />
      <TextInput style={styles.input} placeholder="Contact info" placeholderTextColor="#6b7280" value={contactInfo} onChangeText={setContactInfo} />
      <TouchableOpacity style={styles.scheduleBtn} onPress={submit} disabled={loading} activeOpacity={0.8}>
        {loading ? <ActivityIndicator color="#111518" /> : <Text style={styles.scheduleBtnText}>Submit Request</Text>}
      </TouchableOpacity>
      <TouchableOpacity onPress={onClose} style={{ marginTop: 12, alignItems: 'center' }}>
        <Text style={{ color: '#9ca3af' }}>Cancel</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#111518' },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 24, backgroundColor: '#111518' },
  photo: { width: 380, height: 240 },
  body: { padding: 20 },
  title: { color: '#ffffff', fontSize: 22, fontWeight: '700', marginBottom: 4 },
  address: { color: '#9ca3af', fontSize: 14, marginBottom: 14 },
  infoRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 16 },
  infoChip: { flexDirection: 'row', alignItems: 'center', gap: 4, backgroundColor: '#1e2328', borderRadius: 9999, paddingHorizontal: 10, paddingVertical: 5 },
  infoChipText: { color: '#d1d5db', fontSize: 13 },
  blurb: { color: '#d1d5db', fontSize: 15, fontStyle: 'italic', marginBottom: 10 },
  description: { color: '#9ca3af', fontSize: 14, lineHeight: 21, marginBottom: 16 },
  section: { marginBottom: 20 },
  sectionTitle: { color: '#ffffff', fontSize: 16, fontWeight: '600', marginBottom: 10 },
  detailRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: '#374151' },
  detailLabel: { color: '#9ca3af', fontSize: 14 },
  detailValue: { color: '#ffffff', fontSize: 14 },
  amenity: { color: '#d1d5db', fontSize: 14, marginBottom: 4 },
  scheduleBtn: { backgroundColor: '#ffffff', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 14, borderRadius: 9999, marginTop: 8 },
  scheduleBtnText: { color: '#111518', fontSize: 16, fontWeight: '600' },
  modal: { backgroundColor: '#1e2328', margin: 16, borderRadius: 16, padding: 24, gap: 12 },
  modalTitle: { color: '#ffffff', fontSize: 18, fontWeight: '700', marginBottom: 8 },
  input: { backgroundColor: '#374151', color: '#ffffff', borderRadius: 8, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15 },
  backBtn: { marginTop: 16, paddingVertical: 10, paddingHorizontal: 24, backgroundColor: '#1e2328', borderRadius: 9999 },
  backBtnText: { color: '#ffffff', fontSize: 15 },
  errorText: { color: '#ef4444', fontSize: 15 },
});
