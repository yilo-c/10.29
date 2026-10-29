import { describe, it, expect } from 'vitest'
import {
  IntentType,
  AiTarget,
  PrivacyLevel,
  classifyIntent,
  classifyPrivacy,
  isPersonalContent,
  localNavScreen,
  resolveTarget,
  type UserAiPreference,
} from '../aiRouter'

const defaultPref: UserAiPreference = {
  cloudAiEnabled: true,
  promptBeforeCloud: true,
  preferDesktopAi: false,
}

describe('classifyIntent', () => {
  it('classifies LOCAL_CRUD for creation keywords', () => {
    expect(classifyIntent('创建笔记')).toBe(IntentType.LOCAL_CRUD)
    expect(classifyIntent('新建任务')).toBe(IntentType.LOCAL_CRUD)
    expect(classifyIntent('删除待办')).toBe(IntentType.LOCAL_CRUD)
    expect(classifyIntent('编辑笔记')).toBe(IntentType.LOCAL_CRUD)
    expect(classifyIntent('create a new note')).toBe(IntentType.LOCAL_CRUD)
    expect(classifyIntent('add task')).toBe(IntentType.LOCAL_CRUD)
  })

  it('classifies NAVIGATION for known screens', () => {
    expect(classifyIntent('打开笔记')).toBe(IntentType.NAVIGATION)
    expect(classifyIntent('去设置')).toBe(IntentType.NAVIGATION)
    expect(classifyIntent('显示日历')).toBe(IntentType.NAVIGATION)
    expect(classifyIntent('收件箱')).toBe(IntentType.NAVIGATION)
    expect(classifyIntent('笔记')).toBe(IntentType.NAVIGATION)
  })

  it('classifies INFO_FETCH for search/lookup keywords', () => {
    expect(classifyIntent('查天气')).toBe(IntentType.INFO_FETCH)
    expect(classifyIntent('搜索内容')).toBe(IntentType.INFO_FETCH)
    expect(classifyIntent('查询数据')).toBe(IntentType.INFO_FETCH)
    expect(classifyIntent('search for something')).toBe(IntentType.INFO_FETCH)
    expect(classifyIntent('show my notes')).toBe(IntentType.INFO_FETCH)
  })

  it('defaults to COMPLEX_REASONING for unmatched text', () => {
    expect(classifyIntent('今天天气怎么样？')).toBe(IntentType.COMPLEX_REASONING)
    expect(classifyIntent('帮我分析一下')).toBe(IntentType.COMPLEX_REASONING)
    expect(classifyIntent('你好')).toBe(IntentType.COMPLEX_REASONING)
  })
})

describe('classifyPrivacy', () => {
  it('returns LOCAL_ONLY for sensitive patterns', () => {
    // P0 修复：原测试断言写的是 SYNCABLE，但实现一直返回 LOCAL_ONLY —
    // 敏感内容（日记/密码/API key 等）应留本地不上云，是正确语义。
    // 修测试断言以保护正确行为。
    expect(classifyPrivacy('我的日记')).toBe(PrivacyLevel.LOCAL_ONLY)
    expect(classifyPrivacy('私人笔记')).toBe(PrivacyLevel.LOCAL_ONLY)
    expect(classifyPrivacy('我的密码是 abc')).toBe(PrivacyLevel.LOCAL_ONLY)
    expect(classifyPrivacy('private notes')).toBe(PrivacyLevel.LOCAL_ONLY)
  })

  it('returns default privacy for non-sensitive content', () => {
    expect(classifyPrivacy('购物清单', PrivacyLevel.SHAREABLE)).toBe(PrivacyLevel.SHAREABLE)
    expect(classifyPrivacy('普通笔记', PrivacyLevel.MODEL_EGRESS_ALLOWED)).toBe(PrivacyLevel.MODEL_EGRESS_ALLOWED)
  })
})

describe('isPersonalContent', () => {
  it('identifies personal content patterns', () => {
    expect(isPersonalContent('这是我的笔记')).toBe(true)
    expect(isPersonalContent('my note about something')).toBe(true)
    expect(isPersonalContent('private thoughts')).toBe(true)
  })

  it('rejects non-personal content', () => {
    expect(isPersonalContent('公开笔记')).toBe(false)
    expect(isPersonalContent('购物清单')).toBe(false)
  })
})

describe('localNavScreen', () => {
  it('maps text to known screens', () => {
    expect(localNavScreen('打开笔记')).toBe('笔记')
    expect(localNavScreen('去设置页面')).toBe('设置')
    expect(localNavScreen('待办事项')).toBe('待办')
    expect(localNavScreen('设置闹钟提醒')).toBe('设置')
    expect(localNavScreen('添加闹钟')).toBe('闹钟')
    expect(localNavScreen('同步数据')).toBe('同步')
  })

  it('returns null for unknown screens', () => {
    expect(localNavScreen('随便说说')).toBeNull()
    expect(localNavScreen('你好')).toBeNull()
  })
})

describe('resolveTarget', () => {
  it('routes LOCAL_CRUD to LOCAL_DATABASE', () => {
    expect(resolveTarget(IntentType.LOCAL_CRUD, PrivacyLevel.LOCAL_ONLY, false, defaultPref))
      .toBe(AiTarget.LOCAL_DATABASE)
  })

  it('routes INFO_FETCH to NETWORK_API', () => {
    expect(resolveTarget(IntentType.INFO_FETCH, PrivacyLevel.SHAREABLE, false, defaultPref))
      .toBe(AiTarget.NETWORK_API)
  })

  it('routes NAVIGATION to DESKTOP_LOCAL_AI when desktop connected and preferred', () => {
    const pref: UserAiPreference = { ...defaultPref, preferDesktopAi: true }
    expect(resolveTarget(IntentType.NAVIGATION, PrivacyLevel.SYNCABLE, true, pref))
      .toBe(AiTarget.DESKTOP_LOCAL_AI)
  })

  it('routes NAVIGATION to CLOUD_AI when desktop not connected', () => {
    expect(resolveTarget(IntentType.NAVIGATION, PrivacyLevel.SYNCABLE, false, defaultPref))
      .toBe(AiTarget.CLOUD_AI)
  })

  it('routes COMPLEX_REASONING to DESKTOP_LOCAL_AI for private content with desktop', () => {
    expect(resolveTarget(IntentType.COMPLEX_REASONING, PrivacyLevel.LOCAL_ONLY, true, defaultPref))
      .toBe(AiTarget.DESKTOP_LOCAL_AI)
  })

  it('routes COMPLEX_REASONING to QUEUED_PENDING for private content without desktop', () => {
    expect(resolveTarget(IntentType.COMPLEX_REASONING, PrivacyLevel.LOCAL_ONLY, false, defaultPref))
      .toBe(AiTarget.QUEUED_PENDING)
  })

  it('routes COMPLEX_REASONING to CLOUD_AI for model_egress_allowed', () => {
    expect(resolveTarget(IntentType.COMPLEX_REASONING, PrivacyLevel.MODEL_EGRESS_ALLOWED, false, defaultPref))
      .toBe(AiTarget.CLOUD_AI)
  })

  it('routes COMPLEX_REASONING to QUEUED_PENDING for shareable when promptBeforeCloud', () => {
    expect(resolveTarget(IntentType.COMPLEX_REASONING, PrivacyLevel.SHAREABLE, false, defaultPref))
      .toBe(AiTarget.QUEUED_PENDING)
  })

  it('routes COMPLEX_REASONING to CLOUD_AI for shareable when no prompt', () => {
    const pref: UserAiPreference = { ...defaultPref, promptBeforeCloud: false }
    expect(resolveTarget(IntentType.COMPLEX_REASONING, PrivacyLevel.SHAREABLE, false, pref))
      .toBe(AiTarget.CLOUD_AI)
  })
})
