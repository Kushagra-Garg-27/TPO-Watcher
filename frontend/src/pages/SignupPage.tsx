import { useState, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  CheckCircle2, AlertCircle, Loader2,
  GraduationCap, ArrowRight, ShieldCheck, Mail
} from 'lucide-react'
import { HeroSection } from '../components/sections/HeroSection'
import { MonitoringPipeline } from '../components/sections/MonitoringPipeline'
import { RecentDetections } from '../components/sections/RecentDetections'
import { Marquee } from '../components/motion/Marquee'
import { FadeUp } from '../components/motion/FadeUp'
import { BranchSelector } from '../components/BranchSelector'
import { PreferenceCards } from '../components/PreferenceCards'
import { Footer } from '../components/Footer'
import { Preloader } from '../components/Preloader'
import { api } from '../lib/api'
import { cn, isValidEmail } from '../lib/utils'

interface SignupPageProps {
  navigate: (path: string) => void
}

type FormState = 'idle' | 'loading' | 'success' | 'error'

interface FormErrors {
  email?: string
  branch?: string
  prefs?: string
}

export function SignupPage({ navigate }: SignupPageProps) {
  const [showPreloader, setShowPreloader] = useState(() => {
    // Only show once per tab session to respect user flow
    return !sessionStorage.getItem('tpo_preloader_seen')
  })

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

  function handlePreloaderComplete() {
    setShowPreloader(false)
    sessionStorage.setItem('tpo_preloader_seen', 'true')
  }

  function validate(): boolean {
    const newErrors: FormErrors = {}
    if (!email.trim()) {
      newErrors.email = 'VIT email address is required.'
    } else if (!isValidEmail(email)) {
      newErrors.email = 'Enter a valid email address.'
    }
    if (!branch) newErrors.branch = 'Please select your engineering programme.'
    if (!prefs.pref_internship && !prefs.pref_placement && !prefs.pref_ppo) {
      newErrors.prefs = 'Select at least one opportunity category.'
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
    setPrefs((p) => ({ ...p, [id]: val }))
    if (errors.prefs) setErrors((e) => ({ ...e, prefs: undefined }))
  }

  function scrollToSignup() {
    document.querySelector('#signup')?.scrollIntoView({ behavior: 'smooth' })
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      {/* Cinematic One-Time Preloader */}
      {showPreloader && <Preloader onComplete={handlePreloaderComplete} />}

      {/* ── 01: HERO SECTION ────────────────────────────────────────── */}
      <HeroSection onSubscribeClick={scrollToSignup} />

      {/* ── 02: EDITORIAL MARQUEE ───────────────────────────────────── */}
      <Marquee speedSeconds={20} />

      {/* ── 03: THE PROBLEM & NARRATIVE (WHAT TPO-WATCHER DOES) ────── */}
      <section id="overview" className="py-24 sm:py-32 relative">
        <div className="section-container">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">
            <div className="lg:col-span-6">
              <FadeUp delay={0.1}>
                <span className="text-[11px] font-mono tracking-[0.25em] text-accent uppercase block mb-3">
                  00 // THE PROBLEM
                </span>
                <h2 className="font-display text-4xl sm:text-6xl uppercase text-foreground leading-[0.92] tracking-tight">
                  MANUAL PORTAL CHECKING
                  <br />
                  <span className="text-muted">IS BROKEN.</span>
                </h2>
                <div className="mt-6 space-y-4 text-sm sm:text-base text-muted font-light leading-relaxed font-sans">
                  <p>
                    Recruiting companies post drives on VIERP with short registration windows—frequently closing in 24 to 48 hours. Missing an announcement because you didn't refresh a dashboard costs you an entire interview round.
                  </p>
                  <p>
                    TPO-Watcher inverts the paradigm: instead of students constantly checking VIERP, an autonomous system watches on your behalf, matches your branch eligibility, and notifies your personal inbox with zero delay.
                  </p>
                </div>
              </FadeUp>
            </div>

            <div className="lg:col-span-6 grid grid-cols-1 sm:grid-cols-2 gap-4">
              <FadeUp delay={0.2}>
                <div className="card bg-surface-1 border border-line p-6 rounded-xl h-full flex flex-col justify-between">
                  <div>
                    <span className="text-xs font-mono text-danger-500 uppercase tracking-widest block mb-2 font-semibold">
                      [WITHOUT TPO WATCHER]
                    </span>
                    <h3 className="font-display text-xl text-foreground uppercase tracking-wide">
                      THE MANUAL STRUGGLE
                    </h3>
                    <ul className="mt-4 space-y-2.5 text-xs text-muted font-light">
                      <li className="flex items-start gap-2">
                        <span className="text-danger-500">✕</span> Checking VIERP 5 times a day
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="text-danger-500">✕</span> Missing tight 24-hr deadlines
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="text-danger-500">✕</span> Sorting through ineligible drives
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="text-danger-500">✕</span> WhatsApp noise & rumor panic
                      </li>
                    </ul>
                  </div>
                </div>
              </FadeUp>

              <FadeUp delay={0.3}>
                <div className="card bg-surface-2 border border-accent/40 p-6 rounded-xl h-full flex flex-col justify-between relative shadow-[0_0_24px_rgba(255,59,29,0.1)]">
                  <div>
                    <span className="text-xs font-mono text-accent uppercase tracking-widest block mb-2 font-semibold">
                      [WITH TPO WATCHER]
                    </span>
                    <h3 className="font-display text-xl text-foreground uppercase tracking-wide">
                      AUTONOMOUS INTELLIGENCE
                    </h3>
                    <ul className="mt-4 space-y-2.5 text-xs text-foreground/90 font-light">
                      <li className="flex items-start gap-2">
                        <span className="text-accent">✓</span> Automated scans at 10:00, 17:00, 00:00 IST
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="text-accent">✓</span> Immediate inbox alert on release
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="text-accent">✓</span> Filtered strictly to your branch
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="text-accent">✓</span> Zero VIERP passwords required
                      </li>
                    </ul>
                  </div>
                </div>
              </FadeUp>
            </div>
          </div>
        </div>
      </section>

      {/* ── 04: SUBSCRIPTION / REGISTRATION AREA ───────────────────── */}
      <section id="signup" className="py-24 sm:py-32 relative border-t border-line">
        <div className="section-container">
          <div className="max-w-2xl mx-auto">
            <FadeUp delay={0.1}>
              <div className="text-center mb-12">
                <span className="text-[11px] font-mono tracking-[0.25em] text-accent uppercase block mb-3 font-semibold">
                  SUBSCRIBE FOR ALERTS
                </span>
                <h2 className="font-display text-4xl sm:text-6xl uppercase text-foreground leading-[0.92] tracking-tight">
                  ACTIVATE YOUR
                  <br />
                  <span className="text-muted">INTELLIGENCE FEED</span>
                </h2>
                <p className="mt-4 text-sm text-muted font-light max-w-md mx-auto font-sans">
                  Register your VIT email to receive verified placement notifications matching your engineering discipline.
                </p>

                {/* Preferences Portal Link */}
                <div className="mt-4 text-xs font-mono text-muted">
                  Already subscribed?{' '}
                  <button
                    type="button"
                    onClick={() => navigate('/preferences')}
                    className="text-accent hover:underline font-semibold"
                  >
                    Manage your preferences →
                  </button>
                </div>
              </div>
            </FadeUp>

            {/* The Form Card */}
            <FadeUp delay={0.2} duration={0.6}>
              <div className="card bg-surface-1 border border-line-strong p-6 sm:p-10 relative overflow-hidden shadow-[0_24px_60px_rgba(0,0,0,0.8)]">
                <AnimatePresence mode="wait">
                  {formState === 'success' ? (
                    <motion.div
                      key="success"
                      initial={{ opacity: 0, scale: 0.96 }}
                      animate={{ opacity: 1, scale: 1 }}
                      transition={{ duration: 0.3 }}
                      className="text-center py-8"
                    >
                      <div className="w-16 h-16 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center mx-auto mb-5 text-emerald-400">
                        <CheckCircle2 className="h-8 w-8" />
                      </div>
                      <h3 className="font-display text-2xl sm:text-3xl text-foreground uppercase tracking-wide">
                        CHECK YOUR INBOX
                      </h3>
                      <p className="mt-3 text-sm text-muted max-w-md mx-auto font-sans font-light leading-relaxed">
                        {successMsg}
                      </p>
                      <div className="mt-6 p-4 bg-surface-2 rounded-lg border border-line text-xs font-mono text-muted text-left">
                        <div className="flex items-center gap-2 text-foreground font-semibold mb-1">
                          <Mail className="h-3.5 w-3.5 text-accent" />
                          <span>VERIFICATION LINK DISPATCHED</span>
                        </div>
                        Click the single-use link in your inbox within 24 hours to activate notifications. Once verified, your alerts begin immediately.
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          setFormState('idle')
                          setEmail('')
                          setBranch('')
                          setErrors({})
                          setApiError('')
                        }}
                        className="mt-8 btn-secondary text-xs tracking-widest"
                      >
                        SUBSCRIBE ANOTHER EMAIL
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
                      className="space-y-6"
                    >
                      {/* Global API Error */}
                      {apiError && (
                        <div className="alert-error" role="alert" aria-live="assertive">
                          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5 text-danger-500" />
                          <span>{apiError}</span>
                        </div>
                      )}

                      {/* Email Field */}
                      <div>
                        <label
                          htmlFor="email"
                          className="block text-xs font-mono uppercase tracking-wider text-muted mb-2"
                        >
                          VIT EMAIL ADDRESS <span className="text-accent">*</span>
                        </label>
                        <input
                          id="email"
                          type="email"
                          autoComplete="email"
                          inputMode="email"
                          value={email}
                          onChange={(e) => {
                            setEmail(e.target.value)
                            if (errors.email) setErrors((err) => ({ ...err, email: undefined }))
                            if (apiError) setApiError('')
                          }}
                          placeholder="firstname.lastname@vit.edu"
                          className={cn('input-base font-sans', errors.email && 'error')}
                          aria-invalid={!!errors.email}
                          aria-describedby={errors.email ? 'email-error' : undefined}
                          disabled={formState === 'loading'}
                        />
                        {errors.email && (
                          <p id="email-error" className="mt-1.5 text-xs text-danger-500 flex items-center gap-1 font-mono">
                            <AlertCircle className="h-3 w-3 shrink-0" />
                            {errors.email}
                          </p>
                        )}
                      </div>

                      {/* Batch Constraint Indicator */}
                      <div>
                        <span className="block text-xs font-mono uppercase tracking-wider text-muted mb-2">
                          GRADUATION COHORT
                        </span>
                        <div className="flex items-center justify-between px-4 py-3 bg-surface-2 border border-line rounded-lg text-xs font-mono">
                          <div className="flex items-center gap-2 text-foreground font-medium">
                            <GraduationCap className="h-4 w-4 text-accent" />
                            <span>VIT PUNE CLASS OF 2028</span>
                          </div>
                          <span className="text-[10px] uppercase tracking-wider text-accent font-semibold px-2 py-0.5 bg-accent/10 border border-accent/20 rounded">
                            COHORT RESTRICTED
                          </span>
                        </div>
                      </div>

                      {/* Canonical Branch Selection */}
                      <div>
                        <label
                          htmlFor="branch-selector"
                          className="block text-xs font-mono uppercase tracking-wider text-muted mb-2"
                        >
                          ENGINEERING PROGRAMME <span className="text-accent">*</span>
                        </label>
                        <BranchSelector
                          id="branch-selector"
                          value={branch}
                          onChange={(val) => {
                            setBranch(val)
                            if (errors.branch) setErrors((e) => ({ ...e, branch: undefined }))
                          }}
                          error={errors.branch}
                        />
                        {errors.branch && (
                          <p className="mt-1.5 text-xs text-danger-500 flex items-center gap-1 font-mono">
                            <AlertCircle className="h-3 w-3 shrink-0" />
                            {errors.branch}
                          </p>
                        )}
                      </div>

                      {/* Preference Cards */}
                      <div>
                        <span className="block text-xs font-mono uppercase tracking-wider text-muted mb-2">
                          OPPORTUNITY CATEGORIES
                        </span>
                        <PreferenceCards values={prefs} onChange={prefChange} />
                        {errors.prefs && (
                          <p className="mt-1.5 text-xs text-danger-500 flex items-center gap-1 font-mono">
                            <AlertCircle className="h-3 w-3 shrink-0" />
                            {errors.prefs}
                          </p>
                        )}
                      </div>

                      {/* Submit Button */}
                      <button
                        type="submit"
                        disabled={formState === 'loading'}
                        className="btn-primary w-full py-4 text-sm tracking-widest justify-center mt-4"
                      >
                        {formState === 'loading' ? (
                          <>
                            <Loader2 className="h-4 w-4 animate-spin" />
                            REGISTERING SUBSCRIBER...
                          </>
                        ) : (
                          <>
                            SUBSCRIBE FOR TPO ALERTS
                            <ArrowRight className="h-4 w-4" />
                          </>
                        )}
                      </button>

                      {/* Security Reassurance */}
                      <div className="pt-2 text-center text-[11px] font-mono text-muted flex items-center justify-center gap-1.5">
                        <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
                        <span>TPO credentials are never requested. Unsubscribe anytime.</span>
                      </div>
                    </motion.form>
                  )}
                </AnimatePresence>
              </div>
            </FadeUp>
          </div>
        </div>
      </section>

      {/* ── 05: HOW IT WORKS / WATCHER ENGINE ───────────────────────── */}
      <MonitoringPipeline />

      {/* ── 06: VERIFIED OPPORTUNITY ARCHIVE ────────────────────────── */}
      <RecentDetections />

      {/* ── 07: CINEMATIC FOOTER ────────────────────────────────────── */}
      <Footer navigate={navigate} />
    </div>
  )
}
