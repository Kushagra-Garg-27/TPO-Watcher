import { motion } from 'framer-motion'
import { Briefcase, Award, Building2 } from 'lucide-react'
import { cn } from '../lib/utils'

interface Preference {
  id: 'pref_internship' | 'pref_placement' | 'pref_ppo'
  icon: React.ReactNode
  title: string
  description: string
}

const PREFERENCES: Preference[] = [
  {
    id: 'pref_internship',
    icon: <Briefcase className="h-5 w-5" />,
    title: 'Internships',
    description: 'Get alerts for internship opportunities.',
  },
  {
    id: 'pref_ppo',
    icon: <Award className="h-5 w-5" />,
    title: 'Internship + PPO',
    description: 'Alerts when an internship includes a performance-based PPO.',
  },
  {
    id: 'pref_placement',
    icon: <Building2 className="h-5 w-5" />,
    title: 'Full-time Placements',
    description: 'Get alerts for full-time placement opportunities.',
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
      {PREFERENCES.map(pref => {
        const isSelected = values[pref.id]
        return (
          <motion.label
            key={pref.id}
            htmlFor={`pref-${pref.id}`}
            whileTap={{ scale: 0.985 }}
            transition={{ duration: 0.12 }}
            className={cn(
              'pref-card',
              isSelected && 'selected',
            )}
          >
            {/* Hidden native checkbox for a11y */}
            <input
              type="checkbox"
              id={`pref-${pref.id}`}
              checked={isSelected}
              onChange={e => onChange(pref.id, e.target.checked)}
              className="sr-only"
            />

            {/* Icon */}
            <motion.div
              animate={{
                backgroundColor: isSelected ? 'rgb(238,242,255)' : 'rgb(248,250,252)',
                color: isSelected ? 'rgb(79,70,229)' : 'rgb(100,116,139)',
              }}
              transition={{ duration: 0.15 }}
              className="flex-shrink-0 flex h-9 w-9 items-center justify-center rounded-lg mt-0.5"
            >
              {pref.icon}
            </motion.div>

            {/* Text */}
            <div className="flex-1 min-w-0">
              <p className={cn(
                'text-sm font-semibold transition-colors duration-150',
                isSelected ? 'text-brand-700' : 'text-slate-800'
              )}>
                {pref.title}
              </p>
              <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">
                {pref.description}
              </p>
            </div>

            {/* Checkmark */}
            <div className={cn(
              'flex-shrink-0 flex h-5 w-5 items-center justify-center rounded mt-0.5',
              'border-2 transition-all duration-150',
              isSelected
                ? 'bg-brand-600 border-brand-600'
                : 'bg-white border-slate-300'
            )}>
              {isSelected && (
                <motion.svg
                  initial={{ scale: 0, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ type: 'spring', stiffness: 500, damping: 25 }}
                  className="h-3 w-3 text-white"
                  fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </motion.svg>
              )}
            </div>
          </motion.label>
        )
      })}
    </div>
  )
}
