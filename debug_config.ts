import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: '/home/lap140/Descargas',
  testMatch: 'example.spec.ts',
  use: {
    headless: false,
    launchOptions: {
      args: [
        '--remote-debugging-port=9333',
        '--no-first-run',
        '--no-default-browser-check',
      ],
    },
  },
});
