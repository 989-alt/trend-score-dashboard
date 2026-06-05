/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" → static-demo build (GitHub Pages): fetch bundled sample JSON, no backend. */
  readonly VITE_STATIC?: string;
  /** Remote backend origin (e.g. OCI) for live data on the Pages frontend. */
  readonly VITE_API_BASE?: string;
}
