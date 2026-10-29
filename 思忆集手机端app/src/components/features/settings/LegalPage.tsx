import { IconX } from '../../ui'

interface LegalPageProps {
  type: 'privacy' | 'terms'
  onClose: () => void
}

export default function LegalPage({ type, onClose }: LegalPageProps) {
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 100,
      background: 'var(--bg-primary)',
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '16px 20px', flexShrink: 0,
        borderBottom: '0.5px solid var(--border-subtle)',
      }}>
        <div style={{
          fontSize: 'var(--text-title-1)', fontWeight: 700,
          letterSpacing: -0.5,
        }}>
          {type === 'privacy' ? '隐私政策' : '用户协议'}
        </div>
        <button
          onClick={onClose}
          style={{
            width: 36, height: 36, borderRadius: '50%',
            border: 'none', background: 'var(--bg-secondary)',
            cursor: 'pointer', display: 'flex', alignItems: 'center',
            justifyContent: 'center', color: 'var(--text-primary)',
          }}
        >
          <IconX size={18} />
        </button>
      </div>

      <div style={{
        flex: 1, overflowY: 'auto',
        padding: '20px 20px 40px',
        fontSize: 'var(--text-footnote)',
        color: 'var(--text-secondary)',
        lineHeight: 1.8,
      }}>
        {type === 'privacy' ? <PrivacyContent /> : <TermsContent />}
      </div>
    </div>
  )
}

function PrivacyContent() {
  return (
    <>
      <p style={{ marginBottom: 16, color: 'var(--text-tertiary)' }}>
        最后更新：2026-05-16
      </p>

      <Section title="一、信息收集与使用">
        <p>本应用在您使用以下功能时，可能会收集您主动提供的信息：</p>
        <ul>
          <li><strong>个人信息</strong>：出生日期、出生时辰、性别（用于八字排盘和历法参考计算）。这些信息仅存储在您的设备本地，我们不会上传至任何服务器。</li>
          <li><strong>待办与笔记内容</strong>：您创建的待办事项和笔记内容仅存储在设备本地。如您启用云端同步功能，数据将以端到端加密形式传输。</li>
          <li><strong>应用设置</strong>：主题偏好、提醒设置等应用配置，仅存储在设备本地。</li>
        </ul>
        <p style={{ marginTop: 8 }}><strong>我们不收集：</strong>位置信息、通讯录、相册、设备标识符、网络浏览历史、生物识别数据（PIN 码经哈希处理后存储，不可逆）。</p>
      </Section>

      <Section title="二、数据存储与传输">
        <p>默认情况下，所有数据仅存储在您的设备上（localStorage）。</p>
        <p>如果您选择启用云端同步功能（需付费订阅）：</p>
        <ul>
          <li>待办、笔记、设置等结构化数据将以 AES-256-GCM 加密后上传</li>
          <li>加密密钥由您的账户密码派生，服务器无法读取明文数据</li>
          <li>您可以在任何时候完全清除服务器上的数据</li>
        </ul>
        <p>我们不会将您的数据出售、出租或分享给第三方。</p>
      </Section>

      <Section title="三、第三方服务">
        <p>本应用使用了以下第三方服务，均仅在您明确启用时工作：</p>
        <ul>
          <li><strong>AI 辅助功能</strong>（可选）：您可配置自己的 AI API 地址。请求直接发送至您指定的服务器，我们不介入数据传输。</li>
          <li><strong>系统通知</strong>：通过浏览器 Notification API 发送提醒通知，不涉及第三方服务。</li>
          <li><strong>生物识别 / 设备锁</strong>：通过设备系统 API 实现，本应用不存储您的生物特征数据。</li>
        </ul>
      </Section>

      <Section title="四、数据删除">
        <p>您可以随时在「设置 → 数据管理 → 清除数据」中删除应用内的全部数据。此操作不可撤销。如需删除云端同步数据，请在同步设置中执行「清除云端数据」。</p>
      </Section>

      <Section title="五、儿童隐私">
        <p>本应用不面向 14 周岁以下的儿童。如果我们发现收集了 14 周岁以下用户的个人信息，将立即删除。如您是监护人并认为我们可能收集了相关信息，请通过下方联系方式与我们联系。</p>
      </Section>

      <Section title="六、隐私政策更新">
        <p>我们可能会不时更新本隐私政策。更新后的政策将在应用内展示。重大变更（如新增数据收集类型）将通过应用内通知或启动弹窗告知。</p>
      </Section>

      <Section title="七、联系我们">
        <p>如您对本隐私政策有任何疑问，请联系我们：</p>
        <p>邮箱：privacy@siyinotes.app</p>
      </Section>
    </>
  )
}

function TermsContent() {
  return (
    <>
      <p style={{ marginBottom: 16, color: 'var(--text-tertiary)' }}>
        最后更新：2026-05-16
      </p>
      <p style={{ marginBottom: 16 }}>
        欢迎使用思忆便签。请仔细阅读以下条款。使用本应用即表示您同意接受本协议的约束。
      </p>

      <Section title="一、服务说明">
        <p>思忆便签是一款以传统历法为参考框架的日历与待办管理工具（"本应用"）。本应用提供的所有内容，包括但不限于历法参考信息、宜忌建议、八字排盘等，均基于传统历法理论和公开算法计算，仅供决策参考，不构成任何形式的建议、预测或保证。</p>
      </Section>

      <Section title="二、用户责任">
        <ul>
          <li>您应妥善保管您的设备锁屏 PIN 码，因 PIN 码泄露导致的数据泄露风险由您自行承担。</li>
          <li>您不得利用本应用从事任何违法违规活动。</li>
          <li>您不得尝试反向工程、破解或篡改本应用的任何部分。</li>
        </ul>
      </Section>

      <Section title="三、付费服务">
        <p>本应用采用免费增值模式：</p>
        <ul>
          <li><strong>免费功能</strong>：日历、待办、笔记、今日黄历参考</li>
          <li><strong>Pro 功能</strong>：云端同步、高级历法参考（八字详解、时辰建议、择日对比）、多设备支持</li>
          <li><strong>AI 辅助</strong>：本地 AI 决策辅助（需自行配置 API）</li>
        </ul>
        <p>Pro 订阅为自动续费订阅，您可以在当前周期结束前随时取消。买断为一次性付费，终身有效。</p>
        <p>我们保留调整定价的权利，价格调整将提前通知并仅适用于后续订阅周期。</p>
      </Section>

      <Section title="四、免责声明">
        <p>本应用提供的历法参考信息依据传统历法理论计算，仅供决策参考。开发者不对以下情况承担责任：</p>
        <ul>
          <li>因依赖本应用的历法参考信息做出的任何决策所导致的任何损失或后果</li>
          <li>因数据丢失（包括但不限于本地数据清除、设备损坏、同步冲突）导致的任何损失</li>
          <li>因不可抗力（设备故障、系统崩溃、自然灾害等）导致的服务中断</li>
        </ul>
      </Section>

      <Section title="五、知识产权">
        <p>本应用的全部知识产权（包括但不限于软件架构、UI 设计、历法算法实现、品牌标识）归开发者所有。未经书面授权，您不得复制、分发、修改或创建衍生作品。</p>
      </Section>

      <Section title="六、终止">
        <p>我们保留在您违反本协议任何条款时终止您使用本应用的权利。终止后，您应卸载本应用并删除所有副本。</p>
      </Section>

      <Section title="七、争议解决">
        <p>本协议适用中华人民共和国法律。因本协议引起的或与之相关的争议，双方应友好协商解决；协商不成的，提交开发者所在地有管辖权的人民法院诉讼解决。</p>
      </Section>

      <Section title="八、联系我们">
        <p>邮箱：legal@siyinotes.app</p>
      </Section>
    </>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{
        fontSize: 'var(--text-body)',
        fontWeight: 600,
        color: 'var(--text-primary)',
        marginBottom: 8,
      }}>
        {title}
      </div>
      <div>{children}</div>
    </div>
  )
}
