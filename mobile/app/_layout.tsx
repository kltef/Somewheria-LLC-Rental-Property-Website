import { useRouter, useSegments } from 'expo-router';
import { Stack } from 'expo-router';
import { useEffect, useRef } from 'react';
import { AuthProvider, useAuth } from '../src/context/AuthContext';
import { setupPushNotifications } from '../src/utils/push';

function NavigationGuard() {
  const { user, isLoading } = useAuth();
  const router = useRouter();
  const segments = useSegments();

  useEffect(() => {
    if (isLoading) return;
    const inAuthGroup = segments[0] === '(auth)';
    if (!user && !inAuthGroup) {
      router.replace('/(auth)/login');
    } else if (user && inAuthGroup) {
      router.replace('/(tabs)');
    }
  }, [user, isLoading, segments]);

  return null;
}

function PushSetup() {
  const { user } = useAuth();
  const router = useRouter();
  const registered = useRef(false);

  useEffect(() => {
    if (!user || registered.current) return;
    registered.current = true;
    setupPushNotifications((data) => {
      if (data?.ticket_id) {
        router.push(`/(tabs)/tickets/${data.ticket_id}`);
      }
    });
  }, [user]);

  return null;
}

export default function RootLayout() {
  return (
    <AuthProvider>
      <NavigationGuard />
      <PushSetup />
      <Stack screenOptions={{ headerShown: false }} />
    </AuthProvider>
  );
}
