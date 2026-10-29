/**
 * M2.2 第0步：客户端版本声明（client_version）的唯一来源。
 *
 * 后端 M2.1 已上线 R4 向后兼容（`思忆集app/backend/ws_server.py:952-955` 握手 query
 * 参数、`:1153-1158` 帧内字段、`:711` HTTP body；判定点
 * `method_registry.py:229-292`）：**未声明 client_version 的连接视为 legacy_client，
 * 其 R4 方法（notes.delete / tasks.delete 等）不再返回 {pending:true}，
 * 而是显式 JSON-RPC -32010 CLIENT_UPGRADE_REQUIRED（请升级客户端）**。
 *
 * desk-notes 此前既不带 query 参数也不带帧字段 → 后端下次重启，全部 R4 操作报错。
 * 本模块提供连接期与帧级两处共用的版本串。
 */

/** 兜底版本串：与 packages/desktop/package.json 的 version 保持一致
 *  （回归测试 client-version.test.ts 会读 desktop/package.json 断言同步）。 */
export const PACKAGE_CLIENT_VERSION = '1.2.0'

type RuntimeWithClientVersion = typeof globalThis & {
  __SIYI_CLIENT_VERSION__?: string
}

/**
 * 解析本次连接应声明的 client_version。
 * 优先级：显式 override > 运行时全局（打包/Electron 注入点）> package.json 兜底常量。
 * 后端只做「非空即新客户端」的最小识别（不做格式/白名单校验，见 test_redteam_m21 F③），
 * 故此处必须保证返回值永不为空串。
 */
export function resolveClientVersion(override?: string): string {
  const candidate = override?.trim()
    || (globalThis as RuntimeWithClientVersion).__SIYI_CLIENT_VERSION__?.trim()
    || PACKAGE_CLIENT_VERSION
  return candidate
}
