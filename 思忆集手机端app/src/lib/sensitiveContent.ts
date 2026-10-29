import { swallowError } from './swallowError'
/**
 * Sensitive content detection (P0-10).
 *
 * Used to gate screen-readout text before it leaves the device. The screen
 * may contain banking apps, password fields, OTP codes, ID numbers, etc. If
 * any of these are detected we strip / warn / block sending to cloud AI.
 *
 * Heuristics aren't a substitute for a human review prompt, but they cut the
 * tail risk of obviously-sensitive content being sucked up by the LLM.
 */

const SENSITIVE_KEYWORDS = [
  // Auth credentials
  '密码', '口令', '验证码', '动态码', 'OTP', 'PIN码', 'PIN 码',
  'password', 'passwd', 'token', 'secret', 'api[_-]?key',
  // Banking
  '银行卡', '银行卡号', '储蓄卡', '信用卡', 'CVV', 'cvv',
  '支付密码', '支付宝', '微信支付', '余额',
  // Identity
  '身份证', '身份证号', '社保卡', '护照号', 'ID号',
  // Health
  '病历', '诊断', '处方',
] as const

const SENSITIVE_PATTERNS: RegExp[] = [
  // Chinese 18-digit ID number (loose — last char may be X/x)
  /\b\d{17}[\dXx]\b/,
  // 16-19 digit bank card numbers (with optional spaces every 4)
  /\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4,7}\b/,
  // 6-digit OTP codes adjacent to "验证码" / "code"
  /(?:验证码|动态码|code)[^\d]{0,8}\d{4,8}/i,
  // Phone numbers (CN mobile)
  /\b1[3-9]\d{9}\b/,
]

export interface SensitivityVerdict {
  /** Whether any sensitive content was detected. */
  isSensitive: boolean
  /** Matched keywords / patterns, for UX messaging. */
  matched: string[]
}

export function detectSensitiveContent(text: string): SensitivityVerdict {
  if (!text) return { isSensitive: false, matched: [] }

  const matched: string[] = []
  const lower = text.toLowerCase()

  for (const kw of SENSITIVE_KEYWORDS) {
    // Use case-insensitive substring match for simplicity. Keywords containing
    // regex meta-chars like \\b are escaped via includes.
    if (kw.includes('[') || kw.includes('\\')) {
      try {
        if (new RegExp(kw, 'i').test(text)) matched.push(kw)
      } catch (e) { /* skip bad patterns */ swallowError('sensitiveContent:55', e) }
    } else if (lower.includes(kw.toLowerCase())) {
      matched.push(kw)
    }
  }

  for (const re of SENSITIVE_PATTERNS) {
    const m = text.match(re)
    if (m) matched.push(m[0])
  }

  return { isSensitive: matched.length > 0, matched }
}

/**
 * Redact sensitive substrings, leaving an indicator. Use this if the user
 * still wants to send something but the cloud AI shouldn't see the secret.
 */
export function redactSensitive(text: string): string {
  let out = text
  for (const re of SENSITIVE_PATTERNS) {
    out = out.replace(re, '［已脱敏］')
  }
  return out
}
