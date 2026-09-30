import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Menu, X, Bell } from 'lucide-react'
import { cn } from '../lib/utils'

interface NavbarProps {
  navigate: (path: string) => void
}

const navLinks = [
  { label: 'About', href: '#about' },
  { label: 'How it works', href: '#how-it-works' },
  { label: 'Preferences', href: '/preferences', external: false },
]

export function Navbar({ navigate }: NavbarProps) {
  const [scrolled, setScrolled] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 16)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Close menu on resize to desktop
  useEffect(() => {
    const onResize = () => { if (window.innerWidth >= 768) setMenuOpen(false) }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  function handleNavClick(e: React.MouseEvent, link: typeof navLinks[number]) {
    e.preventDefault()
    setMenuOpen(false)
    if (link.href.startsWith('#')) {
      const el = document.querySelector(link.href)
      el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    } else {
      navigate(link.href)
    }
  }

  function handleLogoClick(e: React.MouseEvent) {
    e.preventDefault()
    navigate('/')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <>
      <header
        className={cn(
          'fixed top-0 inset-x-0 z-40 transition-all duration-300',
          scrolled
            ? 'navbar-backdrop bg-white/90 border-b border-slate-100 shadow-sm'
            : 'bg-transparent'
        )}
      >
        <div className="section-container flex h-14 items-center justify-between">
          {/* Logo */}
          <a
            href="/"
            onClick={handleLogoClick}
            className="flex items-center gap-2.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-lg"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 shadow-brand text-white">
              <Bell className="h-4 w-4" strokeWidth={2.5} />
            </span>
            <span className="text-sm font-semibold text-slate-900 leading-tight">
              TPO Watcher
              <span className="block text-[10px] font-medium text-slate-500 leading-none">
                VIT Pune
              </span>
            </span>
          </a>

          {/* Desktop nav */}
          <nav className="hidden md:flex items-center gap-1" aria-label="Main navigation">
            {navLinks.map(link => (
              <a
                key={link.label}
                href={link.href}
                onClick={e => handleNavClick(e, link)}
                className="btn-ghost text-sm"
              >
                {link.label}
              </a>
            ))}
          </nav>

          {/* Desktop CTA */}
          <div className="hidden md:flex items-center gap-3">
            <a
              href="/#signup"
              onClick={e => {
                e.preventDefault()
                navigate('/')
                setTimeout(() => {
                  document.querySelector('#signup')?.scrollIntoView({ behavior: 'smooth' })
                }, 100)
              }}
              className="btn-primary text-sm px-4 py-2"
            >
              Get Alerts
            </a>
          </div>

          {/* Mobile menu button */}
          <button
            type="button"
            className="md:hidden btn-ghost p-2"
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(prev => !prev)}
          >
            <AnimatePresence mode="wait" initial={false}>
              {menuOpen
                ? <motion.span key="x" initial={{ rotate: -90, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }} exit={{ rotate: 90, opacity: 0 }} transition={{ duration: 0.15 }}>
                    <X className="h-5 w-5" />
                  </motion.span>
                : <motion.span key="menu" initial={{ rotate: 90, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }} exit={{ rotate: -90, opacity: 0 }} transition={{ duration: 0.15 }}>
                    <Menu className="h-5 w-5" />
                  </motion.span>
              }
            </AnimatePresence>
          </button>
        </div>
      </header>

      {/* Mobile menu drawer */}
      <AnimatePresence>
        {menuOpen && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
            className="fixed inset-x-0 top-14 z-30 navbar-backdrop bg-white/95 border-b border-slate-100 shadow-md md:hidden"
          >
            <nav className="section-container py-4 flex flex-col gap-1" aria-label="Mobile navigation">
              {navLinks.map(link => (
                <a
                  key={link.label}
                  href={link.href}
                  onClick={e => handleNavClick(e, link)}
                  className="flex items-center px-3 py-3 text-sm font-medium text-slate-700 hover:text-brand-600 hover:bg-brand-50 rounded-xl transition-colors"
                >
                  {link.label}
                </a>
              ))}
              <div className="pt-2 border-t border-slate-100 mt-1">
                <a
                  href="/#signup"
                  onClick={e => {
                    e.preventDefault()
                    setMenuOpen(false)
                    navigate('/')
                    setTimeout(() => {
                      document.querySelector('#signup')?.scrollIntoView({ behavior: 'smooth' })
                    }, 100)
                  }}
                  className="btn-primary w-full mt-2 text-sm"
                >
                  Get Alerts
                </a>
              </div>
            </nav>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
