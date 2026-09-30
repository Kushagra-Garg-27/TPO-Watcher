import { motion } from 'framer-motion'
import { ChevronDown } from 'lucide-react'
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

  const selectedBranch = CANONICAL_BRANCHES.find(b => b.value === value)

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

  // Keyboard nav on button
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

  // Filter branches by search (checks label, value, and searchTerms)
  const searchLower = search.trim().toLowerCase()
  const filteredGroups = BRANCH_GROUPS.map(group => ({
    ...group,
    branches: group.branches.filter(bv => {
      const branch = CANONICAL_BRANCHES.find(b => b.value === bv)
      if (!branch) return false
      if (!searchLower) return true
      const matchLabel = branch.label.toLowerCase().includes(searchLower)
      const matchValue = branch.value.toLowerCase().includes(searchLower)
      const matchTerms = branch.searchTerms.some(term => term.toLowerCase().includes(searchLower))
      return matchLabel || matchValue || matchTerms
    })
  })).filter(group => group.branches.length > 0)

  return (
    <div ref={containerRef} className="relative">
      {/* Trigger button */}
      <button
        type="button"
        id={id || buttonId}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-label="Select your engineering branch"
        onClick={() => setOpen(prev => !prev)}
        onKeyDown={onButtonKeyDown}
        className={cn(
          'input-base flex items-center justify-between text-left cursor-pointer',
          error && 'error',
          !selectedBranch && 'text-slate-400',
        )}
      >
        <span className={cn('truncate', selectedBranch ? 'text-slate-900' : 'text-slate-400')}>
          {selectedBranch?.label ?? 'Select your branch...'}
        </span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.15 }}>
          <ChevronDown className="h-4 w-4 flex-shrink-0 text-slate-400" />
        </motion.span>
      </button>

      {/* Dropdown */}
      {open && (
        <motion.div
          initial={{ opacity: 0, y: -6, scale: 0.99 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -6, scale: 0.99 }}
          transition={{ duration: 0.15, ease: [0.22, 1, 0.36, 1] }}
          id={listboxId}
          role="listbox"
          aria-label="Engineering branches"
          className={cn(
            'absolute left-0 right-0 z-50 mt-1.5',
            'bg-white border border-slate-200 rounded-xl shadow-card-md',
            'overflow-hidden',
          )}
        >
          {/* Search */}
          <div className="p-2 border-b border-slate-100">
            <input
              ref={searchRef}
              type="text"
              placeholder="Search branch..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-slate-50 border border-slate-200 rounded-lg placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-brand-500/30 focus:border-brand-500"
              onKeyDown={e => {
                if (e.key === 'Escape') { setOpen(false); setSearch('') }
              }}
            />
          </div>

          {/* Options */}
          <div className="max-h-72 overflow-y-auto py-1">
            {filteredGroups.length === 0 && (
              <p className="px-4 py-3 text-sm text-slate-500 text-center">No matching branches</p>
            )}
            {filteredGroups.map(group => (
              <div key={group.label}>
                <div className="px-3 py-1.5 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
                  {group.label}
                </div>
                {group.branches.map(bv => {
                  const branch = CANONICAL_BRANCHES.find(b => b.value === bv)!
                  const isSelected = value === bv
                  return (
                    <button
                      key={bv}
                      role="option"
                      aria-selected={isSelected}
                      type="button"
                      onClick={() => handleSelect(bv)}
                      className={cn(
                        'w-full text-left px-4 py-2.5 text-sm transition-colors',
                        'flex items-center justify-between gap-2',
                        isSelected
                          ? 'bg-brand-50 text-brand-700 font-medium'
                          : 'text-slate-700 hover:bg-slate-50 hover:text-slate-900'
                      )}
                    >
                      <span>{branch.label}</span>
                      {isSelected && (
                        <motion.svg
                          initial={{ scale: 0 }}
                          animate={{ scale: 1 }}
                          className="h-4 w-4 text-brand-600 flex-shrink-0"
                          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}
                        >
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </motion.svg>
                      )}
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
