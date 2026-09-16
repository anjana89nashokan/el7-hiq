export interface RunMetrics {
  completed: number;
  pending: number;
}

export interface DashboardStats {
  user_name: string;
  session_count: number;
  profiling: RunMetrics;
  mapping: RunMetrics;
  recent_activity: APIRecentActivity[];
  sourcing_users_activity: UserActivity[];
  extract_users_activity: UserActivity[];
}

export interface APIRecentActivity {
  date: string;
  session_id: string;
  title: string;
  status: string;
}

export interface Activity {
  id: string;
  label: string;
  target: string;
  time: string;
}

export interface UserActivity {
  user_key: string;
  user_email: string;
  last_activity: string;
  session_count: number;
}