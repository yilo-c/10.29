import React from 'react'

interface ThemeScopeProps {
  theme?: 'dark' | 'light' | null
  backgroundMode?: 'acrylic' | 'pure-white' | 'pure-black'
  children: React.ReactNode
}

/**
 * Scopes theme and background CSS variables to a subtree.
 *
 * Wraps children in a `<div>` with `data-theme` and `data-background` attributes.
 * Because CSS selectors like `[data-theme="light"]` match ANY element (not just
 * `<html>`), this isolates appearance to the wrapped subtree — changes on
 * `document.documentElement` won't affect children, and vice versa.
 */
const ThemeScope: React.FC<ThemeScopeProps> = ({ theme, backgroundMode, children }) => {
  // Don't render a wrapper div at all when there's nothing to scope —
  // keeps the DOM flat for the common case.
  if (!theme && !backgroundMode) {
    return <>{children}</>
  }

  const bgAttr = !backgroundMode || backgroundMode === 'acrylic' ? undefined : backgroundMode

  return (
    <div data-theme={theme || undefined} data-background={bgAttr}>
      {children}
    </div>
  )
}

export default ThemeScope
