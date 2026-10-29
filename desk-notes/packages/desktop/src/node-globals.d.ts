// Minimal Node.js type declarations for frontend build (no @types/node dependency needed).
// Only covers the symbols actually used in src/.

declare namespace NodeJS {
  interface ProcessEnv {
    NODE_ENV?: string
    [key: string]: string | undefined
  }
}

declare let process: {
  env: NodeJS.ProcessEnv
}

// Used in test files: vi.mock(() => { const React = require('react') ... })
declare let require: (id: string) => any

// Used in i18n-batch1.test.ts / aiConfig.test.ts
declare let __dirname: string
declare module 'node:fs' {
  export function readFileSync(path: string, encoding: string): string
  export function readFileSync(path: string): Buffer
  export function readdirSync(path: string): string[]
  export function statSync(path: string): { isDirectory(): boolean; isFile(): boolean }
}
declare module 'node:path' {
  import { resolve, join } from 'path'
  export { resolve, join }
}
