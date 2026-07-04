/// <reference types="vite/client" />

interface Window {
  lucasDbConfig?: {
    apiToken?: string;
    apiTokenSource?: 'env' | 'default';
    apiBaseUrl?: string;
  };
}
