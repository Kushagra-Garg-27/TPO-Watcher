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

export interface AccountStatus {
  email: string
  email_verified: boolean
  role: 'SUBSCRIBER' | 'ADMINISTRATOR'
  account_state: 'ACTIVE' | 'SUSPENDED' | 'REVOKED'
  review_state: 'UNREVIEWED' | 'APPROVED' | 'REJECTED'
  branch_canonical: string
  graduation_year: number
  notification_email: string | null
  destination_verified: boolean
  consent_state: 'NEVER' | 'GRANTED' | 'WITHDRAWN'
  consent_policy: 'student-email-v1'
  delivery_eligible: boolean
  enrollment_model: 'OPERATOR_REVIEW_REQUIRED'
}

export interface LegacyOpportunity {
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

export interface Opportunity extends LegacyOpportunity {
  registration_start: string | null
  registration_start_time: string | null
  registration_end_time: string | null
  registration_start_alternate: string | null
  registration_end_alternate: string | null
  deadline_timezone: string | null
  compensation_unit: string | null
  organizations: string | null
  job_title: string | null
  job_description: string | null
  academic_year: string | null
  eligibility_status: 'PROGRAMME_MATCH_ONLY'
  listing_status: 'CURRENT'
  observed_ms: number
  valid_until_ms: number
}

export interface OpportunityListing {
  items: Opportunity[]
  total: number
  shown: number
  status: 'VERIFIED' | 'STALE' | 'UNVERIFIED'
  observed_ms: number | null
}

export interface OpportunityFeed {
  items: (LegacyOpportunity & Partial<Opportunity>)[]
  total: number | null
  shown: number
  status: OpportunityListing['status'] | 'STORED'
  observed_ms: number | null
}

function isLegacyOpportunities(value: unknown): value is LegacyOpportunity[] {
  if (!Array.isArray(value) || value.length > 5) return false
  const ids = new Set<string>()
  return value.every(item => {
    if (typeof item !== 'object' || item === null ||
        typeof item.id !== 'string' || !item.id.trim() || ids.has(item.id) ||
        typeof item.company !== 'string' || !item.company.trim() ||
        typeof item.is_active !== 'string' || item.is_active.trim().toLowerCase() !== 'true' ||
        !['max_package', 'min_package', 'placement_type', 'registration_end',
          'eligible_programs', 'company_type', 'first_seen_at'].every(key =>
          item[key] === null || typeof item[key] === 'string')) return false
    ids.add(item.id)
    return true
  })
}

export interface ApiResult<T = null> {
  ok: boolean
  data?: T
  error?: string
  status?: number
}

function isOpportunityListing(value: unknown): value is OpportunityListing {
  if (typeof value !== 'object' || value === null) return false
  const data = value as Record<string, unknown>
  const timestamp = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) >= 0
  if (!Array.isArray(data.items) || data.items.length > 5 ||
      !timestamp(data.total) || data.total < data.items.length || data.shown !== data.items.length ||
      typeof data.status !== 'string' || !['VERIFIED', 'STALE', 'UNVERIFIED'].includes(data.status) ||
      !(data.observed_ms === null || timestamp(data.observed_ms))) return false
  if (data.items.length > 0 && data.status !== 'VERIFIED') return false
  if (data.status !== 'UNVERIFIED' && data.observed_ms === null) return false
  const ids = new Set<string>()
  const nullableFields = ['max_package', 'min_package', 'placement_type', 'registration_end',
    'eligible_programs', 'company_type', 'first_seen_at', 'registration_start',
    'registration_start_time', 'registration_end_time', 'registration_start_alternate',
    'registration_end_alternate', 'deadline_timezone', 'compensation_unit', 'organizations',
    'job_title', 'job_description', 'academic_year']
  return data.items.every((value: unknown) => {
    if (typeof value !== 'object' || value === null) return false
    const item = value as Record<string, unknown>
    if (typeof item.id !== 'string' || !item.id.trim() || ids.has(item.id) ||
        typeof item.company !== 'string' || !item.company.trim() ||
        typeof item.is_active !== 'string' || item.is_active.trim().toLowerCase() !== 'true' ||
        item.listing_status !== 'CURRENT' || item.eligibility_status !== 'PROGRAMME_MATCH_ONLY' ||
        !timestamp(item.observed_ms) || !timestamp(item.valid_until_ms) ||
        item.valid_until_ms < item.observed_ms ||
        data.observed_ms === null || (data.observed_ms as number) < item.observed_ms ||
        !nullableFields.every(key => item[key] === null || typeof item[key] === 'string')) return false
    ids.add(item.id)
    return true
  })
}

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<ApiResult<T>> {
  try {
    const res = await fetch(BASE + path, {
      credentials: 'same-origin',
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
      const detail = typeof body === 'object' && body !== null
        ? (body as { detail?: unknown }).detail : undefined
      const err = typeof detail === 'string' ? detail : Array.isArray(detail)
        ? detail.map(entry => typeof entry?.msg === 'string' ? entry.msg : 'Invalid request.').join(' ')
        : 'Request could not be completed. Please try again.'
      return { ok: false, error: err, status: res.status }
    }

    return { ok: true, data: body as T }
  } catch {
    return { ok: false, error: 'Network error. Please check your connection and try again.' }
  }
}

export const api = {
  getAccount(): Promise<ApiResult<AccountStatus>> {
    return request('/api/v1/account')
  },

  updateProfile(branch_canonical: string): Promise<ApiResult<AccountStatus>> {
    return request('/api/v1/account/profile', { method: 'PATCH', body: JSON.stringify({ branch_canonical, graduation_year: 2028 }) })
  },

  setConsent(consent: boolean): Promise<ApiResult<AccountStatus>> {
    return request('/api/v1/account/consent', { method: 'POST', body: JSON.stringify({ consent, policy_version: 'student-email-v1' }) })
  },

  requestDestination(email: string): Promise<ApiResult> {
    return request('/api/v1/account/destination', { method: 'POST', body: JSON.stringify({ email }) })
  },

  confirmDestination(token: string): Promise<ApiResult> {
    return request('/api/v1/account/destination/confirm', { method: 'POST', body: JSON.stringify({ token }) })
  },

  logoutAll(): Promise<ApiResult> {
    return request('/api/v1/account/logout-all', { method: 'POST' })
  },

  revokeAccount(): Promise<ApiResult> {
    return request('/api/v1/account', { method: 'DELETE' })
  },
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

  async getOpportunities(): Promise<ApiResult<LegacyOpportunity[]>> {
    return request('/api/v1/opportunities')
  },

  async getOpportunityFeed(): Promise<ApiResult<OpportunityFeed>> {
    const current = await api.getOpportunityListing()
    if (current.status !== 404) return current
    // Only an absent route permits compatibility fallback. Failed source or
    // custody checks must never be disguised as a fresh legacy observation.
    const legacy = await request<unknown>('/api/v1/opportunities')
    if (!legacy.ok) return { ok: false, error: legacy.error, status: legacy.status }
    if (!isLegacyOpportunities(legacy.data)) return { ok: false, error: 'Stored opportunities response is invalid.' }
    return { ok: true, data: { items: legacy.data, shown: legacy.data.length,
      total: null, status: 'STORED', observed_ms: null } }
  },

  async getOpportunityListing(): Promise<ApiResult<OpportunityListing>> {
    const result = await request<unknown>('/api/v1/opportunity-listing')
    if (!result.ok) return { ok: false, error: result.error, status: result.status }
    if (!isOpportunityListing(result.data)) {
      return { ok: false, error: 'Verified listing response is invalid.' }
    }
    return { ok: true, data: result.data }
  },

  async logout(): Promise<ApiResult<{ message: string; status: string }>> {
    return request('/api/v1/auth/logout', {
      method: 'POST',
    })
  },
}
