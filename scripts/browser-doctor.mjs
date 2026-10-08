import { chromium } from '../frontend/node_modules/playwright/index.mjs';
const browser=await chromium.launch({headless:true});
try {
  const page=await browser.newPage();
  const response=await page.goto(process.argv[2],{timeout:15000});
  if(response.status()!==404)throw new Error('Expected unknown Host 404, got '+response.status());
  console.log('Chromium resolved subdomain and reached gateway: HTTP 404 for unknown project. No DNS overrides used.');
} finally { await browser.close(); }
