import { useState } from 'react'
import { IconTodo, IconCompass, IconNote } from '../../ui'

interface OnboardingSlide {
  icon: React.ReactNode
  title: string
  subtitle: string
  description: string
}

const slides: OnboardingSlide[] = [
  {
    icon: <IconTodo size={48} />,
    title: '安排今日事务',
    subtitle: '看一眼今天的日子，安排今天的事情',
    description: '思忆便签将传统历法参考融入日常待办管理。新建待办时，直观看到今日宜忌与你的计划是否契合。',
  },
  {
    icon: <IconCompass size={48} />,
    title: '传统历法参考',
    subtitle: '黄历 · 八字 · 时辰 · 择日',
    description: '查看今日黄历宜忌、八字排盘、十二时辰参考。所有信息依据传统历法理论计算，仅供决策参考。',
  },
  {
    icon: <IconNote size={48} />,
    title: '随手记录灵感',
    subtitle: '笔记支持纯文本与 Markdown',
    description: '快速记录想法和灵感。支持文件夹分类管理，搜索查找，还能分享给好友。',
  },
  {
    icon: null,
    title: '数据在本地，隐私在手中',
    subtitle: '默认不上传任何数据',
    description: '你的所有待办、笔记和个人信息默认只存储在本地。云端同步作为可选的付费功能，数据将端到端加密。',
  },
]

interface OnboardingProps {
  onComplete: () => void
}

export default function Onboarding({ onComplete }: OnboardingProps) {
  const [step, setStep] = useState(0)
  const slide = slides[step]
  const isLast = step === slides.length - 1

  const handleNext = () => {
    if (isLast) {
      onComplete()
    } else {
      setStep(s => s + 1)
    }
  }

  const handleSkip = () => {
    onComplete()
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 200,
      background: 'var(--bg-primary)',
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Skip button */}
      <div style={{
        display: 'flex', justifyContent: 'flex-end',
        padding: '16px 20px',
        paddingTop: 'calc(16px + env(safe-area-inset-top, 0px))',
        flexShrink: 0,
      }}>
        <button
          onClick={handleSkip}
          style={{
            background: 'none', border: 'none',
            color: 'var(--text-tertiary)',
            fontSize: 'var(--text-footnote)',
            cursor: 'pointer', padding: '8px 4px',
          }}
        >
          跳过
        </button>
      </div>

      {/* Content area */}
      <div style={{
        flex: 1,
        display: 'flex', flexDirection: 'column',
        justifyContent: 'center',
        padding: '0 32px',
      }}>
        <div key={step} style={{
          animation: 'fadeSlideIn 0.3s var(--ease-out)',
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          textAlign: 'center',
        }}>
          {slide.icon && (
            <div style={{
              color: 'var(--accent)',
              marginBottom: 32,
              opacity: 0.9,
            }}>
              {slide.icon}
            </div>
          )}

          {!slide.icon && (
            <div style={{
              width: 56, height: 56, borderRadius: '50%',
              background: 'var(--accent-surface)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              marginBottom: 32,
            }}>
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
            </div>
          )}

          <div style={{
            fontSize: 'var(--text-title-1)',
            fontWeight: 700,
            color: 'var(--text-primary)',
            marginBottom: 8,
            letterSpacing: -0.5,
          }}>
            {slide.title}
          </div>

          <div style={{
            fontSize: 'var(--text-body)',
            color: 'var(--accent)',
            fontWeight: 500,
            marginBottom: 16,
          }}>
            {slide.subtitle}
          </div>

          <div style={{
            fontSize: 'var(--text-footnote)',
            color: 'var(--text-tertiary)',
            lineHeight: 1.7,
            maxWidth: 300,
          }}>
            {slide.description}
          </div>
        </div>
      </div>

      {/* Bottom: dots + button */}
      <div style={{
        flexShrink: 0,
        padding: '20px 24px',
        paddingBottom: 'calc(20px + env(safe-area-inset-bottom, 0px))',
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 20,
      }}>
        {/* Dots */}
        <div style={{ display: 'flex', gap: 8 }}>
          {slides.map((_, i) => (
            <div
              key={i}
              onClick={() => setStep(i)}
              style={{
                width: i === step ? 24 : 8,
                height: 8,
                borderRadius: 4,
                background: i === step ? 'var(--accent)' : 'var(--bg-tertiary)',
                transition: 'all 0.3s ease',
                cursor: 'pointer',
              }}
            />
          ))}
        </div>

        {/* Action button */}
        <button
          onClick={handleNext}
          style={{
            width: '100%', maxWidth: 280,
            padding: '14px 0',
            border: 'none', borderRadius: 'var(--radius-md)',
            background: 'var(--accent)',
            color: '#fff',
            fontSize: 'var(--text-body)',
            fontWeight: 600,
            cursor: 'pointer',
            transition: 'opacity 0.15s ease',
            WebkitTapHighlightColor: 'transparent',
          }}
        >
          {isLast ? '开始使用' : '继续'}
        </button>
      </div>
    </div>
  )
}
