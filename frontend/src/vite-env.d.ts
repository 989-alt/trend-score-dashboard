/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" → static-demo build (GitHub Pages): fetch bundled sample JSON, no backend. */
  readonly VITE_STATIC?: string;
}
