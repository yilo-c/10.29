/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // ── 语义色（直接引用 CSS 变量，主题感知）──
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        'surface-elevated': 'var(--surface-elevated)',
        border: 'var(--border)',
        'border-input': 'var(--border-input)',
        brand: {
          DEFAULT: 'var(--brand)',
          soft: 'var(--brand-soft)',
        },
        success: {
          DEFAULT: 'var(--success)',
          soft: 'var(--success-soft)',
        },
        warning: {
          DEFAULT: 'var(--warning)',
          soft: 'var(--warning-soft)',
        },
        danger: {
          DEFAULT: 'var(--danger)',
          soft: 'var(--danger-soft)',
        },
        // 文字
        'text-primary': 'var(--text-primary)',
        'text-secondary': 'var(--text-secondary)',
        'text-muted': 'var(--text-muted)',
        'text-dim': 'var(--text-dim)',
        // 交互
        'input-bg': 'var(--input-bg)',
        'hover-bg': 'var(--hover-bg)',
        // 向后兼容别名
        accent: {
          DEFAULT: 'var(--brand)',
          soft: 'var(--brand-soft)',
        },
        warn: {
          DEFAULT: 'var(--warning)',
          soft: 'var(--warning-soft)',
        },
        fluent: {
          blue: 'var(--brand)',
          red: 'var(--danger)',
          green: 'var(--success)',
          yellow: 'var(--warning)',
          purple: 'var(--brand)',
          orange: 'var(--warning)',
          pink: 'var(--danger)',
        },
      },
      fontFamily: {
        sans: ['"Segoe UI"', '"Noto Sans SC"', '"PingFang SC"', '"Microsoft YaHei"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"SF Mono"', '"Cascadia Code"', 'Consolas', 'monospace'],
      },
      // 间距 — 严格 4px 网格（spec §1.4 禁止 7/9/15px）
      spacing: {
        '0': '0',
        '1': '4px',
        '2': '8px',
        '3': '12px',
        '4': '16px',
        '5': '20px',
        '6': '24px',
        '8': '32px',
        '10': '40px',
        '12': '48px',
        '14': '56px',
        '16': '64px',
      },
      // 圆角 scale（spec §1.3；P2-24 收敛到 index.css 的 --radius-* 变量，主题感知）
      borderRadius: {
        'none': '0',
        'sm': 'var(--radius-sm)',        // 8px
        'DEFAULT': 'var(--radius)',      // 14px
        'md': 'var(--radius)',           // 14px
        'card': 'var(--radius)',         // 14px — 取代任意值 rounded-[14px]
        'lg': 'var(--radius-lg)',        // 20px
        'xl': 'var(--radius-xl)',        // 24px
        '2xl': '28px',                   // 无对应 CSS 变量，保留字面值
        'full': '9999px',
      },
      // 字号 scale（spec §1.5: 10/11/12/13/14/16/18/22/24/28/32）
      fontSize: {
        '2xs': ['10px', { lineHeight: '1.3' }],
        'xs':  ['11px', { lineHeight: '1.4' }],
        'sm':  ['12px', { lineHeight: '1.4' }],
        'base':['13px', { lineHeight: '1.5' }],
        'md':  ['14px', { lineHeight: '1.5' }],
        'lg':  ['16px', { lineHeight: '1.5' }],
        'xl':  ['18px', { lineHeight: '1.4' }],
        '2xl': ['22px', { lineHeight: '1.3' }],
        '3xl': ['32px', { lineHeight: '1.2' }],
        // display 档 — 页面级大标题 / hero 区（P1）
        'display': ['24px', { lineHeight: '1.25' }],
        'display-lg': ['28px', { lineHeight: '1.2' }],
      },
      boxShadow: {
        'sm': 'var(--shadow-sm)',
        'DEFAULT': 'var(--shadow)',
        'md': 'var(--shadow)',
        'lg': 'var(--shadow-lg)',
      },
      backdropBlur: { acrylic: '20px' },
      transitionDuration: {
        'fast': '150ms',
        'base': '200ms',
        'slow': '300ms',
      },
    },
  },
  plugins: [],
}
