// API client — thin wrappers preserving exact backend field names.
// Under no circumstances rename or reorder these fields.

const BASE = ''  // same-origin

export interface SignupPayload {
  email: string
  graduation_year: 2028
  branch_canonical: string
  pref_internship: boolean
  pref_placement: boolean
  pref_ppo: boolean
}

export interface PreferenceUpdate {
  pref_internship: boolean
  pref_placement: boolean
  pref_ppo: boolean
}

export interface PreferencesResponse {
  email: string
  graduation_year: number
  branch_canonical: string
  pref_internship: boolean
  pref_placement: boolean
  pref_ppo: boolean
}

export interface Opportunity {
  id: string
  company: string
  max_package: string | null
  min_package: string | null
  placement_type: string | null
  registration_end: string | null
  eligible_programs: string | null
  company_type: string | null
  is_active: string | null
  first_seen_at: string | null
}

export interface ApiResult<T = null> {
  ok: boolean
  data?: T
  error?: string
  status?: number
}

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<ApiResult<T>> {
  try {
    const res = await fetch(BASE + path, {
      headers: { 'Content-Type': 'application/json', ...options?.headers },
      ...options,
    })

    let body: unknown
    const ct = res.headers.get('content-type') || ''
    if (ct.includes('application/json')) {
      body = await res.json()
    } else {
      body = await res.text()
    }

    if (!res.ok) {
      const err = typeof body === 'object' && body !== null
        ? (body as { detail?: string }).detail || JSON.stringify(body)
        : String(body)
      return { ok: false, error: err, status: res.status }
    }

    return { ok: true, data: body as T }
  } catch {
    return { ok: false, error: 'Network error. Please check your connection and try again.' }
  }
}

export const api = {
  async signup(payload: SignupPayload): Promise<ApiResult<{ message: string; status: string }>> {
    return request('/api/v1/auth/signup', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  async requestPreferenceLink(email: string): Promise<ApiResult> {
    return request('/api/v1/preferences/request-link', {
      method: 'POST',
      body: JSON.stringify({ email }),
    })
  },

  async getPreferences(): Promise<ApiResult<PreferencesResponse>> {
    return request('/api/v1/preferences')
  },

  async updatePreferences(payload: PreferenceUpdate): Promise<ApiResult> {
    return request('/api/v1/preferences', {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  },

  async verifyConfirm(token: string): Promise<ApiResult<{ message: string; status: string }>> {
    return request('/api/v1/auth/verify/confirm', {
      method: 'POST',
      body: JSON.stringify({ token }),
    })
  },

  async unsubscribeConfirm(token: string): Promise<ApiResult<{ message: string; status: string }>> {
    return request('/api/v1/unsubscribe/confirm', {
      method: 'POST',
      body: JSON.stringify({ token }),
    })
  },

  async preferencesConfirm(token: string): Promise<ApiResult<{ message: string; status: string }>> {
    return request('/api/v1/preferences/confirm', {
      method: 'POST',
      body: JSON.stringify({ token }),
    })
  },

  async getOpportunities(): Promise<ApiResult<Opportunity[]>> {
    return request('/api/v1/opportunities')
  },
}
