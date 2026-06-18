import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { authApi, clearToken, setToken } from '../services/api';
import type { UserInfo } from '../types';

interface AuthContextValue {
  user: UserInfo | null;
  isLoading: boolean;
  login: (googleIdToken: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  isLoading: true,
  login: async () => {},
  logout: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    // On mount, try to refresh the token to restore session
    authApi
      .refresh()
      .then((res) => {
        setUser(res.user);
      })
      .catch(() => {
        // Token missing, expired, or invalid — show login screen
        setUser(null);
      })
      .finally(() => setIsLoading(false));
  }, []);

  const login = useCallback(async (googleIdToken: string) => {
    const res = await authApi.loginWithGoogle(googleIdToken);
    await setToken(res.token);
    setUser(res.user);
  }, []);

  const logout = useCallback(async () => {
    await clearToken();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
