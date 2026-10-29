import React from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import { labelToDegrees, angleDiff } from '../../hooks/useCompass'

interface CompassWidgetProps {
  heading: number | null
  luckyDirection: string
  permission?: 'prompt' | 'granted' | 'denied' | 'unsupported'
  onRequestPermission?: () => void
}

/** Build direction markers from i18n labels */
function buildMarks(labels: readonly string[]) {
  return labels.map((label, i) => ({ label, angle: i * 45 }))
}

const LUCKY_RADIUS = 32 // degrees — half-width of the green arc

const CompassWidget: React.FC<CompassWidgetProps> = ({
  heading,
  luckyDirection,
  permission,
  onRequestPermission,
}) => {
  const { t, tArray } = useTranslation()
  const luckyDeg = labelToDegrees(luckyDirection)
  const diff = heading !== null ? angleDiff(heading, luckyDeg) : null

  const marks = buildMarks(tArray('compass.directions'))
  const needPermission = permission === 'prompt' || permission === 'denied'
  const isUnsupported = permission === 'unsupported'
  const showCompass = heading !== null && !needPermission && !isUnsupported

  return (
    <div className="flex flex-col items-center gap-2 py-1">
      {/* Permission required */}
      {needPermission && onRequestPermission && (
        <button
          onClick={onRequestPermission}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-2xs transition-all hover:brightness-110 active:scale-95"
          style={{ background: 'rgba(139,92,246,0.15)', color: '#c4b5fd', border: '1px solid rgba(139,92,246,0.2)' }}
        >
          <Icon icon="fa-solid fa-compass" />
          {permission === 'denied'
            ? t('compass.permissionDenied')
            : t('compass.enableCompass')
          }
        </button>
      )}

      {/* Permission denied — show text only */}
      {permission === 'denied' && !onRequestPermission && (
        <div className="text-[8px] text-center px-3 py-1 rounded-lg"
          style={{ background: 'rgba(239,68,68,0.06)', color: 'var(--text-muted)' }}
        >
          {t('compass.permissionDeniedHint')}
        </div>
      )}

      {/* Unsupported */}
      {isUnsupported && (
        <div className="text-[8px]" style={{ color: 'var(--text-muted)' }}>
          {t('compass.unsupported')}
        </div>
      )}

      {/* Compass */}
      {showCompass && (
        <>
          <div className="relative" style={{ width: 180, height: 180 }}>
            {/* Outer ring */}
            <svg viewBox="0 0 200 200" className="absolute inset-0 w-full h-full">
              {/* Background circle */}
              <circle cx="100" cy="100" r="95" fill="none"
                stroke="rgba(255,255,255,0.04)" strokeWidth="2" />

              {/* Lucky direction arc */}
              <path
                d={describeArc(100, 100, 88, luckyDeg - LUCKY_RADIUS, luckyDeg + LUCKY_RADIUS)}
                fill="none" stroke="rgba(74,222,128,0.5)" strokeWidth="6" strokeLinecap="round"
              />

              {/* Direction markers — rotated by -heading */}
              <g transform={`rotate(${-heading!}, 100, 100)`}>
                {marks.map(m => {
                  const rad = (m.angle - 90) * Math.PI / 180
                  const r = 78
                  const x = 100 + r * Math.cos(rad)
                  const y = 100 + r * Math.sin(rad)
                  return (
                    <text
                      key={m.label}
                      x={x} y={y}
                      textAnchor="middle" dominantBaseline="central"
                      fill={m.angle === luckyDeg ? '#4ade80' : 'rgba(255,255,255,0.5)'}
                      fontSize={m.label.length > 1 ? 8 : 10}
                      fontWeight={m.angle === luckyDeg ? '600' : '400'}
                      style={{ transition: 'fill 0.3s' }}
                    >
                      {m.label}
                    </text>
                  )
                })}
              </g>
            </svg>

            {/* Fixed red pointer at top */}
            <div className="absolute top-0 left-1/2 -translate-x-1/2 w-0 h-0 z-10"
              style={{
                borderLeft: '8px solid transparent',
                borderRight: '8px solid transparent',
                borderTop: '14px solid var(--danger)',
                filter: 'drop-shadow(0 1px 3px rgba(239,68,68,0.5))',
              }}
            />

            {/* Center dot */}
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full z-10"
              style={{ background: '#8b5cf6' }}
            />
          </div>

          {/* Heading text + angle diff */}
          <div className="flex flex-col items-center gap-0.5">
            <div className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
              {t('compass.currentHeading')}: {Math.round(heading!)}°
            </div>
            {diff !== null && (
              <div className="text-2xs" style={{ color: diff === 0 ? '#4ade80' : 'var(--text-muted)' }}>
                {diff === 0
                  ? `🎯 ${t('compass.facingLucky')}`
                  : `${t('compass.rotate')} ${Math.abs(diff)}° ${
                      diff > 0 ? t('compass.right') : t('compass.left')
                    }`
                }
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

/**
 * SVG arc path helper — draws a circular arc segment.
 * Used to highlight the auspicious direction sector.
 */
function describeArc(cx: number, cy: number, r: number, startDeg: number, endDeg: number): string {
  const startRad = ((startDeg - 90) * Math.PI) / 180
  const endRad = ((endDeg - 90) * Math.PI) / 180
  const x1 = cx + r * Math.cos(startRad)
  const y1 = cy + r * Math.sin(startRad)
  const x2 = cx + r * Math.cos(endRad)
  const y2 = cy + r * Math.sin(endRad)
  const largeArc = endDeg - startDeg > 180 ? 1 : 0
  return `M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}`
}

export default CompassWidget
