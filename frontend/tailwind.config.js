/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // We keep 'ink' to not break existing pages while we transition,
        // but we redefine it to a light-mode friendly palette (inverted slate)
        // Actually, no, let's keep ink as it was so we don't break old pages instantly,
        // and we introduce 'surface', 'primary', 'magic' for the new layout.
        ink: {
          50: '#f8fafc',
          100: '#f1f5f9',
          200: '#e2e8f0',
          300: '#cbd5e1',
          400: '#94a3b8',
          500: '#64748b',
          600: '#475569',
          700: '#334155',
          800: '#1e293b',
          900: '#0f172a',
          950: '#020617',
        },
        surface: {
          50: '#F4EAC5',
          100: '#f1e4b5',
          200: '#ebd8a0',
          300: '#dfc172',
          400: '#d0a44b',
          500: '#b08137',
          600: '#8f6630',
          700: '#75522b',
          800: '#5c4022',
          900: '#422e18',
        },
        pine: {
          50: '#edf5f4',
          100: '#d1e5e1',
          200: '#aaccc4',
          300: '#7eb0a5',
          400: '#64a396',
          500: '#549688',
          600: '#3b7a6e',
          700: '#2d6257',
          800: '#244f46',
          900: '#193430',
          DEFAULT: '#549688',
        },
        tea: {
          50: '#F4EAC5',
          100: '#f1e4b5',
          200: '#ebd8a0',
          300: '#dfc172',
          400: '#d0a44b',
          500: '#b08137',
          600: '#8f6630',
          700: '#75522b',
          800: '#5c4022',
          900: '#422e18',
          DEFAULT: '#F4EAC5',
        },
        // Magic maps to Copper Green (铜绿) #549688
        magic: {
          50: '#edf5f4',
          100: '#d1e5e1',
          200: '#aaccc4',
          300: '#7eb0a5',
          400: '#549688',
          500: '#3b7a6e',
          600: '#2d6257',
          700: '#244f46',
          800: '#1e403a',
          900: '#193430',
        },
        amber: {
          300: '#fcd34d',
          400: '#fbbf24',
          500: '#f59e0b',
          600: '#d97706',
        },
        crimson: {
          400: '#f87171',
          500: '#ef4444',
        },
        jade: {
          400: '#4ade80',
          500: '#22c55e',
        },
        // 莫兰迪蓝（莫奈尔蓝）- 用于卡片流动渐变底色
        monet: {
          50: '#F2F5F9',
          100: '#E4EBF3',
          200: '#CAD7E6',
          300: '#A8BCD4',
          400: '#7E9BBE',
          500: '#5F7BA0',
          600: '#4D6485',
          700: '#3F526D',
          800: '#34445A',
          900: '#2A3748',
        },
      },
      fontFamily: {
        sans: ['Space Grotesk', '"LXGW WenKai"', 'ui-sans-serif', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        serif: ['"LXGW WenKai"', '"Source Han Serif SC"', 'Georgia', 'serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
        display: ['Space Grotesk', '"LXGW WenKai"', 'sans-serif'],
      },
      boxShadow: {
        'soft': '0 4px 20px -2px rgba(0, 0, 0, 0.05)',
        'elevated': '0 10px 40px -10px rgba(0, 0, 0, 0.08)',
        'glass-light': '0 8px 32px 0 rgba(0, 0, 0, 0.04)',
        'float': '0 20px 40px -15px rgba(0,0,0,0.05)',
      },
      animation: {
        'fade-in': 'fadeIn 0.3s ease-out forwards',
        'slide-up': 'slideUp 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards',
        'slide-in-right': 'slideInRight 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards',
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        // 莫兰迪蓝流动渐变 - 用于卡片底色
        'monet-flow': 'monetFlow 18s ease-in-out infinite',
        'monet-flow-slow': 'monetFlow 28s ease-in-out infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideInRight: {
          '0%': { opacity: '0', transform: 'translateX(24px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        // 多层径向渐变位置缓慢漂移，制造"颜色流动"感
        monetFlow: {
          '0%, 100%': {
            backgroundPosition: '0% 0%, 100% 100%, 50% 50%',
          },
          '33%': {
            backgroundPosition: '30% 20%, 70% 80%, 60% 40%',
          },
          '66%': {
            backgroundPosition: '70% 80%, 30% 20%, 40% 60%',
          },
        },
      }
    },
  },
  plugins: [],
}
