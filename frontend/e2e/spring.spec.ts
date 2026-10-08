import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
const root=path.resolve('..');

test('Spring GitHub -> real signup/login, product, persistent upload and application API',async({page,context})=>{
  test.setTimeout(900000);
  page.setDefaultTimeout(30000);
  page.setDefaultNavigationTimeout(45000);
  await page.goto('/');
  await page.getByLabel('관리자 암호').fill(fs.readFileSync(path.join(root,'.secrets/admin-password.txt'),'utf8').trim());
  await page.getByRole('button',{name:'워크스페이스 열기'}).click();
  await page.getByRole('button',{name:'새 프로젝트'}).click();
  await page.getByLabel('GitHub 저장소 주소',{exact:true}).fill('https://github.com/Gandalem/aurashop.git');
  await page.getByRole('button',{name:'소스 검사',exact:true}).click();
  await expect(page.getByText('지원되는 프로젝트입니다')).toBeVisible({timeout:120000});
  await expect(page.getByText('SPRING_BOOT · Java 21 + Vite + MySQL + Redis',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'배포하기'}).click();
  await expect(page.getByRole('link',{name:'운영 사이트 열기'})).toBeVisible({timeout:720000});
  await expect(page.getByText('웹 서버 실행 중',{exact:true})).toBeVisible();
  const url=(await page.getByRole('link',{name:'운영 사이트 열기'}).getAttribute('href'))!;
  const projectId=new URL(page.url()).hash.slice(1);
  console.log('Spring site READY:',url);
  const site=await context.newPage();
  site.setDefaultTimeout(30000);site.setDefaultNavigationTimeout(45000);
  const errors:string[]=[];
  site.on('pageerror',e=>errors.push(e.message));
  expect((await site.goto(url,{waitUntil:'domcontentloaded'}))!.status()).toBe(200);
  await expect(site.getByRole('heading',{name:'ALL PRODUCTS'})).toBeVisible();
  const stamp=Date.now(), email=`campus-browser-${stamp}@example.test`, password=`Test-${crypto.randomUUID()}!`;
  await site.goto(url+'/signup',{waitUntil:'domcontentloaded'});
  await expect(site.getByRole('heading',{name:'CREATE ACCOUNT'})).toBeVisible();
  // The original form requires the external Daum address picker. Exercise the
  // real signup API with a test address; do not mock that external service.
  const signup=await site.evaluate(async({email,password})=>(await fetch('/api/auth/signup',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email,password,name:'Campus Test',phone:'01000000000',address:'테스트 주소'})
  })).status,{email,password});
  expect(signup).toBe(200);
  console.log('Signup API PASS');
  await site.goto(url+'/signin',{waitUntil:'domcontentloaded'});
  await site.getByLabel('Email',{exact:true}).fill(email);
  await site.getByLabel('Password',{exact:true}).fill(password);
  const login=site.waitForResponse(r=>r.url().endsWith('/api/auth/login')&&r.request().method()==='POST');
  await site.getByRole('button',{name:'SIGN IN',exact:true}).click();
  expect((await login).status()).toBe(200);
  console.log('Login PASS');
  await expect(site).toHaveURL(url+'/');
  const productName='Campus Deploy 검증 상품 '+stamp;
  const application=await site.evaluate(async(productName)=>{
    const token=localStorage.getItem('accessToken')!;
    const auth={Authorization:`Bearer ${token}`};
    const png=Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC'),c=>c.charCodeAt(0));
    const form=new FormData();form.append('file',new Blob([png],{type:'image/png'}),'campus-test.png');
    const upload=await fetch('/api/files/upload',{method:'POST',headers:auth,body:form});
    const imageUrl=await upload.text();
    const created=await fetch('/api/products',{method:'POST',headers:{...auth,'Content-Type':'application/json'},body:JSON.stringify({categoryId:1,name:productName,price:1000,stockQuantity:5,description:'실제 MySQL 저장 검사',imageUrls:[imageUrl]})});
    const product=await created.json();
    const cart=await fetch('/api/cart',{method:'POST',headers:{...auth,'Content-Type':'application/json'},body:JSON.stringify({productId:product.id,quantity:1})});
    const refresh=await fetch('/api/auth/reissue',{method:'POST',credentials:'include'});
    return {upload:upload.status,imageUrl,product:created.status,productId:product.id,cart:cart.status,refresh:refresh.status};
  },productName);
  expect(application.upload).toBe(200);expect(application.imageUrl).toMatch(/^\/uploads\//);
  expect(application.product).toBe(200);expect(application.cart).toBe(200);expect(application.refresh).toBe(200);
  await site.reload({waitUntil:'domcontentloaded'});
  await expect(site.getByRole('heading',{name:productName,exact:true})).toBeVisible();
  const productImage=site.getByRole('img',{name:productName,exact:true});
  await expect(productImage).toBeVisible();
  await expect.poll(()=>productImage.evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBe(1);
  await site.screenshot({path:path.join(root,'evidence/spring-browser.png'),fullPage:true});
  await page.screenshot({path:path.join(root,'evidence/spring-dashboard.png'),fullPage:true});
  expect(errors).toEqual([]);
  fs.writeFileSync(path.join(root,'evidence/browser-spring.json'),JSON.stringify({url,projectId,source:'GitHub URL only',checks:['signup page + real API','login UI','MySQL product/cart','Redis refresh token','relative upload URL','image pixels','no page errors'],not_tested:['external Daum address selection','payment'],application},null,2));
});
