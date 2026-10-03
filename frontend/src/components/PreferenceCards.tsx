import { motion } from 'framer-motion'
import { Briefcase, Award, Building2, Check } from 'lucide-react'
import { cn } from '../lib/utils'

interface Preference {
  id: 'pref_internship' | 'pref_placement' | 'pref_ppo'
  icon: typeof Briefcase
  title: string
  description: string
  badge: string
}

const PREFERENCES: Preference[] = [
  {
    id: 'pref_internship',
    icon: Briefcase,
    title: 'INTERNSHIPS',
    description: 'Autonomous alerts for short-term summer or winter engineering internships.',
    badge: 'SUMMER / WINTER',
  },
  {
    id: 'pref_ppo',
    icon: Award,
    title: 'INTERNSHIP + PPO',
    description: 'Opportunities featuring performance-linked Pre-Placement Offers for full-time conversion.',
    badge: 'HIGH CONVERSION',
  },
  {
    id: 'pref_placement',
    icon: Building2,
    title: 'FULL-TIME PLACEMENTS',
    description: 'Direct graduate recruitment drives for permanent campus placements.',
    badge: 'FULL-TIME ROLES',
  },
]

interface PreferenceCardsProps {
  values: {
    pref_internship: boolean
    pref_placement: boolean
    pref_ppo: boolean
  }
  onChange: (id: 'pref_internship' | 'pref_placement' | 'pref_ppo', checked: boolean) => void
}

export function PreferenceCards({ values, onChange }: PreferenceCardsProps) {
  return (
    <div
      className="flex flex-col gap-3"
      role="group"
      aria-label="Opportunity alert preferences"
    >
      {PREFERENCES.map((pref) => {
        const isSelected = values[pref.id]
        const Icon = pref.icon
        return (
          <motion.label
            key={pref.id}
            htmlFor={`pref-${pref.id}`}
            whileTap={{ scale: 0.985 }}
            transition={{ duration: 0.12 }}
            className={cn(
              'pref-card border rounded-lg p-4 flex items-center justify-between gap-4 cursor-pointer transition-all duration-200 select-none',
              isSelected
                ? 'bg-surface-2 border-accent shadow-[0_0_16px_rgba(255,59,29,0.12)]'
                : 'bg-surface-1 border-line hover:border-line-strong'
            )}
          >
            {/* Hidden native checkbox for accessibility */}
            <input
              type="checkbox"
              id={`pref-${pref.id}`}
              checked={isSelected}
              onChange={(e) => onChange(pref.id, e.target.checked)}
              className="sr-only"
            />

            <div className="flex items-start gap-3.5">
              <div
                className={cn(
                  'w-9 h-9 rounded flex items-center justify-center shrink-0 border transition-colors',
                  isSelected
                    ? 'bg-accent/15 border-accent/30 text-accent'
                    : 'bg-surface-2 border-line text-muted'
                )}
              >
                <Icon className="h-4 w-4" />
              </div>

              <div>
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      'font-display text-base uppercase tracking-wider',
                      isSelected ? 'text-foreground' : 'text-muted'
                    )}
                  >
                    {pref.title}
                  </span>
                  <span className="text-[11px] font-mono tracking-wider text-muted/60 uppercase">
                    [{pref.badge}]
                  </span>
                </div>
                <p className="text-xs text-muted font-light mt-0.5 leading-relaxed font-sans">
                  {pref.description}
                </p>
              </div>
            </div>

            {/* Custom Check Indicator */}
            <div
              className={cn(
                'w-5 h-5 rounded border flex items-center justify-center shrink-0 transition-all',
                isSelected
                  ? 'bg-accent border-accent text-white'
                  : 'bg-surface-2 border-line text-transparent'
              )}
            >
              <Check className="h-3 w-3 stroke-[3]" />
            </div>
          </motion.label>
        )
      })}
    </div>
  )
}
