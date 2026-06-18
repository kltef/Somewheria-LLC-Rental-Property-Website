export interface UserInfo {
  email: string;
  name: string;
  role: 'renter' | 'admin' | 'high_admin';
}

export interface Property {
  id: string;
  name: string;
  address: string;
  rent: string;
  deposit: string;
  bedrooms: string;
  bathrooms: string;
  sqft: string;
  lease_length: string;
  pets_allowed: 'Yes' | 'No' | 'Unknown';
  ada_accessible: 'Yes' | 'No' | 'Unknown';
  blurb: string;
  description: string;
  included_amenities: string[];
  custom_amenities: string;
  photos: string[];
  thumbnail: string;
  tour_url: string;
  for_sale?: boolean;
}

export interface TicketNote {
  at: string;
  by: string;
  text: string;
}

export type TicketStatus = 'open' | 'in_progress' | 'awaiting_parts' | 'resolved' | 'closed';
export type TicketPriority = 'low' | 'normal' | 'high' | 'urgent';
export type TicketCategory = 'plumbing' | 'electrical' | 'appliance' | 'hvac' | 'structural' | 'pest' | 'other';

export interface Ticket {
  id: string;
  created_at: string;
  updated_at: string;
  submitted_by: string;
  submitter_name: string;
  contact: string;
  property_id: string;
  property_name: string;
  category: TicketCategory;
  priority: TicketPriority;
  title: string;
  description: string;
  status: TicketStatus;
  assigned_to: string;
  email_updates: boolean;
  notes: TicketNote[];
  photos: string[];
  jira_key?: string;
}

export interface Contract {
  id: string;
  property_name: string;
  start_date: string;
  end_date: string;
  status: string;
  status_class: 'active' | 'pending' | 'ended';
  pdf_filename: string;
  pdf_url: string;
  created_at: string;
}

export interface RenterProfile {
  email: string;
  name: string;
  contact: string;
  email_status_updates: boolean;
  rcs_status_updates: boolean;
}

export interface AdminDashboard {
  tickets: { total: number; open: number; urgent: number };
  ticket_status_counts: Record<string, number>;
  property_count: number;
  pending_registrations: number;
}

export interface PendingRegistration {
  email: string;
  name?: string;
  message?: string;
  submitted_at?: string;
}
