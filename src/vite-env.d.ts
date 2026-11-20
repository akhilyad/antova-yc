/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_INTAKE_ENDPOINT?: string;
  readonly VITE_WORKBENCH_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
