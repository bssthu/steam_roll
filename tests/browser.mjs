import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';

const base='http://127.0.0.1:17863';
await mkdir('artifacts',{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1080},reducedMotion:'reduce'});
const page=await context.newPage();
const errors=[];const external=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('request',request=>{if(!request.url().startsWith(base))external.push(request.url());});
const response=page.waitForResponse(r=>r.url().includes('/api/library')&&r.ok());
await page.goto(base);const data=await (await response).json();
await page.locator('.game-card').first().waitFor();
await page.waitForFunction(()=>[...document.querySelectorAll('.game-grid img')].filter(img=>img.getBoundingClientRect().top<innerHeight+100).every(img=>img.complete));
assert(data.games.length>0);
assert.equal(await page.locator('#totalStat').textContent(),String(data.games.length));
await page.screenshot({path:'artifacts/desktop.png',fullPage:false});

await page.selectOption('#installed','yes');
assert.equal(Number(await page.locator('#visibleCount').textContent()),data.games.filter(g=>g.installed).length);
await page.selectOption('#recent','old180');
const old=data.games.filter(g=>g.installed&&g.lastPlayed&&(Date.now()/1000-g.lastPlayed)>=180*86400);
assert.equal(Number(await page.locator('#visibleCount').textContent()),old.length);
await page.click('#resetFilters');

const groups=data.groups.filter(g=>g.count>0).slice(0,2);
for(const group of groups)await page.locator('#groups input').filter({}).evaluateAll((elements,id)=>{const el=elements.find(x=>x.value===id);el.click();},group.id);
assert.equal(Number(await page.locator('#visibleCount').textContent()),data.games.filter(g=>g.groups.some(id=>groups.some(x=>x.id===id))).length);
await page.click('#resetFilters');

const game=data.games.find(g=>g.installed)||data.games[0];
await page.fill('#search',String(game.id));
await page.locator(`[data-select="${game.id}"]`).first().click();
await page.click('#modeManual');
assert.match(await page.locator('#poolBadge').textContent(),/1 款/);
await page.click('#roll');
await page.locator('#result[open]').waitFor();
assert.equal(await page.locator('.result-info h2').textContent(),game.name);
assert.equal(await page.locator('#again').isDisabled(),true);
await page.screenshot({path:'artifacts/result.png'});
await page.click('#closeResult');
await page.click('#resetRound');
assert.equal(await page.locator('#roll').isDisabled(),false);
await page.click('#clearSelected');
assert.equal(await page.locator('#roll').isDisabled(),true);

await page.click('#modeFiltered');await page.click('#resetFilters');
await page.fill('#search','<img src=x onerror=alert(1)>');
assert.equal(await page.locator('.game-card').count(),0);
assert.equal(await page.locator('#roll').isDisabled(),true);
await page.click('#resetFilters');
await page.locator('#toast').evaluate(element=>element.hidden=true);
await page.setViewportSize({width:390,height:844});
await page.screenshot({path:'artifacts/mobile.png',fullPage:false});
assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'Mobile page overflows horizontally');
await page.setViewportSize({width:1440,height:1080});

// A failed refresh clears old results and candidates, and recovery works.
await page.route('**/api/library?**',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'test unavailable'})}));
await page.click('#refresh');await page.locator('.notice.error').waitFor();
assert.equal(await page.locator('#roll').isDisabled(),true);
assert.equal(await page.locator('.game-card').count(),0);
assert.equal(await page.locator('.history-item').count(),0);
await page.unroute('**/api/library?**');
await page.click('#refresh');await page.locator('.game-card').first().waitFor();
assert.equal(errors.length,0,errors.join('\n'));
assert.equal(external.length,0,external.join('\n'));
// Account switches clear the previous account's manual picks and history.
if(data.accounts.length>1){
  const other=data.accounts.find(a=>a.id!==data.account);
  const switched=page.waitForResponse(r=>r.url().includes('/api/library')&&r.ok());
  await page.selectOption('#account',other.id);const otherData=await (await switched).json();
  await page.waitForFunction(()=>document.getElementById('refresh').disabled===false);
  assert.equal(await page.locator('#selectedCount').textContent(),'0');
  assert.equal(Number(await page.locator('#totalStat').textContent()),otherData.games.length);
  const returned=page.waitForResponse(r=>r.url().includes('/api/library')&&r.ok());
  await page.selectOption('#account',data.account);await returned;
  await page.locator('.game-card').first().waitFor();
}
// Test opt-in image routing independently of external network availability.
const defaultExternalRequests=external.length;
const missing=data.games.find(g=>!g.cover);
if(missing){
  await page.route('https://shared.fastly.steamstatic.com/**',route=>route.fulfill({status:200,contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=','base64')}));
  await page.fill('#search',String(missing.id));await page.check('#onlineCovers');
  assert.equal(await page.locator(`[data-id="${missing.id}"] img`).first().getAttribute('src'),missing.onlineCover);
  await page.uncheck('#onlineCovers');
  assert.equal(await page.locator(`[data-id="${missing.id}"] img`).count(),0);
}
assert.equal(errors.length,0,errors.join('\n'));
const result={status:'BROWSER_PASS',browser:'Microsoft Edge (headless)',games:data.games.length,installed:data.games.filter(g=>g.installed).length,covers:data.games.filter(g=>g.cover).length,errors,defaultExternalRequests,optInMockImageRequests:external.length-defaultExternalRequests};
await writeFile('artifacts/browser-result.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
await browser.close();
