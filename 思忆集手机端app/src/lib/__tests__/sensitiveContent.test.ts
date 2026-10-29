import { describe, it, expect } from 'vitest'
import { detectSensitiveContent, redactSensitive } from '../sensitiveContent'

describe('detectSensitiveContent (P0-10)', () => {
  it('flags password keywords', () => {
    expect(detectSensitiveContent('请输入密码').isSensitive).toBe(true)
    expect(detectSensitiveContent('Enter your password').isSensitive).toBe(true)
  })

  it('flags OTP / verification code patterns', () => {
    const v = detectSensitiveContent('您的验证码是 832914，5 分钟内有效')
    expect(v.isSensitive).toBe(true)
    expect(v.matched.length).toBeGreaterThan(0)
  })

  it('flags Chinese 18-digit ID number', () => {
    expect(detectSensitiveContent('身份证号 110101199003076789').isSensitive).toBe(true)
  })

  it('flags credit card patterns', () => {
    const v = detectSensitiveContent('卡号 4532 1234 5678 9010')
    expect(v.isSensitive).toBe(true)
  })

  it('flags Chinese mobile phone number', () => {
    expect(detectSensitiveContent('联系 13901234567').isSensitive).toBe(true)
  })

  it('flags banking keywords', () => {
    expect(detectSensitiveContent('查看银行卡余额').isSensitive).toBe(true)
    expect(detectSensitiveContent('支付密码错误').isSensitive).toBe(true)
  })

  it('returns isSensitive=false for benign text', () => {
    expect(detectSensitiveContent('今天天气真好').isSensitive).toBe(false)
    expect(detectSensitiveContent('请帮我写一篇日记').isSensitive).toBe(false)
    expect(detectSensitiveContent('Open the calculator app').isSensitive).toBe(false)
  })

  it('handles empty input gracefully', () => {
    expect(detectSensitiveContent('').isSensitive).toBe(false)
  })

  it('matched array is informative for UX (not just true/false)', () => {
    const v = detectSensitiveContent('密码：abc123\n身份证号 110101199003076789')
    expect(v.isSensitive).toBe(true)
    expect(v.matched.length).toBeGreaterThanOrEqual(2)
  })
})

describe('redactSensitive (P0-10)', () => {
  it('replaces ID numbers and phone numbers with marker', () => {
    const out = redactSensitive('我的身份证 110101199003076789，电话 13901234567')
    expect(out).not.toContain('110101199003076789')
    expect(out).not.toContain('13901234567')
    expect(out).toContain('已脱敏')
  })

  it('leaves benign text unchanged', () => {
    const text = '今天天气真好，准备出门散步'
    expect(redactSensitive(text)).toBe(text)
  })
})
