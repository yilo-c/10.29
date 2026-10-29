export function SkeletonLoader() {
  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Title skeleton */}
      <div className="skeleton" style={{ width: '40%', height: 28 }} />
      {/* Calendar skeleton */}
      <div className="skeleton" style={{ width: '100%', height: 200, borderRadius: 'var(--radius-md)' }} />
      {/* Filter chips skeleton */}
      <div style={{ display: 'flex', gap: 8 }}>
        <div className="skeleton" style={{ width: 60, height: 32, borderRadius: 16 }} />
        <div className="skeleton" style={{ width: 60, height: 32, borderRadius: 16 }} />
        <div className="skeleton" style={{ width: 80, height: 32, borderRadius: 16 }} />
      </div>
      {/* Todo items skeleton */}
      {[1, 2, 3].map(i => (
        <div key={i} className="skeleton" style={{ width: '100%', height: 52, borderRadius: 'var(--radius-md)' }} />
      ))}
    </div>
  )
}
