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

// Historical / database-backed opportunity record from /api/v1/opportunities
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

// Verified opportunity record from /api/v1/opportunity-listing
export interface VerifiedOpportunity {
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

// Backwards-compatible alias for verified listing items
export type Opportunity = VerifiedOpportunity

export interface OpportunityListing {
  items: VerifiedOpportunity[]
  total: number
  shown: number
  status: 'VERIFIED' | 'STALE' | 'UNVERIFIED'
  observed_ms: number | null
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

function isLegacyOpportunityArray(value: unknown): value is LegacyOpportunity[] {
  if (!Array.isArray(value)) return false
  const nullableFields = ['max_package', 'min_package', 'placement_type', 'registration_end',
    'eligible_programs', 'company_type', 'is_active', 'first_seen_at']
  return value.every((item: unknown) => {
    if (typeof item !== 'object' || item === null) return false
    const rec = item as Record<string, unknown>
    if (typeof rec.id !== 'string' || !rec.id.trim() ||
        typeof rec.company !== 'string' || !rec.company.trim()) return false
    return nullableFields.every(key => rec[key] === null || rec[key] === undefined || typeof rec[key] === 'string')
  })
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

  async getOpportunities(): Promise<ApiResult<LegacyOpportunity[]>> {
    const res = await request<unknown>('/api/v1/opportunities')
    if (!res.ok) return { ok: false, error: res.error, status: res.status }
    if (!isLegacyOpportunityArray(res.data)) {
      return { ok: false, error: 'Opportunities response is invalid.', status: 502 }
    }
    return { ok: true, data: res.data }
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
