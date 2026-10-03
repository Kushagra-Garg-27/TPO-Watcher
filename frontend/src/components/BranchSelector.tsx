import { motion } from 'framer-motion'
import { ChevronDown, Check, Search } from 'lucide-react'
import { CANONICAL_BRANCHES, BRANCH_GROUPS } from '../lib/branches'
import { cn } from '../lib/utils'
import { useRef, useState, useEffect, useId } from 'react'

interface BranchSelectorProps {
  value: string
  onChange: (value: string) => void
  error?: string
  id?: string
}

export function BranchSelector({ value, onChange, error, id }: BranchSelectorProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const buttonId = useId()
  const listboxId = useId()

  const selectedBranch = CANONICAL_BRANCHES.find((b) => b.value === value)

  // Close on outside click
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!containerRef.current?.contains(e.target as Node)) {
        setOpen(false)
        setSearch('')
      }
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  // Focus search on open
  useEffect(() => {
    if (open) {
      setTimeout(() => searchRef.current?.focus(), 50)
    }
  }, [open])

  // Keyboard navigation on button
  function onButtonKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
    }
    if (e.key === 'Escape') setOpen(false)
  }

  function handleSelect(val: string) {
    onChange(val)
    setOpen(false)
    setSearch('')
  }

  // Filter branches by search
  const searchLower = search.trim().toLowerCase()
  const filteredGroups = BRANCH_GROUPS.map((group) => ({
    ...group,
    branches: group.branches.filter((bv) => {
      const branch = CANONICAL_BRANCHES.find((b) => b.value === bv)
      if (!branch) return false
      if (!searchLower) return true
      const matchLabel = branch.label.toLowerCase().includes(searchLower)
      const matchValue = branch.value.toLowerCase().includes(searchLower)
      const matchTerms = branch.searchTerms.some((term) =>
        term.toLowerCase().includes(searchLower)
      )
      return matchLabel || matchValue || matchTerms
    }),
  })).filter((group) => group.branches.length > 0)

  return (
    <div ref={containerRef} className="relative">
      {/* Trigger Button */}
      <button
        type="button"
        id={id || buttonId}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-label="Select your engineering branch"
        onClick={() => setOpen((prev) => !prev)}
        onKeyDown={onButtonKeyDown}
        className={cn(
          'input-base flex items-center justify-between text-left cursor-pointer transition-all duration-200',
          error && 'error',
          !selectedBranch && 'text-muted'
        )}
      >
        <span className={cn('truncate', selectedBranch ? 'text-foreground font-medium' : 'text-muted/60')}>
          {selectedBranch?.label ?? 'Select your engineering programme...'}
        </span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.15 }}>
          <ChevronDown className="h-4 w-4 shrink-0 text-muted" />
        </motion.span>
      </button>

      {/* Dropdown Menu */}
      {open && (
        <motion.div
          initial={{ opacity: 0, y: -6, scale: 0.99 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -6, scale: 0.99 }}
          transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
          id={listboxId}
          role="listbox"
          aria-label="Engineering branches"
          className="absolute left-0 right-0 z-50 mt-1.5 bg-surface-1 border border-line-strong rounded-xl shadow-[0_16px_36px_rgba(0,0,0,0.85)] overflow-hidden"
        >
          {/* Search Field */}
          <div className="p-2 border-b border-line bg-surface-2 flex items-center gap-2">
            <Search className="h-4 w-4 text-muted shrink-0 ml-1.5" />
            <input
              ref={searchRef}
              type="text"
              placeholder="Search branch code or keyword (e.g. CSE, IT, AI)..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full px-2 py-1.5 text-xs bg-transparent text-foreground placeholder:text-muted/60 focus:outline-none font-sans"
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  setOpen(false)
                  setSearch('')
                }
              }}
            />
          </div>

          {/* Options List */}
          <div className="max-h-72 overflow-y-auto py-1">
            {filteredGroups.length === 0 && (
              <p className="px-4 py-3 text-xs font-mono text-muted text-center">
                No matching canonical branches found.
              </p>
            )}
            {filteredGroups.map((group) => (
              <div key={group.label} className="border-b border-line/40 last:border-b-0">
                <div className="px-3 py-1.5 text-[11px] font-mono font-semibold text-muted uppercase tracking-wider bg-surface-2/40">
                  {group.label}
                </div>
                {group.branches.map((bv) => {
                  const branch = CANONICAL_BRANCHES.find((b) => b.value === bv)!
                  const isSelected = value === bv
                  return (
                    <button
                      key={bv}
                      role="option"
                      aria-selected={isSelected}
                      type="button"
                      onClick={() => handleSelect(bv)}
                      className={cn(
                        'w-full text-left px-4 py-2.5 text-xs transition-colors flex items-center justify-between gap-3',
                        isSelected
                          ? 'bg-accent/15 text-accent font-medium'
                          : 'text-foreground/80 hover:bg-surface-2 hover:text-foreground'
                      )}
                    >
                      <span className="leading-snug">{branch.label}</span>
                      {isSelected && <Check className="h-3.5 w-3.5 text-accent shrink-0" />}
                    </button>
                  )
                })}
              </div>
            ))}
          </div>
        </motion.div>
      )}
    </div>
  )
}
