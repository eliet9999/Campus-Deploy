import {test,expect,Page,BrowserContext} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
const root=path.resolve('..');
const sample=(name:string)=>path.join(root,'samples',name+'.zip');
const evidence=(name:string)=>path.join(root,'evidence',name);
const password=fs.readFileSync(path.join(root,'.secrets/admin-password.txt'),'utf8').trim();

async function browserStatus(context:BrowserContext,url:string){
  const probe=await context.newPage();
  try {return (await probe.goto(url))!.status();} finally {await probe.close();}
}
async function login(page:Page){
  await page.goto('/');
  await page.getByLabel('관리자 암호').fill(password);
  await page.getByRole('button',{name:'워크스페이스 열기'}).click();
  await expect(page.getByRole('heading',{name:'프로젝트',exact:true})).toBeVisible();
}
async function upload(page:Page,name:string){
  await page.getByRole('button',{name:'ZIP 업로드',exact:true}).click();
  await page.getByLabel('프로젝트 ZIP',{exact:true}).setInputFiles(sample(name));
  await page.getByRole('button',{name:'소스 검사',exact:true}).click();
  await expect(page.getByText('지원되는 프로젝트입니다')).toBeVisible();
  await page.getByRole('button',{name:'배포하기'}).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.getByRole('link',{name:'Preview 열기'})).toBeVisible({timeout:120000});
}
function monitor(page:Page){
  const errors:string[]=[],failures:string[]=[],consoleErrors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('requestfailed',r=>{if(r.failure()?.errorText!=='net::ERR_ABORTED')failures.push(r.url()+': '+r.failure()?.errorText)});
  page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('401'))consoleErrors.push(m.text())});
  return {errors,failures,consoleErrors};
}

test('Public GitHub URL is the default and deploys without a ZIP or typed project names',async({page,context})=>{
  await login(page);
  await page.getByRole('button',{name:'새 프로젝트'}).click();
  await expect(page.getByRole('button',{name:'공개 GitHub',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page.getByLabel('프로젝트 ZIP',{exact:true})).toHaveCount(0);
  const repository='https://github.com/mdn/beginner-html-site-styled';
  await page.getByLabel('GitHub 저장소 주소',{exact:true}).fill(repository);
  await expect(page.getByLabel('프로젝트 이름')).toHaveValue('beginner-html-site-styled');
  await expect(page.getByLabel('프로젝트 주소 (slug)')).toHaveValue(/^beginner-html-site-styled-[a-f0-9]{6}$/);
  await page.getByRole('button',{name:'소스 검사',exact:true}).click();
  await expect(page.getByText('지원되는 프로젝트입니다')).toBeVisible({timeout:120000});
  await page.screenshot({path:evidence('10-github-url.png'),fullPage:true});
  await page.getByRole('button',{name:'배포하기'}).click();
  await expect(page.getByRole('link',{name:'Preview 열기'})).toBeVisible({timeout:120000});
  const url=await page.getByRole('link',{name:'운영 사이트 열기'}).getAttribute('href');
  const site=await context.newPage();
  expect((await site.goto(url!))!.status()).toBe(200);
  await expect(site.getByRole('heading',{name:'Mozilla is cool'})).toBeVisible();
  const projectId=new URL(page.url()).hash.slice(1);
  const project=await page.evaluate(async id=>(await fetch('/api/projects/'+id)).json(),projectId);
  expect(project.source.kind).toBe('GIT');
  expect(project.source.sha).toMatch(/^[a-f0-9]{40}$/);
  fs.writeFileSync(evidence('browser-github.json'),JSON.stringify({repository,url,projectId,source_kind:project.source.kind,sha:project.source.sha,preset:project.preset,status:project.deployments[0].status,zip_uploaded:false,names:'automatically filled'},null,2));
});

test('STATIC UI upload → Preview → promote → rollback → cleanup',async({page,context})=>{
  const issues=monitor(page);
  await page.goto('/');
  await page.screenshot({path:evidence('01-login.png'),fullPage:true});
  await login(page);
  await page.getByRole('button',{name:'새 프로젝트'}).click();
  const slug='ui-static-'+Date.now();
  await page.getByLabel('프로젝트 이름').fill('브라우저 시연 · Campus Garden');
  await page.getByLabel('프로젝트 주소 (slug)').fill(slug);
  await page.getByRole('button',{name:'ZIP 업로드',exact:true}).click();
  await page.getByLabel('프로젝트 ZIP',{exact:true}).setInputFiles(sample('static-v1'));
  await page.getByRole('button',{name:'소스 검사',exact:true}).click();
  await expect(page.getByText('지원되는 프로젝트입니다')).toBeVisible();
  await page.screenshot({path:evidence('02-source-detection.png'),fullPage:true});
  await page.getByRole('button',{name:'배포하기'}).click();
  await expect(page.getByRole('link',{name:'Preview 열기'})).toBeVisible();
  const v1=await page.getByRole('link',{name:'Preview 열기'}).getAttribute('href');
  const production=await page.getByRole('link',{name:'운영 사이트 열기'}).getAttribute('href');
  const site=await context.newPage();const siteIssues=monitor(site);
  await site.goto(production!);await expect(site.getByText('CAMPUS GARDEN / VERSION 01')).toBeVisible();
  await site.getByRole('button',{name:'인사하기'}).click();await expect(site.getByText('안녕하세요! v1 JavaScript가 동작합니다.')).toBeVisible();
  await expect(site.locator('body')).toHaveCSS('background-color','rgb(237, 242, 232)');
  await site.screenshot({path:evidence('03-static-browser.png'),fullPage:true});
  await page.getByRole('button',{name:'새 버전 배포',exact:true}).click();await upload(page,'static-v2');
  await expect(page.getByRole('link',{name:'Preview 열기'})).not.toHaveAttribute('href',v1!);
  const v2=await page.getByRole('link',{name:'Preview 열기'}).getAttribute('href');
  expect(v2).not.toBe(v1);
  await site.reload();await expect(site.getByText('CAMPUS GARDEN / VERSION 01')).toBeVisible();
  await site.goto(v2!);await expect(site.getByText('CAMPUS GARDEN / VERSION 02')).toBeVisible();
  await page.getByRole('button',{name:'운영 반영',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'운영 반영',exact:true}).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await site.goto(production!);await site.reload();await expect(site.getByText('CAMPUS GARDEN / VERSION 02')).toBeVisible();
  await site.goto(v1!);await expect(site.getByText('CAMPUS GARDEN / VERSION 01')).toBeVisible();
  await page.getByRole('button',{name:'이 버전으로 롤백',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'이 버전으로 롤백',exact:true}).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await site.goto(production!);await site.reload();await expect(site.getByText('CAMPUS GARDEN / VERSION 01')).toBeVisible();
  await page.reload();await expect(page.getByLabel('실행 로그')).toContainText('[READY]');
  await page.screenshot({path:evidence('04-project-rollback.png'),fullPage:true});
  await page.getByRole('button',{name:'프로젝트 삭제',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'프로젝트 삭제',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('정리 완료');
  expect(await browserStatus(context,production!)).toBe(404);
  await page.screenshot({path:evidence('05-projects.png'),fullPage:true});
  expect(issues).toEqual({errors:[],failures:[],consoleErrors:[]});expect(siteIssues).toEqual({errors:[],failures:[],consoleErrors:[]});
  fs.writeFileSync(evidence('browser-static.json'),JSON.stringify({production,v1,v2,issues,siteIssues,localhostDNS:'real Chromium resolution, no host resolver overrides'},null,2));
});

test('React Vite browser interaction, SPA direct navigation, MIME and origin boundary',async({page,context})=>{
  const issues=monitor(page);await login(page);
  await page.getByRole('button',{name:'새 프로젝트'}).click();
  await page.getByLabel('프로젝트 이름').fill('브라우저 시연 · React Counter');
  await page.getByLabel('프로젝트 주소 (slug)').fill('ui-react-'+Date.now());
  await upload(page,'react-vite-spa');
  await expect(page.getByLabel('실행 로그')).toContainText('v24.11.1');
  await expect(page.getByLabel('실행 로그')).toContainText('npm run build');
  const url=await page.getByRole('link',{name:'Preview 열기'}).getAttribute('href');
  const site=await context.newPage();const siteIssues=monitor(site);const assets:{url:string;type:string;status:number}[]=[];
  site.on('response',r=>{if(/\.(js|css)$/.test(r.url()))assets.push({url:r.url(),type:r.headers()['content-type'],status:r.status()})});
  await site.goto(url!);await site.getByRole('button',{name:'클릭 횟수: 0'}).click();await expect(site.getByRole('button',{name:'클릭 횟수: 1'})).toBeVisible();
  await site.getByRole('link',{name:'소개 페이지로 이동'}).click();await expect(site).toHaveURL(/\/about$/);
  await site.reload();await expect(site.getByText('/about',{exact:true})).toBeVisible();
  await site.getByRole('button',{name:'클릭 횟수: 0'}).click();await expect(site.getByRole('button',{name:'클릭 횟수: 1'})).toBeVisible();
  await site.screenshot({path:evidence('06-react-spa.png'),fullPage:true});
  expect(assets.some(a=>a.url.endsWith('.js')&&a.type.includes('javascript')&&a.status===200)).toBeTruthy();
  expect(assets.some(a=>a.url.endsWith('.css')&&a.type.includes('text/css')&&a.status===200)).toBeTruthy();
  expect(await browserStatus(context,url!+'/missing.js')).toBe(404);
  expect(await browserStatus(context,url!+'/api/projects')).toBe(404);
  const cors=await site.evaluate(async()=>{try{await fetch('http://localhost:3000/api/projects',{credentials:'include'});return 'readable'}catch{return 'blocked'}});
  expect(cors).toBe('blocked');
  // A deliberate cross-origin request above is expected to emit a browser CORS error.
  const unexpected=siteIssues.consoleErrors.filter(e=>!e.includes('CORS')&&!e.includes('Failed to load resource'));
  expect(issues).toEqual({errors:[],failures:[],consoleErrors:[]});expect(siteIssues.errors).toEqual([]);expect(unexpected).toEqual([]);
  fs.writeFileSync(evidence('browser-react.json'),JSON.stringify({url,assets,issues,siteIssues,cors},null,2));
  await page.screenshot({path:evidence('07-vite-deployment.png'),fullPage:true});
});

test('Node Express UI, live POST response, Preview isolation, promote and image rollback',async({page,context})=>{
  const issues=monitor(page);await login(page);
  await page.getByRole('button',{name:'새 프로젝트'}).click();
  await page.getByLabel('프로젝트 이름').fill('브라우저 시연 · Express HTTP');
  await page.getByLabel('프로젝트 주소 (slug)').fill('ui-node-'+Date.now());
  await upload(page,'node-express-v1');
  await expect(page.getByText('웹 서버 실행 중',{exact:true})).toBeVisible();
  await expect(page.getByLabel('실행 로그')).toContainText('npm ci');
  await page.getByRole('button',{name:'런타임 로그',exact:true}).click();
  await expect(page.getByLabel('실행 로그')).toContainText('NODE_READY v1');
  const v1=await page.getByRole('link',{name:'Preview 열기'}).getAttribute('href');
  const production=await page.getByRole('link',{name:'운영 사이트 열기'}).getAttribute('href');
  const site=await context.newPage();const siteIssues=monitor(site);
  await site.goto(v1!);await site.getByRole('button',{name:'서버에 인사하기'}).click();
  await expect(site.getByText('안녕하세요, Campus! v1 서버가 응답했습니다.')).toBeVisible();
  await site.screenshot({path:evidence('08-node-browser.png'),fullPage:true});
  await page.getByRole('button',{name:'새 버전 배포',exact:true}).click();await upload(page,'node-express-v2');
  const v2=await page.getByRole('link',{name:'Preview 열기'}).getAttribute('href');
  expect(v1).not.toBe(v2);
  await site.goto(production!);await expect(site.getByRole('heading',{name:'Express 서버 v1'})).toBeVisible();
  await site.goto(v2!);await site.getByRole('button',{name:'서버에 인사하기'}).click();
  await expect(site.getByText('안녕하세요, Campus! v2 서버가 응답했습니다.')).toBeVisible();
  await page.getByRole('button',{name:'운영 반영',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'운영 반영',exact:true}).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await site.goto(production!);await expect(site.getByRole('heading',{name:'Express 서버 v2'})).toBeVisible();
  await page.getByRole('button',{name:'이 버전으로 롤백',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'이 버전으로 롤백',exact:true}).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await site.reload();await expect(site.getByRole('heading',{name:'Express 서버 v1'})).toBeVisible();
  await site.getByRole('button',{name:'서버에 인사하기'}).click();
  await expect(site.getByText('안녕하세요, Campus! v1 서버가 응답했습니다.')).toBeVisible();
  expect(await browserStatus(context,production!+'/api/projects')).toBe(404);
  const v1Id=new URL(v1!).hostname.slice(2).split('.')[0].slice(0,8);
  await page.locator('.deployment-row').filter({hasText:v1Id}).click();
  await expect(page.getByLabel('실행 로그')).toContainText('NODE_READY v1');
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:evidence('09-node-runtime-logs.png'),fullPage:true});
  expect(issues.errors).toEqual([]);expect(siteIssues.errors).toEqual([]);
  expect(issues.failures).toEqual([]);expect(siteIssues.failures).toEqual([]);
  expect(issues.consoleErrors).toEqual([]);expect(siteIssues.consoleErrors).toEqual([]);
  fs.writeFileSync(evidence('browser-node.json'),JSON.stringify({production,v1,v2,issues,siteIssues,post:'real browser fetch POST /greet',localhostDNS:'real Chromium DNS'},null,2));
});
