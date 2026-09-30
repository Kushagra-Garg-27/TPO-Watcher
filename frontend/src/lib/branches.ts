// Canonical branch values — MUST match the backend exactly.
// Do NOT rename or merge these. These are presentation-layer groupings only.

export interface BranchOption {
  readonly value: string
  readonly label: string
  readonly searchTerms: readonly string[]
}

// Exactly 12 active undergraduate B.Tech programmes for VIT Pune (Class of 2028)
// Reference: https://www.vit.edu/undergraduate/
export const CANONICAL_BRANCHES: readonly BranchOption[] = [
  // COMPUTING, COMPUTER SCIENCE & IT
  {
    value: 'VIT_CE',
    label: 'B.Tech Computer Engineering',
    searchTerms: ['ce', 'comp', 'computer engineering', 'software'],
  },
  {
    value: 'VIT_CSE_DS',
    label: 'B.Tech Computer Science and Engineering (Data Science)',
    searchTerms: ['cse', 'ds', 'data science', 'computer science and engineering'],
  },
  {
    value: 'VIT_IT',
    label: 'B.Tech Information Technology',
    searchTerms: ['it', 'information technology'],
  },
  {
    value: 'VIT_CSE_IOT_CS_BC',
    label: 'B.Tech Computer Science and Engineering (Internet of Things and Cyber Security Including Blockchain Technology)',
    searchTerms: ['iot', 'cyber', 'security', 'blockchain', 'cse', 'internet of things', 'cyber security'],
  },
  {
    value: 'VIT_CSE_AI',
    label: 'B.Tech Computer Science and Engineering (Artificial Intelligence)',
    searchTerms: ['cse', 'ai', 'artificial intelligence', 'computer science and engineering'],
  },
  {
    value: 'VIT_CSE_AIML',
    label: 'B.Tech Computer Science and Engineering (Artificial Intelligence and Machine Learning)',
    searchTerms: ['cse', 'ai', 'ml', 'aiml', 'artificial intelligence', 'machine learning'],
  },
  {
    value: 'VIT_AIDS',
    label: 'B.Tech Artificial Intelligence and Data Science',
    searchTerms: ['aids', 'ai', 'ds', 'artificial intelligence', 'data science', 'ai & ds'],
  },
  {
    value: 'VIT_CE_SE',
    label: 'B.Tech Computer Engineering (Software Engineering)',
    searchTerms: ['ce', 'se', 'software engineering', 'computer engineering'],
  },

  // ELECTRONICS & CONTROL
  {
    value: 'VIT_ENTC',
    label: 'B.Tech Electronics and Telecommunication Engineering',
    searchTerms: ['entc', 'electronics', 'telecommunication', 'telecom', 'extc'],
  },
  {
    value: 'VIT_ICE',
    label: 'B.Tech Instrumentation and Control Engineering',
    searchTerms: ['ice', 'instrumentation', 'control', 'instrumentation and control'],
  },

  // MECHANICAL
  {
    value: 'VIT_MECH',
    label: 'B.Tech Mechanical Engineering',
    searchTerms: ['mech', 'mechanical'],
  },

  // CIVIL
  {
    value: 'VIT_CIVIL',
    label: 'B.Tech Civil Engineering',
    searchTerms: ['civil', 'civil engineering'],
  },
] as const

// Historical / backward-compatibility branch mappings (preserved for historical profiles)
export const HISTORICAL_BRANCHES = [
  { value: 'VIT_CS_AI', label: 'B.Tech Computer Science and Artificial Intelligence' },
  { value: 'VIT_ELEC', label: 'B.Tech Electronics Engineering' },
] as const

// UI grouping metadata only — exactly 4 categories representing all 12 programmes
export const BRANCH_GROUPS = [
  {
    label: 'COMPUTING, COMPUTER SCIENCE & IT',
    branches: [
      'VIT_CE',
      'VIT_CSE_DS',
      'VIT_IT',
      'VIT_CSE_IOT_CS_BC',
      'VIT_CSE_AI',
      'VIT_CSE_AIML',
      'VIT_AIDS',
      'VIT_CE_SE',
    ],
  },
  {
    label: 'ELECTRONICS & CONTROL',
    branches: ['VIT_ENTC', 'VIT_ICE'],
  },
  {
    label: 'MECHANICAL',
    branches: ['VIT_MECH'],
  },
  {
    label: 'CIVIL',
    branches: ['VIT_CIVIL'],
  },
] as const

export type CanonicalBranchValue = typeof CANONICAL_BRANCHES[number]['value']

export function getBranchLabel(value: string): string {
  const current = CANONICAL_BRANCHES.find(b => b.value === value)
  if (current) return current.label
  const historical = HISTORICAL_BRANCHES.find(b => b.value === value)
  if (historical) return historical.label
  return value
}
