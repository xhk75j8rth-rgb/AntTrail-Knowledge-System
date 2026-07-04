import { contextBridge } from 'electron';

contextBridge.exposeInMainWorld('lucasDbConfig', {
  apiToken: process.env.LUCAS_DB_API_TOKEN || 'lucas-local-dev-token',
  apiTokenSource: process.env.LUCAS_DB_API_TOKEN ? 'env' : 'default',
  apiBaseUrl: process.env.LUCAS_DB_API_BASE_URL || ''
});
