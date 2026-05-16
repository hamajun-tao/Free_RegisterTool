export interface Platform {
  name: string
  display_name: string
}

export interface Account {
  id: number
  platform: string
  email: string
  password: string
  user_id: string
  region: string
  token: string
  status: string
  trial_end_time: number
  cashier_url: string
  extra_json: string
  created_at: string
  updated_at: string
}

export interface AccountListResponse {
  items: Account[]
  total: number
}

export interface TaskLogItem {
  id: number
  created_at: string
  platform: string
  email: string
  status: 'success' | 'failed'
  error: string
}

export interface TaskLogListResponse {
  total: number
  items: TaskLogItem[]
}

export interface ProxyItem {
  id: number
  url: string
  region: string
  success_count: number
  fail_count: number
  is_active: boolean
  last_checked: string | null
}

export interface ApiError {
  message: string
  detail?: string
  status?: number
}

export interface BatchResult {
  deleted: number
  not_found: number[]
  total_requested: number
}
