import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function isValidEmail(email: string): boolean {
  return /^[\w.-]+@([\w.-]+\.)+[\w-]{2,}$/.test(email.trim())
}

export function isVitEmail(email: string): boolean {
  return email.trim().toLowerCase().endsWith('@vit.edu')
}
