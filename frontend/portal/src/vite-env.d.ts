/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base domain for host-based client URLs (default radar-analytics.com). */
  readonly VITE_BASE_DOMAIN?: string;
  readonly VITE_VIEWER_URL?: string;
}
