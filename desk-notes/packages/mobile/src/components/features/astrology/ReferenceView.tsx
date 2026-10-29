import { useState } from 'react'
import { useStore } from '@desk-notes/shared'
import { Card } from '../../ui'

export default function ReferenceView() {
  const userProfile = useStore(s => s.userProfile)
  const [expandedCard, setExpandedCard] = useState<string | null>('huangli')

  const hasProfile = !!userProfile?.birthDate

  const cards = [
    {
      id: 'huangli',
      title: '今日黄历',
      subtitle: hasProfile ? '' : '填写个人信息以获取参考',
      content: hasProfile ? (
        <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
          <div><span style={{ color: 'var(--tone-smooth)' }}>较顺利</span> 签约 · 出行 · 纳财</div>
          <div><span style={{ color: 'var(--tone-neutral)' }}>平日</span> 入学 · 嫁娶</div>
          <div><span style={{ color: 'var(--tone-caution)' }}>宜谨慎</span> 动土 · 安葬</div>
          <div style={{ color: 'var(--text-disabled)', marginTop: 4 }}>冲猴 · 煞南</div>
          <div style={{ fontSize: 10, color: 'var(--text-disabled)', marginTop: 8 }}>
            ℹ️ 依据《钦定协纪辨方书》，仅供决策参考
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-tertiary)' }}>
          在设置中填写出生信息以获取个性化参考
        </div>
      ),
    },
    {
      id: 'bazi',
      title: '八字参考',
      subtitle: hasProfile ? '甲辰 戊辰 癸亥 壬子' : '填写生日信息',
      content: hasProfile ? (
        <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
          <div>日主 癸水 · 身旺</div>
          <div>五行缺金</div>
          <div style={{ marginTop: 8, color: 'var(--text-tertiary)' }}>
            传统参考：今日日主偏旺，重要事项可多听取他人意见再定。若涉及金融、法律类事项，可适当增加确认环节。
          </div>
          <div style={{ fontSize: 10, color: 'var(--text-disabled)', marginTop: 8 }}>
            ℹ️ 依据传统子平八字理论，仅供决策参考
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-tertiary)' }}>
          在设置中填写出生信息以查看
        </div>
      ),
    },
    {
      id: 'qimen',
      title: '时辰参考',
      subtitle: '当前申时 (15:00-17:00)',
      content: (
        <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
          <div>可参考方位 <span style={{ color: 'var(--gold-primary)' }}>西南</span></div>
          <div style={{ marginTop: 4 }}>天辅星 · 杜门 · 巽四宫</div>
          <div style={{ marginTop: 8, color: 'var(--text-tertiary)' }}>
            此时宜静不宜动，适合处理文书、策划类事项。
          </div>
          <div style={{ fontSize: 10, color: 'var(--text-disabled)', marginTop: 8 }}>
            ℹ️ 依据传统时家奇门排盘，仅供决策参考
          </div>
        </div>
      ),
    },
    {
      id: 'compass',
      title: '方位罗盘',
      subtitle: '',
      content: (
        <div>
          <div
            style={{
              width: 120,
              height: 120,
              borderRadius: '50%',
              border: '1px solid var(--border-card)',
              margin: '0 auto 12px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              position: 'relative',
              fontSize: 'var(--text-tiny)',
              color: 'var(--text-tertiary)',
            }}
          >
            <span style={{ position: 'absolute', top: 8 }}>N</span>
            <span style={{ position: 'absolute', right: 8 }}>东</span>
            <span style={{ position: 'absolute', bottom: 8 }}>S</span>
            <span style={{ position: 'absolute', left: 8 }}>西</span>
            <span style={{ color: 'var(--gold-primary)', fontSize: 'var(--text-body)', fontWeight: 600 }}>↙</span>
          </div>
          <div style={{ textAlign: 'center', fontSize: 'var(--text-small)', color: 'var(--text-secondary)' }}>
            当前朝向 西南 · 与今日可参考方位一致
          </div>
          <div style={{ fontSize: 10, color: 'var(--text-disabled)', marginTop: 8, textAlign: 'center' }}>
            ℹ️ 罗盘依赖设备传感器，仅供方位参考
          </div>
        </div>
      ),
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div style={{ padding: 'var(--space-4) var(--space-4) 0', flexShrink: 0 }}>
        <div style={{ fontSize: 'var(--text-h1)', fontWeight: 600 }}>今日参考</div>
      </div>

      {/* Content */}
      <div style={{
        flex: 1, overflowY: 'auto', padding: 'var(--space-4)',
        display: 'flex', flexDirection: 'column', gap: 'var(--space-3)',
        WebkitOverflowScrolling: 'touch',
      }}>
        {/* TodaySummary */}
        <div style={{
          background: 'var(--bg-glass)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-md)',
          padding: '14px 16px',
          fontSize: 'var(--text-small)',
          color: 'var(--text-secondary)',
          lineHeight: 1.6,
        }}>
          今日较适合处理签约、整理、沟通类事项。重要决定仍建议以实际条件为准。
        </div>

        {/* Cards */}
        {cards.map(card => (
          <Card
            key={card.id}
            variant="elevated"
            title={card.title}
            subtitle={card.subtitle}
            collapsed={expandedCard !== card.id}
            onToggle={() => setExpandedCard(expandedCard === card.id ? null : card.id)}
          >
            {card.content}
          </Card>
        ))}

        {/* Footer */}
        <div style={{
          fontSize: 10, color: 'var(--text-disabled)',
          textAlign: 'center', padding: 'var(--space-4) 0',
        }}>
          以上信息依据传统历法理论整理，仅供做决策时参考。
        </div>
      </div>
    </div>
  )
}
