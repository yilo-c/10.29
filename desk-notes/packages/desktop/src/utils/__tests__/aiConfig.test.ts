import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { getLocalAIConfig } from '../aiConfig'

// ── P2-9 regression: no hardcoded Ollama defaults in component sources ───
//
// Components used to inline `apiUrl: 'http://localhost:11434', model:
// 'qwen2.5-7b:latest'` everywhere. They now must go through
// `getLocalAIConfig()` (or the shared constants) so the defaults stay in
// one place. This test scans component source files for the bare literals.

const HARDCODED_URL = 'http://localhost:11434'
const HARDCODED_MODEL = 'qwen2.5-7b:latest'

const COMPONENTS_ROOT = resolve(__dirname, '..', '..', 'components')
const APP_TSX = resolve(__dirname, '..', '..', 'App.tsx')
const FLOATING_ROOT = resolve(__dirname, '..', '..', 'FloatingBubble')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === '__tests__' || name === 'node_modules') continue
    const full = join(dir, name)
    const st = statSync(full)
    if (st.isDirectory()) walk(full, out)
    else if (st.isFile() && (full.endsWith('.tsx') || full.endsWith('.ts'))) out.push(full)
  }
  return out
}

function collectComponentSources(): string[] {
  const files: string[] = walk(COMPONENTS_ROOT)
  files.push(APP_TSX)
  try {
    files.push(...walk(FLOATING_ROOT))
  } catch {
    /* FloatingBubble may not exist in all builds */
  }
  return files
}

function hasInlineAIConfigLiteral(source: string): boolean {
  // Match `apiUrl: 'http://localhost:11434'` (with single or double quotes)
  // so we don't false-flag the substring in unrelated comments / strings.
  const urlPattern = /apiUrl\s*:\s*['"]http:\/\/localhost:11434['"]/
  const modelPattern = /model\s*:\s*['"]qwen2\.5-7b:latest['"]/
  return urlPattern.test(source) || modelPattern.test(source)
}

describe('P2-9: AI defaults are centralized', () => {
  it('exposes a getLocalAIConfig() helper returning the shared defaults', () => {
    const cfg = getLocalAIConfig()
    expect(cfg.apiUrl).toBe(HARDCODED_URL)
    expect(cfg.model).toBe(HARDCODED_MODEL)
    expect(cfg.protocol).toBe('ollama')
    expect(cfg.enabled).toBe(true)
    expect(cfg.autoTagging).toBe(false)
  })

  it('honours partial overrides while keeping the defaults', () => {
    const cfg = getLocalAIConfig({ enabled: false, autoTagging: true })
    expect(cfg.enabled).toBe(false)
    expect(cfg.autoTagging).toBe(true)
    expect(cfg.apiUrl).toBe(HARDCODED_URL)
    expect(cfg.model).toBe(HARDCODED_MODEL)
  })

  it('no component source inlines the Ollama URL / model literally', () => {
    const offenders: string[] = []
    for (const file of collectComponentSources()) {
      const src = readFileSync(file, 'utf8')
      if (hasInlineAIConfigLiteral(src)) offenders.push(file)
    }
    expect(offenders, `Hardcoded AI config literals found in:\n${offenders.join('\n')}`).toEqual([])
  })
})
