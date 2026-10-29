import React from 'react'
import { useStore } from '../../store/useStore'

const CalendarViewLazy = React.lazy(() => import('../Todo/CalendarView'))

const CalendarModule: React.FC = () => {
  const setViewMode = useStore(s => s.setViewMode)

  // Switch back to list view when leaving this module
  React.useEffect(() => {
    setViewMode('calendar')
    return () => { setViewMode('list') }
  }, [setViewMode])

  return (
    <div className="flex-1 overflow-y-auto p-3">
      <React.Suspense fallback={<div className="h-8" />}>
        <CalendarViewLazy />
      </React.Suspense>
    </div>
  )
}

export default CalendarModule
