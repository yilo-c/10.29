// Vault 能力描述符 —— 把 VaultService 投影成统一能力（对应规范 §6.1 / §7.1）
//
// 这一层是「中立契约」：名字、schema、风险、超时都不含 DSH 方言。
// DSH adapter 负责把它转成 dsh tool；CLI/MCP adapter 转成各自格式。

import { VaultError } from './service.mjs'

const SPEC = 'siyi.capability/v1'

function descriptor(over) {
  return {
    specVersion: SPEC,
    version: '1.0.0',
    kind: 'command',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    risk: 'read',
    sensitivity: 'personal',
    permissions: [],
    idempotent: true,
    supportsCancel: false,
    timeoutMs: 10000,
    concurrency: 'parallel',
    ...over,
  }
}

/** 把 VaultError 归一成 bridge 错误码。 */
function toBridgeError(e) {
  if (e instanceof VaultError) {
    return Object.assign(new Error(e.message), { code: e.code, details: e.details })
  }
  throw e
}

function wrap(fn) {
  return async (args, ctx) => {
    try { return await fn(args, ctx) } catch (e) { throw toBridgeError(e) }
  }
}

export function vaultCapabilities(vault) {
  return {
    'vault.get': {
      descriptor: descriptor({
        title: '读取笔记',
        description: '按服务端生成的 26 位 ULID 读取一篇笔记，返回正文、元数据、双链、标签和 revision',
        inputSchema: {
          type: 'object',
          properties: { id: { type: 'string', pattern: '^[0-9A-HJKMNP-TV-Z]{26}$' } },
          required: ['id'],
          additionalProperties: false,
        },
        outputSchema: {
          type: 'object',
          properties: {
            id: { type: 'string' }, title: { type: 'string' },
            body: { type: 'string' }, revision: { type: 'string' },
          },
        },
        risk: 'read', timeoutMs: 10000,
      }),
      execute: wrap(async args => {
        const n = vault.get(args.id)
        return {
          id: n.id, title: n.meta.title || '', body: n.body,
          tags: n.meta.tags || [], links: n.links, revision: n.revision,
          logicalPath: n.logicalPath,
        }
      }),
    },

    'vault.search': {
      descriptor: descriptor({
        title: '搜索笔记',
        description: '按关键词、类型、文件夹、标签、状态筛选笔记，默认排除 secret 敏感笔记',
        inputSchema: {
          type: 'object',
          properties: {
            q: { type: 'string' }, kind: { type: 'string' },
            folder: { type: 'string' }, tag: { type: 'string' },
            status: { type: 'string' },
            includeSensitive: { type: 'boolean' },
            limit: { type: 'integer', minimum: 1, maximum: 2000 },
          },
          additionalProperties: false,
        },
        risk: 'read', timeoutMs: 15000,
        outputLimits: { maxItems: 2000 },
      }),
      execute: wrap(async args => vault.list(args || {})),
    },

    'vault.save': {
      descriptor: descriptor({
        title: '保存笔记',
        description: '新建笔记（id 由服务端生成），或按 expectedRevision 更新已有笔记；冲突返回 CONFLICT',
        inputSchema: {
          type: 'object',
          properties: {
            id: { type: 'string' },
            expectedRevision: { type: 'string', pattern: '^[0-9a-f]{64}$' },
            title: { type: 'string' }, body: { type: 'string' },
            kind: { type: 'string' }, tags: { type: 'array', items: { type: 'string' } },
            folder: { type: 'string' }, status: { type: 'string' },
            due: { type: 'string' }, sensitivity: { type: 'string' },
          },
          additionalProperties: false,
        },
        risk: 'write', idempotent: true, supportsCancel: false,
        timeoutMs: 20000, concurrency: 'serialized',
      }),
      execute: wrap(async (args, ctx) => {
        if (args.id) {
          return vault.update(args.id, args, { expectedRevision: args.expectedRevision })
        }
        // 新建：明确剔除 id 字段，防止外部 id 进入
        const { id, expectedRevision, ...input } = args
        return vault.create(input)
      }),
    },

    'vault.trash': {
      descriptor: descriptor({
        title: '移入回收站',
        description: '把笔记移到 .siyi/trash，不永久删除；带 expectedRevision 时校验内容未变',
        inputSchema: {
          type: 'object',
          properties: { id: { type: 'string' }, expectedRevision: { type: 'string' } },
          required: ['id'],
          additionalProperties: false,
        },
        risk: 'write', idempotent: false, timeoutMs: 15000, concurrency: 'serialized',
      }),
      execute: wrap(async args => vault.trash(args.id, { expectedRevision: args.expectedRevision })),
    },

    'vault.stats': {
      descriptor: descriptor({
        title: '库统计',
        description: '返回笔记总数、按类型与状态分布',
        inputSchema: { type: 'object', properties: {}, additionalProperties: false },
        risk: 'read', timeoutMs: 20000,
      }),
      execute: wrap(async () => vault.stats()),
    },

    'vault.reconcile': {
      descriptor: descriptor({
        title: '索引对账',
        description: '清理崩溃遗留临时文件并重算笔记数；文件监听丢事件时用它兜底',
        inputSchema: { type: 'object', properties: {}, additionalProperties: false },
        risk: 'write', idempotent: true, timeoutMs: 60000, concurrency: 'exclusive',
      }),
      execute: wrap(async () => vault.reconcile()),
    },
  }
}
