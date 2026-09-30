/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'Helvetica', 'Arial', 'sans-serif'],
      },
      colors: {
        // VIT TPO Watcher design system
        brand: {
          50:  '#eef2ff',
          100: '#e0e7ff',
          200: '#c7d2fe',
          300: '#a5b4fc',
          400: '#818cf8',
          500: '#6366f1',   // indigo-500 - primary actions
          600: '#4f46e5',   // indigo-600 - primary buttons
          700: '#4338ca',   // indigo-700 - hover
          800: '#3730a3',   // indigo-800 - deep
          900: '#312e81',   // indigo-900 - hero text
          950: '#1e1b4b',
        },
        success: {
          50:  '#ecfdf5',
          100: '#d1fae5',
          500: '#10b981',
          600: '#059669',
          700: '#047857',
        },
        warning: {
          50:  '#fffbeb',
          100: '#fef3c7',
          500: '#f59e0b',
          600: '#d97706',
          700: '#b45309',
        },
        danger: {
          50:  '#fef2f2',
          100: '#fee2e2',
          200: '#fecaca',
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
        shimmer: {
          '0%, 100%': { backgroundPosition: '0% 50%' },
          '50%':       { backgroundPosition: '100% 50%' },
        },
        'pulse-slow': {
          '0%, 100%': { opacity: '1' },
          '50%':       { opacity: '0.5' },
        },
      },
      animation: {
        'fade-up':     'fade-up 0.5s cubic-bezier(0.22,1,0.36,1) both',
        'fade-in':     'fade-in 0.4s ease both',
        'slide-down':  'slide-down 0.2s ease both',
        'pulse-slow':  'pulse-slow 3s ease-in-out infinite',
      },
      boxShadow: {
        'card':   '0 1px 3px rgba(15,23,42,0.06), 0 4px 16px rgba(15,23,42,0.04)',
        'card-md':'0 2px 8px rgba(15,23,42,0.08), 0 16px 32px rgba(15,23,42,0.06)',
        'focus':  '0 0 0 3px rgba(99,102,241,0.25)',
        'brand':  '0 4px 14px rgba(79,70,229,0.25)',
      },
      borderRadius: {
        'xl':  '0.75rem',
        '2xl': '1rem',
        '3xl': '1.5rem',
      },
    },
  },
  plugins: [
    // @tailwindcss/forms is loaded separately if available
  ],
}
