/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API base path. Defaults to '/api/v1', served through the dev proxy. */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
