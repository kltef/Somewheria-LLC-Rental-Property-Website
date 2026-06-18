import * as SecureStore from 'expo-secure-store';
import type {
  AdminDashboard,
  Contract,
  PendingRegistration,
  Property,
  RenterProfile,
  Ticket,
  TicketCategory,
  TicketPriority,
  TicketStatus,
  UserInfo,
} from '../types';

const API_BASE = process.env.EXPO_PUBLIC_API_BASE_URL ?? 'http://localhost:5000';
const TOKEN_KEY = 'api_token';

export class ApiError extends Error {
  constructor(
    message: string,
    public statusCode: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function getToken(): Promise<string | null> {
  return SecureStore.getItemAsync(TOKEN_KEY);
}

export async function setToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(TOKEN_KEY, token);
}

export async function clearToken(): Promise<void> {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
}

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      message = body.error ?? message;
    } catch {
      // ignore parse error
    }
    throw new ApiError(message, res.status);
  }
  return res.json() as Promise<T>;
}

// ------------------------------------------------------------------ auth

export const authApi = {
  loginWithGoogle: (idToken: string) =>
    apiFetch<{ token: string; user: UserInfo }>('/api/v1/auth/google', {
      method: 'POST',
      body: JSON.stringify({ id_token: idToken }),
    }),

  refresh: () =>
    apiFetch<{ token: string; user: UserInfo }>('/api/v1/auth/refresh', {
      method: 'POST',
    }),
};

// ------------------------------------------------------------------ properties

export const propertiesApi = {
  list: () => apiFetch<{ properties: Property[] }>('/api/v1/properties'),
  get: (uuid: string) => apiFetch<Property>(`/api/v1/properties/${uuid}`),
  schedule: (uuid: string, data: {
    name: string;
    date: string;
    contact_method: string;
    contact_info: string;
  }) =>
    apiFetch<{ success: boolean }>(`/api/v1/properties/${uuid}/schedule`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
};

// ------------------------------------------------------------------ profile

export const profileApi = {
  get: () => apiFetch<RenterProfile>('/api/v1/profile'),
  update: (data: Partial<RenterProfile>) =>
    apiFetch<{ success: boolean; profile: RenterProfile }>('/api/v1/profile', {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
};

// ------------------------------------------------------------------ contracts

export const contractsApi = {
  list: () => apiFetch<{ contracts: Contract[] }>('/api/v1/contracts'),
};

// ------------------------------------------------------------------ tickets

export const ticketsApi = {
  list: () => apiFetch<{ tickets: Ticket[] }>('/api/v1/tickets'),

  create: (data: {
    title: string;
    description: string;
    category: TicketCategory;
    priority: TicketPriority;
    submitter_name?: string;
    contact?: string;
    property_id?: string;
    email_updates?: boolean;
  }) =>
    apiFetch<{ ticket: Ticket }>('/api/v1/tickets', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  get: (id: string) => apiFetch<{ ticket: Ticket }>(`/api/v1/tickets/${id}`),

  setEmailUpdates: (id: string, enabled: boolean) =>
    apiFetch<{ ticket: Ticket }>(`/api/v1/tickets/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ email_updates: enabled }),
    }),

  addNote: (id: string, text: string) =>
    apiFetch<{ ticket: Ticket }>(`/api/v1/tickets/${id}/notes`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
};

// ------------------------------------------------------------------ push

export const pushApi = {
  register: (token: string) =>
    apiFetch<{ success: boolean }>('/api/v1/push-token', {
      method: 'POST',
      body: JSON.stringify({ token }),
    }),
};

// ------------------------------------------------------------------ admin

export const adminApi = {
  dashboard: () => apiFetch<AdminDashboard>('/api/v1/admin/dashboard'),

  tickets: (params?: { status?: TicketStatus; priority?: TicketPriority; q?: string }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set('status', params.status);
    if (params?.priority) qs.set('priority', params.priority);
    if (params?.q) qs.set('q', params.q);
    const query = qs.toString() ? `?${qs}` : '';
    return apiFetch<{ tickets: Ticket[]; summary: AdminDashboard['tickets'] }>(`/api/v1/admin/tickets${query}`);
  },

  updateTicket: (id: string, updates: {
    status?: TicketStatus;
    priority?: TicketPriority;
    assigned_to?: string;
  }) =>
    apiFetch<{ ticket: Ticket }>(`/api/v1/admin/tickets/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    }),

  users: () => apiFetch<{ users: Array<{ email: string; role: string }> }>('/api/v1/admin/users'),

  registrations: () => apiFetch<{ registrations: PendingRegistration[] }>('/api/v1/admin/registrations'),

  approveRegistration: (email: string) =>
    apiFetch<{ success: boolean }>('/api/v1/admin/registrations', {
      method: 'POST',
      body: JSON.stringify({ email, action: 'approve' }),
    }),

  rejectRegistration: (email: string) =>
    apiFetch<{ success: boolean }>('/api/v1/admin/registrations', {
      method: 'POST',
      body: JSON.stringify({ email, action: 'reject' }),
    }),

  listings: () => apiFetch<{ properties: Property[] }>('/api/v1/admin/listings'),

  toggleSale: (uuid: string) =>
    apiFetch<{ success: boolean }>(`/api/v1/admin/listings/${uuid}/toggle-sale`, { method: 'POST' }),

  deleteListing: (uuid: string) =>
    apiFetch<{ success: boolean }>(`/api/v1/admin/listings/${uuid}`, { method: 'DELETE' }),
};
