/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        display: ['Anton', 'sans-serif'],
        sans: ['Onest', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      colors: {
        // Marcus Vane cinematic foundation tokens
        background: '#08080a',
        foreground: '#f3f1ea',
        surface: {
          DEFAULT: '#111114',
          1: '#111114',
          2: '#17171b',
          3: '#202026',
        },
        muted: '#807f78',
        line: {
          DEFAULT: 'rgba(243,241,234,0.10)',
          strong: 'rgba(243,241,234,0.22)',
        },
        accent: {
          DEFAULT: '#ff3b1d',
          2: '#ff6a3d',
          muted: 'rgba(255, 59, 29, 0.15)',
        },
        // Restrained semantic status tokens
        brand: {
          50:  '#fff5f2',
          100: '#ffe6e0',
          200: '#ffccc2',
          300: '#ffa394',
          400: '#ff6a3d',
          500: '#ff3b1d',
          600: '#e52c0f',
          700: '#c22108',
          800: '#9e1e0a',
          900: '#7f1d0d',
          950: '#450b04',
        },
        success: {
          50:  '#0d2818',
          100: '#164426',
          500: '#22c55e',
          600: '#16a34a',
          700: '#15803d',
        },
        warning: {
          50:  '#2a1f07',
          100: '#48350d',
          500: '#f59e0b',
          600: '#d97706',
          700: '#b45309',
        },
        danger: {
          50:  '#2d1010',
          100: '#4a1717',
          200: '#731f1f',
          500: '#ef4444',
          600: '#dc2626',
          700: '#b91c1c',
        },
      },
      keyframes: {
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(16px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-in': {
          from: { opacity: '0' },
          to:   { opacity: '1' },
        },
        'slide-down': {
          from: { opacity: '0', transform: 'translateY(-8px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
        marquee: {
          '0%': { transform: 'translateX(0%)' },
          '100%': { transform: 'translateX(-50%)' },
        },
        'pulse-beacon': {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.4', transform: 'scale(1.15)' },
        },
      },
      animation: {
        'fade-up':     'fade-up 0.5s cubic-bezier(0.16,1,0.3,1) both',
        'fade-in':     'fade-in 0.4s ease both',
        'slide-down':  'slide-down 0.2s ease both',
        'marquee':     'marquee 22s linear infinite',
        'pulse-beacon':'pulse-beacon 2s cubic-bezier(0.4,0,0.6,1) infinite',
      },
      boxShadow: {
        'card':   '0 1px 2px rgba(0,0,0,0.4), 0 4px 16px rgba(0,0,0,0.6)',
        'card-md':'0 4px 12px rgba(0,0,0,0.6), 0 20px 40px rgba(0,0,0,0.8)',
        'glow-accent': '0 0 24px rgba(255,59,29,0.25)',
        'focus':  '0 0 0 2px rgba(255,59,29,0.6)',
      },
      borderRadius: {
        'sm':  '0.25rem',
        'md':  '0.5rem',
        'lg':  '0.75rem',
        'xl':  '1rem',
        '2xl': '1.25rem',
      },
    },
  },
  plugins: [],
}
