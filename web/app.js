import {filterGames, candidatePool, randomIndex, hours, lastPlayed} from './domain.js';

const $ = id => document.getElementById(id);
const state = {data:null, selected:new Set(), drawn:new Set(), groups:new Set(), manual:false, history:[], busy:false, loading:false, limits:new Map(), collapsed:new Set()};
let filtered = [], request = 0, rollEpoch = 0, toastTimer;
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 3000);
}

function currentFilters() {
  return {search:$('search').value, groups:state.groups, installed:$('installed').value, playtime:$('playtime').value, recent:$('recent').value};
}

function pool() {
  return candidatePool(filtered, state.selected, state.manual, state.drawn, $('noRepeat').checked);
}

function renderPool() {
  const candidates = pool();
  $('poolBadge').textContent = `候选池 · ${candidates.length} 款`;
  $('selectedCount').textContent = state.selected.size;
  $('roundCount').textContent = `本轮已抽 ${state.drawn.size} 款`;
  $('roll').disabled = state.loading || state.busy || !candidates.length;
  $('again').disabled = state.loading || state.busy || !candidates.length;
  $('modeFiltered').classList.toggle('active', !state.manual);
  $('modeManual').classList.toggle('active', state.manual);
  $('modeFiltered').setAttribute('aria-pressed', String(!state.manual));
  $('modeManual').setAttribute('aria-pressed', String(state.manual));
  const selectedInView = filtered.filter(g => state.selected.has(g.id)).length;
  const base = state.manual ? selectedInView : filtered.length;
  $('poolHint').textContent = state.loading ? '正在读取本机游戏库…' : state.manual && !state.selected.size ? '点击游戏封面勾选候选，再来 ROLL。' : base && !candidates.length ? '当前候选已全部抽过，点击“重置本轮”即可重新开始。' : !candidates.length ? '当前条件下没有候选游戏，试着放宽筛选。' : state.manual ? `已手选 ${state.selected.size} 款，其中 ${selectedInView} 款符合当前筛选；每款等概率。` : `从当前筛选结果中等概率抽取，多分组中的相同游戏只算一次。`;
  $('selectVisible').disabled = state.loading || !filtered.length;
  $('clearSelected').disabled = !state.selected.size;
}

function imageSource(game) {
  return game.cover || ($('onlineCovers').checked ? game.onlineCover : null);
}

function card(game) {
  const selected = state.selected.has(game.id);
  return `<article class="game-card ${selected?'selected':''}" data-id="${game.id}"><button class="cover-button" data-select="${game.id}" aria-label="${selected?'取消选择':'选择'} ${escape(game.name)}" aria-pressed="${selected}"><span class="cover-fallback"><b>${escape(game.name)}</b><small>暂无本地封面</small></span>${imageSource(game)?`<img src="${escape(imageSource(game))}" alt="" loading="lazy" decoding="async">`:''}<span class="cover-check" aria-hidden="true">✓</span>${game.installed?'<span class="installed-badge">● 已安装</span>':''}</button><div class="game-info"><h3 class="game-name" title="${escape(game.name)}">${escape(game.name)}</h3><div class="game-meta"><span>◷ ${hours(game.minutes)}</span><span>${game.installed?'':'未安装'}</span></div><div class="game-date">最近游玩 · ${lastPlayed(game.lastPlayed)}</div></div></article>`;
}

function renderLibrary() {
  if (!state.data || state.loading) { renderPool(); return; }
  filtered = filterGames(state.data.games, currentFilters());
  const sort = $('sort').value;
  filtered.sort((a,b) => sort==='recent' ? b.lastPlayed-a.lastPlayed || a.id-b.id : sort==='timeDesc' ? b.minutes-a.minutes || a.id-b.id : sort==='timeAsc' ? a.minutes-b.minutes || a.id-b.id : a.name.localeCompare(b.name,'zh-CN'));
  $('visibleCount').textContent = filtered.length;
  $('filterDescription').textContent = state.groups.size ? `已选择 ${state.groups.size} 个分组 · ${filtered.length} 款不同游戏` : '按 Steam 分组展示 · 同一游戏可出现在多个分组';
  if (!filtered.length) {
    $('library').innerHTML = '<div class="empty"><h3>暂时没有符合条件的游戏</h3><p>试试清除分组、搜索或筛选条件。</p><button class="button subtle" data-reset>清除所有筛选</button></div>';
  } else {
    $('library').innerHTML = state.data.groups.filter(group => !state.groups.size || state.groups.has(group.id)).map(group => {
      const games = filtered.filter(game => game.groups.includes(group.id));
      if (!games.length) return '';
      const limit = state.limits.get(group.id) || 30;
      return `<details class="game-group" data-group="${escape(group.id)}" ${state.collapsed.has(group.id)?'':'open'}><summary>${escape(group.name)}${group.dynamic?' · 动态缓存':''}<b>${games.length} 款</b></summary><div class="game-grid">${games.slice(0,limit).map(card).join('')}</div>${games.length>limit?`<button class="load-more" data-more="${escape(group.id)}">继续显示 ${Math.min(30,games.length-limit)} 款 · 还有 ${games.length-limit} 款</button>`:''}</details>`;
    }).join('');
  }
  renderPool();
}

function renderGroups() {
  $('allGames').classList.toggle('active', !state.groups.size);
  $('groups').innerHTML = state.data.groups.map(group => `<label class="group-option"><input type="checkbox" value="${escape(group.id)}" ${state.groups.has(group.id)?'checked':''}><span class="group-name" title="${escape(group.name)}">${escape(group.name)}</span><b>${group.count}</b></label>`).join('');
}

function resetFilters() {
  state.groups.clear(); state.limits.clear();
  $('search').value = ''; for (const id of ['installed','playtime','recent']) $(id).value = 'all';
  if (state.data) renderGroups(); renderLibrary();
}

function showResult(game) {
  $('resultBody').innerHTML = `<p class="result-intro">就是它了 · YOUR NEXT GAME</p><div class="result-game">${imageSource(game)?`<img class="result-cover" src="${escape(imageSource(game))}" alt="${escape(game.name)} 封面">`:''}<div class="result-info"><h2>${escape(game.name)}</h2><p class="result-status">${game.installed?'● 已安装，随时开玩':'○ 尚未安装'}</p><p>游玩时长 · ${hours(game.minutes)}<br>最近游玩 · ${lastPlayed(game.lastPlayed)}</p><p>${escape(state.data.groups.filter(g => game.groups.includes(g.id)).map(g => g.name).join(' / '))}</p></div></div>`;
  if (!$('result').open) $('result').showModal();
}

function renderHistory() {
  $('historySection').hidden = !state.history.length;
  $('history').innerHTML = state.history.slice(0,12).map((entry,index) => `<button class="history-item" data-history="${index}">${imageSource(entry.game)?`<img src="${escape(imageSource(entry.game))}" alt="" loading="lazy">`:''}<span>${escape(entry.game.name)}<small>${entry.time} · ${hours(entry.game.minutes)}</small></span></button>`).join('');
}

async function roll() {
  if (state.loading || state.busy || !state.data) return;
  const candidates = pool();
  if (!candidates.length) { toast('候选池为空，请调整筛选或重置本轮。'); return; }
  const epoch = ++rollEpoch;
  state.busy = true; document.body.classList.add('rolling'); renderPool();
  const game = candidates[randomIndex(candidates.length)];
  await new Promise(resolve => setTimeout(resolve, matchMedia('(prefers-reduced-motion: reduce)').matches?0:450));
  if (epoch !== rollEpoch) return;
  state.busy = false; document.body.classList.remove('rolling');
  state.drawn.add(game.id);
  state.history.unshift({game,time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})});
  state.history = state.history.slice(0,30);
  showResult(game); renderPool(); renderHistory();
}

async function load(account, refresh = true) {
  const currentRequest = ++request;
  ++rollEpoch; state.busy = false; document.body.classList.remove('rolling');
  state.loading = true; filtered = []; state.data = null;
  $('result').close(); $('resultBody').replaceChildren();
  // A refresh must not leave hidden or removed games visible in the previous result/history.
  state.history = []; renderHistory();
  $('library').innerHTML = '<div class="loading"><span class="spinner"></span><h3>正在打开你的游戏库</h3><p>只读获取分组、封面与游玩记录…</p></div>';
  $('groups').replaceChildren(); $('notice').hidden = true; $('library').setAttribute('aria-busy','true');
  $('account').disabled = true; $('refresh').disabled = true; renderPool();
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(),60000);
  try {
    const query = new URLSearchParams({refresh:refresh?'1':'0'}); if (account) query.set('account',account);
    const response = await fetch(`/api/library?${query}`,{signal:controller.signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '读取失败');
    if (currentRequest !== request) return;
    state.data = data;
    const ids = new Set(data.games.map(g => g.id));
    state.selected = new Set([...state.selected].filter(id => ids.has(id)));
    state.drawn = new Set([...state.drawn].filter(id => ids.has(id)));
    state.groups = new Set([...state.groups].filter(id => data.groups.some(g => g.id === id)));
    $('account').innerHTML = data.accounts.map(a => `<option value="${a.id}">${escape(a.name)}${a.active?' · 当前':''}</option>`).join('');
    $('account').value = data.account;
    $('allCount').textContent = data.games.length; $('totalStat').textContent = data.games.length;
    $('installedStat').textContent = data.games.filter(g => g.installed).length;
    $('sourceNote').textContent = data.sourceNote;
    $('updatedAt').textContent = `更新于 ${new Date(data.updatedAt*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}`;
    $('notice').className = 'notice'; $('notice').hidden = !data.warnings.length;
    $('notice').innerHTML = data.warnings.map(w => `<p>${escape(w)}</p>`).join('');
    renderGroups();
  } catch (error) {
    if (currentRequest !== request) return;
    state.data = null; filtered = [];
    $('notice').className = 'notice error'; $('notice').hidden = false;
    $('notice').textContent = error.name==='AbortError'?'读取超时，请稍后刷新。':error.message;
    $('library').innerHTML = '<div class="empty"><h3>暂时无法读取游戏库</h3><p>请确认 Steam 已登录，打开游戏库完成同步，然后点击右上角刷新。</p></div>';
    for (const id of ['allCount','totalStat','installedStat','visibleCount']) $(id).textContent = '—';
  } finally {
    clearTimeout(timeout);
    if (currentRequest === request) {
      state.loading = false; $('account').disabled = !$('account').value || !state.data;
      $('refresh').disabled = false; $('library').setAttribute('aria-busy','false');
      renderLibrary(); renderPool();
    }
  }
}

$('groups').addEventListener('change', event => {
  const {value,checked} = event.target;
  checked ? state.groups.add(value) : state.groups.delete(value);
  $('allGames').classList.toggle('active',!state.groups.size); state.limits.clear(); renderLibrary();
});
for (const id of ['allGames','clearGroups']) $(id).addEventListener('click',() => {state.groups.clear(); if(state.data) renderGroups();renderLibrary();});
for (const id of ['search','installed','playtime','recent','sort']) $(id).addEventListener(id==='search'?'input':'change',() => {state.limits.clear();renderLibrary();});
$('resetFilters').addEventListener('click',resetFilters);
$('noRepeat').addEventListener('change',renderPool);
$('modeFiltered').addEventListener('click',() => {state.manual=false;renderPool();});
$('modeManual').addEventListener('click',() => {state.manual=true;renderPool();});
$('resetRound').addEventListener('click',() => {state.drawn.clear();renderPool();toast('已开启新一轮，每款游戏都可以再次抽到。');});
$('clearSelected').addEventListener('click',() => {state.selected.clear();renderLibrary();});
$('selectVisible').addEventListener('click',() => {filtered.forEach(g => state.selected.add(g.id));state.manual=true;renderLibrary();toast(`已将 ${filtered.length} 款筛选结果加入手选。`);});
$('library').addEventListener('click',event => {
  const select = event.target.closest('[data-select]');
  if (select) {
    const id = Number(select.dataset.select);
    state.selected.has(id)?state.selected.delete(id):state.selected.add(id);
    document.querySelectorAll(`[data-select="${id}"]`).forEach(button => {
      const selected = state.selected.has(id);
      button.closest('.game-card').classList.toggle('selected',selected); button.setAttribute('aria-pressed',String(selected));
      const game=state.data.games.find(g=>g.id===id);button.setAttribute('aria-label',`${selected?'取消选择':'选择'} ${game.name}`);
    });
    renderPool();
  }
  const more=event.target.closest('[data-more]');
  if(more){const id=more.dataset.more;state.limits.set(id,(state.limits.get(id)||30)+30);renderLibrary();}
  if(event.target.closest('[data-reset]'))resetFilters();
});
$('library').addEventListener('toggle', event => {
  if(event.target.matches('details[data-group]')) {
    event.target.open?state.collapsed.delete(event.target.dataset.group):state.collapsed.add(event.target.dataset.group);
  }
},true);
document.addEventListener('error',event => {if(event.target.tagName==='IMG')event.target.hidden=true;},true);
$('history').addEventListener('click',event => {const button=event.target.closest('[data-history]');if(button)showResult(state.history[Number(button.dataset.history)].game);});
for(const id of ['roll','again'])$(id).addEventListener('click',roll);
for(const id of ['closeResult','done'])$(id).addEventListener('click',()=>$('result').close());
$('result').addEventListener('click',event=>{if(event.target===$('result')){const r=$('result').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)$('result').close();}});
$('refresh').addEventListener('click',()=>load($('account').value||undefined));
$('account').addEventListener('change',()=>{state.selected.clear();state.drawn.clear();state.groups.clear();state.limits.clear();state.collapsed.clear();load($('account').value);});
$('stop').addEventListener('click',async()=>{
  try{
    const health=await (await fetch('/api/health')).json();
    const response=await fetch('/api/shutdown',{method:'POST',headers:{'X-Steam-Roll-Token':health.token}});
    if(!response.ok)throw new Error('服务退出失败');
    ++request;++rollEpoch;state.loading=true;state.data=null;filtered=[];state.history=[];state.selected.clear();state.drawn.clear();renderHistory();renderPool();
    $('result').close();$('resultBody').replaceChildren();$('groups').replaceChildren();
    $('library').innerHTML='<div class="empty"><h3>本地服务已退出</h3><p>关闭此网页即可。下次双击“启动 Steam Roll.cmd”重新打开。</p></div>';
    $('notice').hidden=true;for(const id of ['refresh','account','stop'])$(id).disabled=true;
    toast('服务已退出，Steam 数据未作修改。');
  }catch(error){toast(`无法退出服务：${error.message}`);}
});
load();

$('onlineCovers').addEventListener('change',()=>{renderLibrary();renderHistory();toast($('onlineCovers').checked?'已允许从 Steam 官方 CDN 补全缺失封面。':'已恢复只用本地封面。');});
