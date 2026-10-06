import { useEffect, useState, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Menu, X, ArrowUpRight } from 'lucide-react'

interface NavbarProps {
  navigate: (path: string) => void
}

interface NavItem {
  label: string
  href: string
  isPage?: boolean
}

const NAV_ITEMS: NavItem[] = [
  { label: 'OVERVIEW', href: '#overview' },
  { label: 'HOW IT WORKS', href: '#how-it-works' },
  { label: 'LIVE DETECTIONS', href: '#detections' },
  { label: 'PREFERENCES', href: '/preferences', isPage: true },
]

export function Navbar({ navigate }: NavbarProps) {
  const [scrolled, setScrolled] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Lock body scroll when mobile menu is open
  useEffect(() => {
    if (menuOpen) {
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
    return () => {
      document.body.style.overflow = ''
    }
  }, [menuOpen])

  // Close mobile menu on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && menuOpen) {
        setMenuOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [menuOpen])

  // Close menu on resize to desktop
  useEffect(() => {
    const onResize = () => {
      if (window.innerWidth >= 1024) setMenuOpen(false)
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  function handleNav(item: NavItem) {
    setMenuOpen(false)
    if (item.isPage) {
      navigate(item.href)
    } else {
      if (window.location.pathname !== '/' && window.location.pathname !== '/signup') {
        navigate('/')
        setTimeout(() => {
          document.querySelector(item.href)?.scrollIntoView({ behavior: 'smooth' })
        }, 120)
      } else {
        const el = document.querySelector(item.href)
        el?.scrollIntoView({ behavior: 'smooth' })
      }
    }
  }

  function handleLogoClick(e: React.MouseEvent) {
    e.preventDefault()
    setMenuOpen(false)
    navigate('/')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <>
      <header
        className={`fixed top-0 inset-x-0 z-40 transition-all duration-300 ${
          scrolled
            ? 'nav-backdrop border-b border-line py-3 shadow-[0_4px_30px_rgba(0,0,0,0.8)]'
            : 'bg-transparent py-5'
        }`}
      >
        <div className="section-container flex items-center justify-between">
          {/* Logo / System Signature */}
          <a
            href="/"
            onClick={handleLogoClick}
            className="flex items-center gap-3 group focus:outline-none focus-visible:ring-1 focus-visible:ring-accent rounded-sm"
          >
            <span className="flex items-center justify-center w-7 h-7 bg-surface-2 border border-line-strong rounded text-accent font-display text-sm group-hover:border-accent transition-colors">
              ✦
            </span>
            <div className="flex flex-col">
              <span className="font-display text-lg tracking-wider text-foreground leading-none">
                TPO WATCHER
              </span>
              <span className="text-[10px] tracking-[0.2em] font-mono text-muted uppercase">
                VIT PUNE · 2028
              </span>
            </div>
          </a>

          {/* Center: Desktop Nav Links */}
          <nav
            className="hidden lg:flex items-center gap-8"
            aria-label="Primary navigation"
          >
            {NAV_ITEMS.map((item) => (
              <button
                key={item.label}
                type="button"
                onClick={() => handleNav(item)}
                className="relative text-[11px] font-mono tracking-[0.18em] uppercase text-muted hover:text-foreground transition-colors duration-200 group py-1"
              >
                {item.label}
                <span className="absolute bottom-0 left-0 w-0 h-[1.5px] bg-accent group-hover:w-full transition-all duration-200" />
              </button>
            ))}
          </nav>

          {/* Right: Live Status & CTA */}
          <div className="hidden lg:flex items-center gap-5">

            {/* Subscribe Action */}
            <button
              type="button"
              onClick={() => {
                if (window.location.pathname !== '/' && window.location.pathname !== '/signup') {
                  navigate('/')
                  setTimeout(() => {
                    document.querySelector('#signup')?.scrollIntoView({ behavior: 'smooth' })
                  }, 120)
                } else {
                  document.querySelector('#signup')?.scrollIntoView({ behavior: 'smooth' })
                }
              }}
              className="btn-primary text-xs py-2 px-4 tracking-widest"
            >
              SUBSCRIBE
              <ArrowUpRight className="h-3.5 w-3.5" />
            </button>
          </div>

          {/* Mobile Menu Toggle */}
          <div className="flex items-center gap-3 lg:hidden">

            <button
              type="button"
              className="p-2 text-foreground hover:text-accent transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-accent rounded"
              aria-label={menuOpen ? 'Close navigation menu' : 'Open navigation menu'}
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((prev) => !prev)}
            >
              {menuOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
            </button>
          </div>
        </div>
      </header>

      {/* Full-Screen Dark Cinematic Mobile Menu */}
      <AnimatePresence>
        {menuOpen && (
          <motion.div
            ref={menuRef}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
            className="fixed inset-0 z-30 bg-[#08080a] flex flex-col justify-between p-6 pt-24 pb-8 lg:hidden"
            role="dialog"
            aria-modal="true"
            aria-label="Mobile navigation menu"
          >
            {/* Background grid motif */}
            <div className="absolute inset-0 bg-dark-grid opacity-30 pointer-events-none" />

            {/* Nav Items */}
            <nav className="relative flex flex-col gap-5 my-auto" aria-label="Mobile links">
              {NAV_ITEMS.map((item, index) => (
                <motion.button
                  key={item.label}
                  type="button"
                  onClick={() => handleNav(item)}
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -10 }}
                  transition={{
                    duration: 0.35,
                    delay: index * 0.05,
                    ease: [0.16, 1, 0.3, 1],
                  }}
                  className="group flex items-center justify-between text-left py-2 border-b border-line"
                >
                  <span className="font-display text-3xl sm:text-4xl text-foreground uppercase tracking-wider group-hover:text-accent group-hover:translate-x-2 transition-all duration-200">
                    {item.label}
                  </span>
                  <ArrowUpRight className="h-5 w-5 text-muted group-hover:text-accent transition-colors" />
                </motion.button>
              ))}
            </nav>

            {/* Bottom Actions */}
            <div className="relative pt-6 border-t border-line flex flex-col gap-3">
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false)
                  if (window.location.pathname !== '/' && window.location.pathname !== '/signup') {
                    navigate('/')
                    setTimeout(() => {
                      document.querySelector('#signup')?.scrollIntoView({ behavior: 'smooth' })
                    }, 120)
                  } else {
                    document.querySelector('#signup')?.scrollIntoView({ behavior: 'smooth' })
                  }
                }}
                className="btn-primary w-full py-3.5 text-sm tracking-widest justify-center"
              >
                SUBSCRIBE FOR ALERTS
                <ArrowUpRight className="h-4 w-4" />
              </button>

              <div className="flex items-center justify-between text-[11px] font-mono text-muted pt-2">
                <span>VIT PUNE CLASS OF 2028</span>
                <span className="text-accent">● AUTONOMOUS ENGINE</span>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
