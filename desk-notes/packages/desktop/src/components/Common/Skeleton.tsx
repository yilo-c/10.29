import React from 'react'

interface SkeletonProps {
  className?: string
  style?: React.CSSProperties
}

/** 骨架屏占位块（P6）：微光 shimmer，替代裸 spinner，避免加载时布局跳动 */
const Skeleton: React.FC<SkeletonProps> = ({ className = '', style }) => (
  <div className={`skeleton ${className}`.trim()} style={style} aria-hidden />
)

export default Skeleton
