import { chromium } from 'playwright';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on('console', msg => {
    if (msg.type() === 'error' || msg.type() === 'warning') {
      errors.push(`[${msg.type()}] ${msg.text()}`);
    }
  });
  page.on('pageerror', err => errors.push(`[PAGE_ERROR] ${err.message}`));

  await page.goto('http://localhost:5178/', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(3000);

  console.log('\n=== Console errors/warnings ===');
  if (errors.length === 0) {
    console.log('No errors found');
  } else {
    errors.forEach(e => console.log(e));
  }

  // Check if app rendered
  const hasApp = await page.$('#root, .app');
  console.log('App rendered:', !!hasApp);

  await browser.close();
})();
