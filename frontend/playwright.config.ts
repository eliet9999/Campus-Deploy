import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir:'./e2e', timeout:180000, expect:{timeout:30000}, workers:1,
  use:{baseURL:'http://localhost:3000', viewport:{width:1440,height:1000}, headless:true, screenshot:'only-on-failure', trace:'off'},
  reporter:[['list'],['json',{outputFile:'../evidence/playwright-results.json'}]],
});
