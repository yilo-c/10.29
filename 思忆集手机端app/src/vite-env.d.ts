/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SENTRY_DSN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module 'lunar-javascript' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const lunar: any
  export default lunar
}
