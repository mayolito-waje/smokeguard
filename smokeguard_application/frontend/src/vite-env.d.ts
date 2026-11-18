/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Repository URL (or `owner/repo`) for Model Info source links. */
  readonly VITE_GITHUB_REPO?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
