import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// ── P2-12 batch 1 regression: no hardcoded Chinese in migrated components ─
//
// CapabilityApproval.tsx and AIActivityPanel.tsx used to inline ~25 Chinese
// strings between them. They have now been migrated to use `t()` from the
// shared i18n module under the `capability.*` and `aiUsage.*` namespaces.
// This test locks them down by failing if any CJK character reappears in
// either source outside of allowed locations (comments, t('...') keys).

const CJK = /[\u4e00-\u9fff]/
const COMPONENTS_ROOT = resolve(__dirname, '..', '..', '..', 'components')
const BATCH_1_FILES = [
  resolve(COMPONENTS_ROOT, 'Common', 'CapabilityApproval.tsx'),
  resolve(COMPONENTS_ROOT, 'Common', 'AIActivityPanel.tsx'),
  resolve(COMPONENTS_ROOT, 'Todo', 'MorningBrief.tsx'),
  resolve(COMPONENTS_ROOT, 'Common', 'ApiKeyManager.tsx'),
  resolve(COMPONENTS_ROOT, 'Common', 'CloudSyncPanel.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'SettingsModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'FlashcardModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Todo', 'QimenPanel.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'AIManagementModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'KnowledgeGraphModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Wordbank', 'WordbankModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'TranslationModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'TransferView.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'InfoModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'StatisticsModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'DashboardCardContents.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'InboxModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'InfoFeedView.tsx'),
  resolve(COMPONENTS_ROOT, 'Wordbank', 'WordEditForm.tsx'),
  resolve(COMPONENTS_ROOT, 'Wordbank', 'WordReview.tsx'),
  resolve(COMPONENTS_ROOT, 'Wordbank', 'WordListPanel.tsx'),
  resolve(COMPONENTS_ROOT, 'Wordbank', 'WordDictation.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'WordDetailPanel.tsx'),
  resolve(COMPONENTS_ROOT, 'Common', 'NoteEditor.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'SystemHealthPanel.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'InfoSectionConfig.tsx'),
  resolve(COMPONENTS_ROOT, 'FloatingNote', 'AIChatPanel.tsx'),
  resolve(COMPONENTS_ROOT, 'Todo', 'TodoList.tsx'),
  resolve(COMPONENTS_ROOT, 'Search', 'QuickSearchModal.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'InfoDetailModal.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'AgentMonitorModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'NotesModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'BacklinksModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Common', 'AISettingsPanel.tsx'),
  resolve(COMPONENTS_ROOT, 'Common', 'BackupRestorePanel.tsx'),
  resolve(COMPONENTS_ROOT, 'Todo', 'QimenQuickDialog.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'DashboardSettings.tsx'),
  resolve(COMPONENTS_ROOT, 'Proactive', 'SuggestionPanel.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'TodoModule.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'DashboardWidgets.tsx'),
  resolve(COMPONENTS_ROOT, 'FloatingNote', 'AIToolbar.tsx'),
  resolve(COMPONENTS_ROOT, 'MainWindow', 'StatusBar.tsx'),
  resolve(COMPONENTS_ROOT, 'Common', 'TrashView.tsx'),
  resolve(COMPONENTS_ROOT, 'Todo', 'HuangliPanel.tsx'),
  resolve(COMPONENTS_ROOT, 'Common', 'DailyNoteView.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'ModelSelector.tsx'),
  resolve(COMPONENTS_ROOT, 'Common', 'PermissionGate.tsx'),
  resolve(COMPONENTS_ROOT, 'FloatingNote', 'SiyiFileImage.tsx'),
  resolve(COMPONENTS_ROOT, 'PWA', 'PWAProvider.tsx'),
  resolve(COMPONENTS_ROOT, 'Todo', 'WeekView.tsx'),
  resolve(COMPONENTS_ROOT, 'FloatingNote', 'CanvasMode.tsx'),
  resolve(COMPONENTS_ROOT, 'Common', 'SpeakButton.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'ChainProgress.tsx'),
  resolve(COMPONENTS_ROOT, 'Todo', 'CompassWidget.tsx'),
  resolve(COMPONENTS_ROOT, 'Todo', 'GuidanceDashboard.tsx'),
  resolve(COMPONENTS_ROOT, 'Todo', 'CalendarView.tsx'),
  resolve(COMPONENTS_ROOT, 'Modules', 'RelatedMemories.tsx'),
  resolve(COMPONENTS_ROOT, 'Common', 'ErrorBoundary.tsx'),
]

function stripAllowed(line: string): string {
  // Drop t('...') / t("...") / tArray('...') argument literals — they are
  // i18n keys, never user-visible Chinese themselves.
  let out = line.replace(/\bt(?:Array)?\(\s*['"][^'"]*['"][^)]*\)/g, '')
  // Drop JSX block comments {/* ... */} — they live in source only.
  out = out.replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '')
  // Drop JS block comments /* ... */ inside a line.
  out = out.replace(/\/\*[\s\S]*?\*\//g, '')
  // Drop // line comments (keep block comments for simplicity — they rarely
  // contain CJK in source).
  const commentIdx = out.indexOf('//')
  if (commentIdx >= 0) out = out.slice(0, commentIdx)
  return out
}

function findHardcodedCjk(file: string): string[] {
  const src = readFileSync(file, 'utf8')
  const offenders: string[] = []
  src.split('\n').forEach((line: string, i: number) => {
    const stripped = line.trimStart()
    // Skip pure comment lines.
    if (stripped.startsWith('//') || stripped.startsWith('*') || stripped.startsWith('/*')) return
    if (!CJK.test(line)) return
    const masked = stripAllowed(line)
    if (CJK.test(masked)) {
      offenders.push(`${file}:${i + 1}: ${line.trim()}`)
    }
  })
  return offenders
}

describe('P2-12 batch 1: i18n migration', () => {
  for (const file of BATCH_1_FILES) {
    it(`has no hardcoded Chinese strings: ${file.split(/[\\/]/).slice(-2).join('/')}`, () => {
      const offenders = findHardcodedCjk(file)
      expect(
        offenders,
        `Hardcoded CJK found (should use t()):\n${offenders.join('\n')}`,
      ).toEqual([])
    })
  }
})
