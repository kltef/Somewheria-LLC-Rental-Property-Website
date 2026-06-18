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
import { useAuth } from '../../src/context/AuthContext';
import { profileApi } from '../../src/services/api';
import type { RenterProfile } from '../../src/types';

export default function ProfileScreen() {
  const { user, logout } = useAuth();
  const [profile, setProfile] = useState<RenterProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');
  const [contact, setContact] = useState('');
  const [emailUpdates, setEmailUpdates] = useState(true);

  useEffect(() => {
    profileApi
      .get()
      .then((p) => {
        setProfile(p);
        setName(p.name);
        setContact(p.contact);
        setEmailUpdates(p.email_status_updates);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  async function save() {
    setSaving(true);
    try {
      const res = await profileApi.update({ name, contact, email_status_updates: emailUpdates });
      setProfile(res.profile);
      Alert.alert('Saved', 'Your profile has been updated.');
    } catch (err: unknown) {
      Alert.alert('Error', err instanceof Error ? err.message : 'Failed to save.');
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#ffffff" />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20, gap: 20 }}>
      {/* User info */}
      <View style={styles.userCard}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>{(user?.name ?? 'U')[0].toUpperCase()}</Text>
        </View>
        <View>
          <Text style={styles.userName}>{user?.name}</Text>
          <Text style={styles.userEmail}>{user?.email}</Text>
          <Text style={styles.userRole}>{user?.role}</Text>
        </View>
      </View>

      {/* Editable profile */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Profile</Text>

        <View style={styles.field}>
          <Text style={styles.label}>Display Name</Text>
          <TextInput
            style={styles.input}
            value={name}
            onChangeText={setName}
            placeholder="Your name"
            placeholderTextColor="#6b7280"
          />
        </View>

        <View style={styles.field}>
          <Text style={styles.label}>Contact Info</Text>
          <TextInput
            style={styles.input}
            value={contact}
            onChangeText={setContact}
            placeholder="Phone or alternate email"
            placeholderTextColor="#6b7280"
          />
        </View>

        <View style={styles.switchRow}>
          <Text style={styles.switchLabel}>Email status updates on tickets</Text>
          <Switch
            value={emailUpdates}
            onValueChange={setEmailUpdates}
            trackColor={{ true: '#10b981' }}
          />
        </View>

        <TouchableOpacity
          style={[styles.saveBtn, saving && { opacity: 0.6 }]}
          onPress={save}
          disabled={saving}
          activeOpacity={0.8}
        >
          {saving ? (
            <ActivityIndicator color="#111518" size="small" />
          ) : (
            <Text style={styles.saveBtnText}>Save Changes</Text>
          )}
        </TouchableOpacity>
      </View>

      {/* Sign out */}
      <TouchableOpacity
        style={styles.logoutBtn}
        onPress={() =>
          Alert.alert('Sign Out', 'Are you sure?', [
            { text: 'Cancel', style: 'cancel' },
            { text: 'Sign Out', style: 'destructive', onPress: logout },
          ])
        }
        activeOpacity={0.8}
      >
        <Text style={styles.logoutBtnText}>Sign Out</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#111518' },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#111518' },
  userCard: { flexDirection: 'row', alignItems: 'center', gap: 16, backgroundColor: '#1e2328', borderRadius: 12, padding: 20 },
  avatar: { width: 54, height: 54, borderRadius: 27, backgroundColor: '#374151', justifyContent: 'center', alignItems: 'center' },
  avatarText: { color: '#ffffff', fontSize: 22, fontWeight: '700' },
  userName: { color: '#ffffff', fontSize: 17, fontWeight: '600' },
  userEmail: { color: '#9ca3af', fontSize: 13, marginTop: 2 },
  userRole: { color: '#6b7280', fontSize: 12, marginTop: 2, textTransform: 'capitalize' },
  section: { backgroundColor: '#1e2328', borderRadius: 12, padding: 20, gap: 14 },
  sectionTitle: { color: '#ffffff', fontSize: 16, fontWeight: '600' },
  field: { gap: 6 },
  label: { color: '#9ca3af', fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.5 },
  input: { backgroundColor: '#374151', color: '#ffffff', borderRadius: 8, padding: 12, fontSize: 15 },
  switchRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 4 },
  switchLabel: { color: '#d1d5db', fontSize: 14, flex: 1, marginRight: 12 },
  saveBtn: { backgroundColor: '#ffffff', paddingVertical: 14, borderRadius: 9999, alignItems: 'center', marginTop: 4 },
  saveBtnText: { color: '#111518', fontSize: 15, fontWeight: '600' },
  logoutBtn: { backgroundColor: '#1e2328', borderWidth: 1, borderColor: '#ef4444', paddingVertical: 14, borderRadius: 9999, alignItems: 'center' },
  logoutBtnText: { color: '#ef4444', fontSize: 15, fontWeight: '600' },
});
