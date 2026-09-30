// Canonical branch values — MUST match the backend exactly.
// Do NOT rename or merge these. These are presentation-layer groupings only.

export const CANONICAL_BRANCHES = [
  { value: 'VIT_CE',      label: 'B.Tech Computer Engineering' },
  { value: 'VIT_IT',      label: 'B.Tech Information Technology' },
  { value: 'VIT_AIDS',    label: 'B.Tech Artificial Intelligence & Data Science' },
  { value: 'VIT_CSE_AI',  label: 'B.Tech Computer Science and Engineering (Artificial Intelligence)' },
  { value: 'VIT_CSE_AIML',label: 'B.Tech Computer Science and Engineering (AI & Machine Learning)' },
  { value: 'VIT_CS_AI',   label: 'B.Tech Computer Science and Artificial Intelligence' },
  { value: 'VIT_ENTC',    label: 'B.Tech Electronics and Telecommunication Engineering' },
  { value: 'VIT_ELEC',    label: 'B.Tech Electronics Engineering' },
  { value: 'VIT_MECH',    label: 'B.Tech Mechanical Engineering' },
] as const

// UI grouping metadata only — presentation layer, not eligibility logic
export const BRANCH_GROUPS = [
  {
    label: 'Computing & Software Engineering',
    branches: ['VIT_CE', 'VIT_IT'],
  },
  {
    label: 'Artificial Intelligence & Data Science',
    branches: ['VIT_AIDS', 'VIT_CSE_AI', 'VIT_CSE_AIML', 'VIT_CS_AI'],
  },
  {
    label: 'Electronics & Core Engineering',
    branches: ['VIT_ENTC', 'VIT_ELEC', 'VIT_MECH'],
  },
] as const

export type CanonicalBranchValue = typeof CANONICAL_BRANCHES[number]['value']

export function getBranchLabel(value: string): string {
  return CANONICAL_BRANCHES.find(b => b.value === value)?.label ?? value
}
