import { useState, useRef } from 'react'
import { motion, AnimatePresence, type Variants } from 'framer-motion'
import {
  Bell, ShieldCheck, Mail, ArrowRight,
  CheckCircle2, AlertCircle, Loader2,
  GraduationCap, MapPin, ChevronDown
} from 'lucide-react'
import { BranchSelector } from '../components/BranchSelector'
import { PreferenceCards } from '../components/PreferenceCards'
import { api } from '../lib/api'
import { cn, isValidEmail } from '../lib/utils'

interface SignupPageProps {
  navigate: (path: string) => void
}

// Stagger animation helper
const staggerContainer: Variants = {
  hidden: {},
  show: {
    transition: { staggerChildren: 0.08, delayChildren: 0.15 }
  },
}

const staggerItem: Variants = {
  hidden: { opacity: 0, y: 16 },
  show:   { opacity: 1, y: 0 },
}

type FormState = 'idle' | 'loading' | 'success' | 'error'

interface FormErrors {
  email?: string
  branch?: string
  prefs?: string
}

export function SignupPage({ navigate }: SignupPageProps) {
  const [email, setEmail] = useState('')
  const [branch, setBranch] = useState('')
  const [prefs, setPrefs] = useState({
    pref_internship: true,
    pref_placement: true,
    pref_ppo: true,
  })
  const [formState, setFormState] = useState<FormState>('idle')
  const [errors, setErrors] = useState<FormErrors>({})
  const [successMsg, setSuccessMsg] = useState('')
  const [apiError, setApiError] = useState('')

  const formRef = useRef<HTMLFormElement>(null)

  function validate(): boolean {
    const newErrors: FormErrors = {}
    if (!email.trim()) {
      newErrors.email = 'Email address is required.'
    } else if (!isValidEmail(email)) {
      newErrors.email = 'Enter a valid email address.'
    }
    if (!branch) newErrors.branch = 'Please select your engineering branch.'
    if (!prefs.pref_internship && !prefs.pref_placement && !prefs.pref_ppo) {
      newErrors.prefs = 'Select at least one opportunity type.'
    }
    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!validate()) return

    setFormState('loading')
    setApiError('')

    const result = await api.signup({
      email: email.trim().toLowerCase(),
      graduation_year: 2028,
      branch_canonical: branch,
      pref_internship: prefs.pref_internship,
      pref_placement: prefs.pref_placement,
      pref_ppo: prefs.pref_ppo,
    })

    if (result.ok) {
      setFormState('success')
      setSuccessMsg(result.data?.message || 'Verification link sent! Check your inbox.')
    } else {
      setFormState('error')
      setApiError(result.error || 'Something went wrong. Please try again.')
    }
  }

  function prefChange(id: 'pref_internship' | 'pref_placement' | 'pref_ppo', val: boolean) {
    setPrefs(p => ({ ...p, [id]: val }))
    if (errors.prefs) setErrors(e => ({ ...e, prefs: undefined }))
  }

  return (
    <div className="min-h-screen">
      {/* Hero section */}
      <section className="relative pt-24 pb-16 sm:pt-32 sm:pb-20 overflow-hidden">
        {/* Subtle grid background */}
        <div className="absolute inset-0 bg-grid-pattern opacity-100" aria-hidden="true" />

        {/* Gradient orbs */}
        <div
          className="hero-orb"
          style={{
            top: '5%', left: '10%', width: '500px', height: '500px',
            background: 'radial-gradient(circle, rgba(79,70,229,0.08), transparent 70%)'
          }}
          aria-hidden="true"
        />
        <div
          className="hero-orb"
          style={{
            top: '20%', right: '5%', width: '400px', height: '400px',
            background: 'radial-gradient(circle, rgba(99,102,241,0.06), transparent 70%)'
          }}
          aria-hidden="true"
        />

        <div className="section-container relative">
          <motion.div
            variants={staggerContainer}
            initial="hidden"
            animate="show"
            className="max-w-2xl"
          >
            {/* Eyebrow */}
            <motion.div variants={staggerItem} transition={{ duration: 0.5, ease: 'easeOut' }} className="flex items-center gap-2 mb-6">
              <span className="badge-brand gap-2">
                <MapPin className="h-3 w-3" />
                VIT Pune · Class of 2028
              </span>
              <span className="badge-success gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-success-500 animate-pulse-slow" />
                Automatic monitoring active
              </span>
            </motion.div>

            {/* Headline */}
            <motion.h1
              variants={staggerItem}
              transition={{ duration: 0.5, ease: 'easeOut' }}
              className="text-4xl sm:text-5xl lg:text-6xl font-bold text-slate-900 leading-[1.08] tracking-[-0.025em]"
            >
              Never miss a relevant{' '}
              <span className="text-brand-600">
                TPO opportunity.
              </span>
            </motion.h1>

            {/* Supporting text */}
            <motion.p
              variants={staggerItem}
              transition={{ duration: 0.5, ease: 'easeOut' }}
              className="mt-5 text-lg text-slate-600 leading-relaxed max-w-xl"
            >
              We periodically monitor the VIT TPO portal and email you when a matching
              opportunity is detected.
            </motion.p>

            {/* Trust indicators */}
            <motion.div variants={staggerItem} transition={{ duration: 0.5, ease: 'easeOut' }} className="mt-6 flex flex-wrap gap-4">
              {[
                { icon: <Mail className="h-4 w-4" />, text: 'Email alerts for your branch' },
                { icon: <ShieldCheck className="h-4 w-4" />, text: 'TPO credentials never requested' },
                { icon: <GraduationCap className="h-4 w-4" />, text: 'Built for VIT Pune 2028' },
              ].map(({ icon, text }) => (
                <span key={text} className="flex items-center gap-1.5 text-sm text-slate-600">
                  <span className="text-brand-500">{icon}</span>
                  {text}
                </span>
              ))}
            </motion.div>

            {/* CTAs */}
            <motion.div variants={staggerItem} transition={{ duration: 0.5, ease: 'easeOut' }} className="mt-8 flex items-center gap-3">
              <a
                href="#signup"
                onClick={e => {
                  e.preventDefault()
                  document.querySelector('#signup')?.scrollIntoView({ behavior: 'smooth' })
                }}
                className="btn-primary"
              >
                Get TPO Alerts
                <ArrowRight className="h-4 w-4" />
              </a>
              <a
                href="#how-it-works"
                onClick={e => {
                  e.preventDefault()
                  document.querySelector('#how-it-works')?.scrollIntoView({ behavior: 'smooth' })
                }}
                className="btn-secondary"
              >
                How it works
                <ChevronDown className="h-4 w-4" />
              </a>
            </motion.div>
          </motion.div>
        </div>
      </section>

      {/* How it works */}
      <section id="how-it-works" className="py-16 sm:py-20 bg-slate-50 border-y border-slate-100">
        <div className="section-container">
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-80px' }}
            transition={{ duration: 0.5 }}
            className="text-center mb-12"
          >
            <h2 className="text-2xl sm:text-3xl font-bold text-slate-900 tracking-tight">
              How it works
            </h2>
            <p className="mt-3 text-slate-600 text-base max-w-md mx-auto">
              Three steps to start receiving matching TPO alerts.
            </p>
          </motion.div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 max-w-3xl mx-auto">
            {[
              { step: '01', title: 'Choose your branch', description: 'Select your engineering programme from the 9 canonical VIT Pune branches.' },
              { step: '02', title: 'Choose opportunity types', description: 'Pick which types of opportunities you want to be notified about.' },
              { step: '03', title: 'Receive email alerts', description: 'Get notified by email whenever a matching TPO opportunity is detected.' },
            ].map(({ step, title, description }, i) => (
              <motion.div
                key={step}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: '-60px' }}
                transition={{ duration: 0.45, delay: i * 0.1 }}
                className="relative"
              >
                <div className="card p-6 h-full">
                  <span className="text-[40px] font-bold text-brand-100 leading-none select-none">
                    {step}
                  </span>
                  <h3 className="mt-2 text-sm font-semibold text-slate-900">{title}</h3>
                  <p className="mt-1.5 text-sm text-slate-500 leading-relaxed">{description}</p>
                </div>
                {/* Connector */}
                {i < 2 && (
                  <div className="hidden md:block absolute top-1/2 -right-3 w-6 h-px bg-slate-200" aria-hidden="true" />
                )}
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Opportunity types */}
      <section id="about" className="py-16 sm:py-20">
        <div className="section-container">
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-80px' }}
            transition={{ duration: 0.5 }}
            className="text-center mb-10"
          >
            <h2 className="text-2xl sm:text-3xl font-bold text-slate-900 tracking-tight">
              Opportunity types
            </h2>
            <p className="mt-3 text-slate-600 text-base max-w-sm mx-auto">
              Subscribe to any combination of these placement types.
            </p>
          </motion.div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 max-w-2xl mx-auto">
            {[
              { label: 'Internships', icon: '💼', desc: 'Short-term internship opportunities matching your branch.' },
              { label: 'Internship + PPO', icon: '⭐', desc: 'Internships that include a performance-based pre-placement offer.' },
              { label: 'Placements', icon: '🏢', desc: 'Full-time placement opportunities from recruiting companies.' },
            ].map(({ label, icon, desc }, i) => (
              <motion.div
                key={label}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: '-40px' }}
                transition={{ duration: 0.4, delay: i * 0.08 }}
                className="card p-5 hover:shadow-card-md hover:border-brand-100 transition-all duration-200"
              >
                <span className="text-2xl" aria-hidden="true">{icon}</span>
                <h3 className="mt-3 text-sm font-semibold text-slate-900">{label}</h3>
                <p className="mt-1 text-xs text-slate-500 leading-relaxed">{desc}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ─── SIGNUP FORM ──────────────────────────────────────────────── */}
      <section id="signup" className="py-16 sm:py-20 bg-slate-50 border-t border-slate-100">
        <div className="section-container">
          <div className="max-w-xl mx-auto">
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-60px' }}
              transition={{ duration: 0.5 }}
            >
              <div className="text-center mb-8">
                <h2 className="text-2xl sm:text-3xl font-bold text-slate-900 tracking-tight">
                  Subscribe for alerts
                </h2>
                <p className="mt-2 text-slate-600 text-sm">
                  Enter your VIT email to start receiving matching opportunities.
                </p>
              </div>

              {/* Already subscribed */}
              <p className="text-center text-sm text-slate-500 mb-6">
                Already subscribed?{' '}
                <button
                  type="button"
                  onClick={() => navigate('/preferences')}
                  className="text-brand-600 font-medium hover:text-brand-700 focus:outline-none focus-visible:underline"
                >
                  Manage your preferences →
                </button>
              </p>

              <div className="card p-6 sm:p-8">
                {/* Success state */}
                <AnimatePresence mode="wait">
                  {formState === 'success' ? (
                    <motion.div
                      key="success"
                      initial={{ opacity: 0, scale: 0.97 }}
                      animate={{ opacity: 1, scale: 1 }}
                      transition={{ duration: 0.3 }}
                      className="text-center py-6"
                    >
                      <motion.div
                        initial={{ scale: 0 }}
                        animate={{ scale: 1 }}
                        transition={{ type: 'spring', stiffness: 400, damping: 20, delay: 0.1 }}
                        className="flex h-16 w-16 items-center justify-center rounded-full bg-success-100 mx-auto mb-4"
                      >
                        <CheckCircle2 className="h-8 w-8 text-success-600" />
                      </motion.div>
                      <h3 className="text-lg font-semibold text-slate-900">Check your inbox</h3>
                      <p className="mt-2 text-sm text-slate-600 max-w-sm mx-auto">
                        {successMsg}
                      </p>
                      <p className="mt-4 text-xs text-slate-400">
                        Once verified, you'll receive email alerts when matching opportunities are detected.
                      </p>
                      <button
                        type="button"
                        onClick={() => {
                          setFormState('idle')
                          setEmail('')
                          setBranch('')
                          setErrors({})
                          setApiError('')
                        }}
                        className="mt-6 btn-secondary text-sm"
                      >
                        Subscribe another email
                      </button>
                    </motion.div>
                  ) : (
                    <motion.form
                      key="form"
                      ref={formRef}
                      onSubmit={handleSubmit}
                      noValidate
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      className="space-y-5"
                    >
                      {/* Global API error */}
                      {apiError && (
                        <motion.div
                          initial={{ opacity: 0, y: -4 }}
                          animate={{ opacity: 1, y: 0 }}
                          className="alert-error"
                          role="alert"
                          aria-live="assertive"
                        >
                          <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
                          <span>{apiError}</span>
                        </motion.div>
                      )}

                      {/* Email field */}
                      <div>
                        <label htmlFor="email" className="block text-sm font-medium text-slate-700 mb-1.5">
                          VIT email address
                        </label>
                        <input
                          id="email"
                          type="email"
                          autoComplete="email"
                          inputMode="email"
                          value={email}
                          onChange={e => {
                            setEmail(e.target.value)
                            if (errors.email) setErrors(err => ({ ...err, email: undefined }))
                            if (apiError) setApiError('')
                          }}
                          placeholder="firstname.lastname@vit.edu"
                          className={cn('input-base', errors.email && 'error')}
                          aria-invalid={!!errors.email}
                          aria-describedby={errors.email ? 'email-error' : undefined}
                          disabled={formState === 'loading'}
                        />
                        {errors.email && (
                          <motion.p
                            id="email-error"
                            initial={{ opacity: 0, y: -4 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="mt-1.5 text-xs text-danger-600 flex items-center gap-1"
                            role="alert"
                          >
                            <AlertCircle className="h-3 w-3" />
                            {errors.email}
                          </motion.p>
                        )}
                      </div>

                      {/* Batch badge (not a form field) */}
                      <div>
                        <p className="text-sm font-medium text-slate-700 mb-1.5">Graduation batch</p>
                        <div className="flex items-center gap-2 px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl">
                          <GraduationCap className="h-4 w-4 text-brand-500 flex-shrink-0" />
                          <span className="text-sm font-medium text-slate-700">Class of 2028</span>
                          <span className="ml-auto text-[11px] font-medium text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
                            Batch restricted
                          </span>
                        </div>
                      </div>

                      {/* Branch selector */}
                      <div>
                        <label htmlFor="branch-selector" className="block text-sm font-medium text-slate-700 mb-1.5">
                          Engineering programme
                        </label>
                        <BranchSelector
                          id="branch-selector"
                          value={branch}
                          onChange={val => {
                            setBranch(val)
                            if (errors.branch) setErrors(e => ({ ...e, branch: undefined }))
                          }}
                          error={errors.branch}
                        />
                        {errors.branch && (
                          <motion.p
                            initial={{ opacity: 0, y: -4 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="mt-1.5 text-xs text-danger-600 flex items-center gap-1"
                            role="alert"
                          >
                            <AlertCircle className="h-3 w-3" />
                            {errors.branch}
                          </motion.p>
                        )}
                      </div>

                      {/* Preference cards */}
                      <div>
                        <p className="text-sm font-medium text-slate-700 mb-2">
                          Opportunity alert preferences
                        </p>
                        <PreferenceCards values={prefs} onChange={prefChange} />
                        {errors.prefs && (
                          <motion.p
                            initial={{ opacity: 0, y: -4 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="mt-1.5 text-xs text-danger-600 flex items-center gap-1"
                            role="alert"
                          >
                            <AlertCircle className="h-3 w-3" />
                            {errors.prefs}
                          </motion.p>
                        )}
                      </div>

                      {/* Submit */}
                      <motion.button
                        type="submit"
                        disabled={formState === 'loading'}
                        whileTap={{ scale: 0.97 }}
                        transition={{ duration: 0.12 }}
                        className="btn-primary w-full text-sm"
                      >
                        {formState === 'loading' ? (
                          <>
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Registering...
                          </>
                        ) : (
                          <>
                            Subscribe for Alerts
                            <ArrowRight className="h-4 w-4" />
                          </>
                        )}
                      </motion.button>
                    </motion.form>
                  )}
                </AnimatePresence>
              </div>
            </motion.div>
          </div>
        </div>
      </section>

      {/* Trust / Privacy section */}
      <section className="py-14 sm:py-16">
        <div className="section-container">
          <div className="max-w-2xl mx-auto">
            <motion.div
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5 }}
              className="text-center mb-8"
            >
              <h2 className="text-xl font-bold text-slate-900">Privacy &amp; how your data is used</h2>
            </motion.div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {[
                {
                  icon: '🔒',
                  title: 'TPO credentials are never requested',
                  desc: 'This service never asks for your VIERP username, password, or session tokens. Students never provide TPO credentials.'
                },
                {
                  icon: '📡',
                  title: 'Server-side monitoring',
                  desc: 'The watcher monitors the TPO portal server-side using its own credentials. Your account is separate from TPO access.'
                },
                {
                  icon: '📧',
                  title: 'Email is used for alerts only',
                  desc: 'Your email is used to send opportunity alerts and account management links. It is not shared with third parties.'
                },
                {
                  icon: '✅',
                  title: 'Full control',
                  desc: 'You can update your preferences or unsubscribe at any time using the links in your alert emails.'
                },
              ].map(({ icon, title, desc }, i) => (
                <motion.div
                  key={title}
                  initial={{ opacity: 0, y: 12 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.4, delay: i * 0.07 }}
                  className="flex gap-3 p-4"
                >
                  <span className="text-xl flex-shrink-0 mt-0.5">{icon}</span>
                  <div>
                    <p className="text-sm font-semibold text-slate-800">{title}</p>
                    <p className="text-xs text-slate-500 mt-1 leading-relaxed">{desc}</p>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-slate-100 py-8">
        <div className="section-container flex flex-col sm:flex-row items-center justify-between gap-4 text-sm text-slate-500">
          <div className="flex items-center gap-2">
            <Bell className="h-4 w-4 text-brand-500" />
            <span className="font-medium text-slate-700">TPO Watcher</span>
            <span>— VIT Pune Class of 2028</span>
          </div>
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={() => navigate('/preferences')}
              className="hover:text-slate-900 transition-colors focus:outline-none focus-visible:underline"
            >
              Manage Preferences
            </button>
          </div>
        </div>
      </footer>
    </div>
  )
}
