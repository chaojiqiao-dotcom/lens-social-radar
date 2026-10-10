const fmt = new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 1 });
let dataset = null;
let archiveDataset = null;
let searchPlatform = 'all';
let searchTimer = null;

function esc(v='') {
  return String(v).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}
function n(v){ return Number(v || 0); }
function pct(v){
  if(v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  return `${Number(v).toFixed(2)}%`;
}
function metricKnown(x,key){
  const availability = x?.metric_availability;
  if(availability && Object.prototype.hasOwnProperty.call(availability,key)) return Boolean(availability[key]);
  return x?.[key] !== null && x?.[key] !== undefined;
}
function metricText(x,key){ return metricKnown(x,key) ? fmt.format(n(x[key])) : '—'; }
function shareSaveText(x){
  const hasShare = metricKnown(x,'shares');
  const hasSave = metricKnown(x,'saves');
  if(!hasShare && !hasSave) return '—';
  return fmt.format((hasShare?n(x.shares):0)+(hasSave?n(x.saves):0));
}
function isRankEligible(x){ return x?.ranking_eligible !== false; }
function heatText(x){
  const value = x?.heat_score ?? x?.score;
  return value === null || value === undefined ? '—' : Number(value).toFixed(1);
}
function confidenceText(x){ return x?.data_confidence === null || x?.data_confidence === undefined ? '—' : `${Number(x.data_confidence).toFixed(0)}%`; }
function platformLabel(p){ return ({instagram:'Instagram',tiktok:'TikTok',pinterest:'Pinterest'})[p] || p; }
function arr(v){ return Array.isArray(v) ? v : (v ? [v] : []); }
function normalizeText(v=''){ return String(v).toLowerCase().normalize('NFKC').trim(); }

function renderKpis(data){
  const posts = data.posts || [];
  const eligible = posts.filter(isRankEligible);
  const views = eligible.reduce((a,x)=>a+(metricKnown(x,'views')?n(x.views):0),0);
  const top = eligible[0] || {};
  const cards = [
    ['采集内容', fmt.format(data.stats?.posts || posts.length), '本周进入案例库'],
    ['可入热榜', fmt.format(data.stats?.ranking_eligible || eligible.length), '通过数据质量门槛'],
    ['有效触达', fmt.format(views), '仅统计已获取播放/曝光的数据'],
    ['当前总榜 TOP 1', esc(top.brand || '—'), top.platform ? `${platformLabel(top.platform)} · 热度 ${heatText(top)}` : '等待有效样本'],
  ];
  document.querySelector('#kpis').innerHTML = cards.map(([a,b,c])=>`<article class="kpi"><div class="kpi-label">${a}</div><div class="kpi-value">${b}</div><div class="kpi-sub">${c}</div></article>`).join('');
}

function getTopAnalysis(rank){
  return (dataset.analysis?.top3_analysis || []).find(x=>Number(x.rank)===Number(rank)) || {};
}

function accessLabel(x){
  const mode = String(x.access_mode || '').toLowerCase();
  if(mode === 'mobile' || mode === 'mobile_only') return '建议手机打开';
  if(mode === 'local') return '本地素材';
  if(mode === 'demo') return 'DEMO';
  return '';
}

function posterInner(x){
  const label = `${platformLabel(x.platform)} · ${x.brand || 'Post'}`;
  if(x.thumbnail){
    return `<img src="${esc(x.thumbnail)}" alt="${esc(label)} 首帧/封面" loading="lazy" onerror="this.remove();this.parentElement.classList.add('image-error')">`;
  }
  return '';
}

function coverMarkup(x, rankLabel, compact=false){
  const tag = x.url ? 'a' : 'div';
  const href = x.url ? ` href="${esc(x.url)}" target="_blank" rel="noopener noreferrer"` : '';
  const access = accessLabel(x);
  return `<${tag} class="post-cover ${x.url ? 'clickable' : 'no-link'} ${compact ? 'compact-cover' : ''} platform-${esc(x.platform)}"${href} aria-label="${x.url ? '打开原帖' : '示例内容，无原帖链接'}">
    ${posterInner(x)}
    <div class="poster-fallback"><div class="iris-mark"><i></i></div><span>${esc(platformLabel(x.platform))}</span></div>
    <span class="cover-rank">${esc(rankLabel)}</span>
    ${access ? `<span class="access-badge">${esc(access)}</span>` : ''}
    <span class="cover-action">${x.url ? '打开原帖 ↗' : '示例封面'}</span>
  </${tag}>`;
}

function platformPosts(platform){
  const rows = (dataset.posts || []).filter(x=>x.platform===platform && isRankEligible(x));
  rows.sort((a,b)=>{
    const ar = n(a.platform_rank) || 9999;
    const br = n(b.platform_rank) || 9999;
    if(ar !== br) return ar - br;
    return n(b.score)-n(a.score);
  });
  return rows.slice(0,10);
}

function renderPlatformBoards(){
  const platforms = ['instagram','tiktok'];
  document.getElementById('platformBoards').innerHTML = platforms.map(platform=>{
    const posts = platformPosts(platform);
    const collected = (dataset.posts || []).filter(x=>x.platform===platform).length;
    const excluded = Math.max(0,collected-posts.length);
    const cards = posts.map((x,i)=>{
      const rank = n(x.platform_rank) || i+1;
      return `<article class="rank-card">
        ${coverMarkup(x, `#${rank}`)}
        <div class="rank-card-body">
          <div class="rank-card-top"><span class="platform-pill">${esc(platformLabel(platform))}</span><span class="score-number">热度 ${heatText(x)}</span></div>
          <h3>${esc(x.brand || 'Unknown')}</h3>
          <p class="card-caption">${esc(x.caption || '—')}</p>
          <div class="card-author">${esc(x.author || '')} · ${esc(x.region || '')}</div>
          <div class="data-quality-row"><span>数据完整度 ${confidenceText(x)}</span><span>可入榜</span></div>
          <div class="card-metrics">
            <span><b>${metricText(x,'views')}</b> 触达</span>
            <span><b>${pct(x.engagement_rate)}</b> 互动率</span>
            <span><b>${shareSaveText(x)}</b> 分享+收藏</span>
          </div>
        </div>
      </article>`;
    }).join('');
    return `<section class="platform-board platform-board-${platform}">
      <div class="platform-board-head">
        <div><div class="eyebrow">${esc(platformLabel(platform).toUpperCase())} WEEKLY</div><h3>${esc(platformLabel(platform))} TOP 10</h3></div>
        <div class="board-count">${posts.length} / 10${excluded ? ` · ${excluded} 条仅入案例库` : ''}</div>
      </div>
      <div class="rank-gallery">${cards || '<div class="empty-state">本周暂无有效数据。</div>'}</div>
    </section>`;
  }).join('');
}

function renderPodium(){
  const top = (dataset.posts || []).filter(isRankEligible).slice(0,3);
  document.querySelector('#podium').innerHTML = top.map((x,i)=>{
    const a = getTopAnalysis(x.rank);
    const link = x.url ? `<a class="winner-link" target="_blank" rel="noopener noreferrer" href="${esc(x.url)}">打开原帖 ↗</a>` : '';
    return `<article class="winner rank-${i+1}" data-rank="0${i+1}">
      <div class="winner-media">${coverMarkup(x, `总榜 #${x.rank}`, true)}</div>
      <div class="winner-content">
        <div class="winner-head"><span class="rank-chip">#${x.rank}</span><span class="platform-chip">${esc(platformLabel(x.platform))} · 平台#${esc(x.platform_rank || '—')} · ${esc(x.region)}</span></div>
        <h3>${esc(x.brand)}</h3>
        <p class="headline">${esc(a.headline || x.caption || '')}</p>
        <div class="mini-metrics">
          <div class="mini-metric"><label>平台热度分</label><strong>${heatText(x)}</strong></div>
          <div class="mini-metric"><label>数据完整度</label><strong>${confidenceText(x)}</strong></div>
          <div class="mini-metric"><label>触达</label><strong>${metricText(x,'views')}</strong></div>
        </div>
        <div class="deep">
          <div class="deep-block"><b>为什么火</b><p>${esc(a.why_it_won || '—')}</p></div>
          <div class="deep-block"><b>画面 / 节奏 / 信息结构</b><p>${esc(a.creative_breakdown || x.media_notes || '—')}</p></div>
          <div class="deep-block"><b>值得复制</b><p>${esc(a.what_to_copy || '—')}</p></div>
          <div class="deep-block"><b>判断风险</b><p>${esc(a.risk || '—')}</p></div>
          ${link}
        </div>
      </div>
    </article>`;
  }).join('');
}

function renderSignals(){
  document.getElementById('weeklySummary').textContent = dataset.analysis?.weekly_summary || '—';
  document.getElementById('signals').innerHTML=(dataset.analysis?.signals || []).map((x,i)=>`<div class="signal"><b>SIGNAL 0${i+1}</b>${esc(x)}</div>`).join('');
  document.getElementById('regionalNote').textContent=dataset.analysis?.regional_note || '—';
}
function renderBars(target, obj){
  const entries=Object.entries(obj||{}).sort((a,b)=>b[1]-a[1]);
  const max=Math.max(1,...entries.map(x=>n(x[1])));
  document.getElementById(target).innerHTML=entries.map(([k,v])=>`<div class="bar-row"><span>${esc(k)}</span><span class="bar-track"><i class="bar-fill" style="display:block;width:${(n(v)/max*100).toFixed(1)}%"></i></span><span class="bar-value">${v}</span></div>`).join('');
}

// ---------- Search / case library ----------
function searchAliases(x){
  const region = String(x.region || '');
  const type = String(x.content_type || '');
  const aliases = [];
  if(region === 'Southeast Asia') aliases.push('东南亚','东南亚市场','sea');
  if(region === 'Europe & North America') aliases.push('欧美','欧洲','北美','欧美市场');
  if(region === 'Middle East') aliases.push('中东','中东市场','middle east');
  if(type === 'video' || type === 'reel') aliases.push('视频','短视频');
  if(type === 'carousel') aliases.push('轮播','图文','多图');
  if(type === 'pin') aliases.push('图片','pin','pinterest图片');
  if(x.platform === 'instagram') aliases.push('ins','ig','instagram','reels');
  if(x.platform === 'tiktok') aliases.push('tiktok','tt','抖音海外版');
  if(x.platform === 'pinterest') aliases.push('pinterest','pin');
  return aliases;
}

function searchableParts(x){
  const tagFields = ['hashtags','search_tags','visual_tags','shooting_tags','editing_tags','hook_tags'];
  const tags = tagFields.flatMap(k=>arr(x[k]));
  return {
    primary: [x.brand,x.author,x.caption,x.platform,platformLabel(x.platform),x.region,x.content_type].filter(Boolean),
    tags,
    other: [x.media_notes,x.week_start,x.week_end,...searchAliases(x)].filter(Boolean),
  };
}

function searchHaystack(x){
  const p = searchableParts(x);
  return normalizeText([...p.primary,...p.tags,...p.other].join(' | '));
}

function queryTokens(q){
  return normalizeText(q).split(/[\s,+，/|]+/).filter(Boolean);
}

function relevanceForSearch(x, query){
  const q = normalizeText(query);
  const tokens = queryTokens(query);
  const parts = searchableParts(x);
  const primary = normalizeText(parts.primary.join(' | '));
  const tags = parts.tags.map(normalizeText);
  const hay = searchHaystack(x);
  if(!tokens.length) return -1;
  if(!tokens.every(t=>hay.includes(t))) return -1;
  let score = 0;
  if(primary.includes(q)) score += 22;
  if(normalizeText(x.brand).includes(q)) score += 18;
  if(normalizeText(x.author).includes(q)) score += 14;
  if(normalizeText(x.caption).includes(q)) score += 12;
  for(const t of tokens){
    if(tags.some(tag=>tag === t)) score += 14;
    else if(tags.some(tag=>tag.includes(t))) score += 9;
    if(primary.includes(t)) score += 5;
  }
  score += isRankEligible(x) ? Math.min(5, n(x.heat_score ?? x.score)/20) : 0;
  return score;
}

function matchedTags(x, query){
  const tokens = queryTokens(query);
  const tags = ['search_tags','visual_tags','shooting_tags','editing_tags','hook_tags','hashtags'].flatMap(k=>arr(x[k]));
  const matched = tags.filter(tag=>tokens.some(t=>normalizeText(tag).includes(t) || t.includes(normalizeText(tag))));
  return [...new Set(matched)].slice(0,5);
}

function formatWeek(x){
  if(x.week_start && x.week_end) return `${x.week_start} → ${x.week_end}`;
  const d = String(x.posted_at || '').slice(0,10);
  return d || '历史案例';
}

function renderSearchResults(query){
  const wrap = document.getElementById('searchResults');
  const meta = document.getElementById('searchMeta');
  const q = String(query || '').trim();
  if(!q){
    wrap.innerHTML = '';
    meta.innerHTML = `<span>可搜索：品牌 / 账号 / 地区 / 美瞳颜色 / 拍摄手法 / 剪辑手法 / Hook / 内容形式</span><b>${fmt.format((archiveDataset?.posts || dataset?.posts || []).length)} 条案例已建立索引</b>`;
    return;
  }
  const source = archiveDataset?.posts || dataset?.posts || [];
  let results = source
    .filter(x=>searchPlatform === 'all' || x.platform === searchPlatform)
    .map(x=>({x, r:relevanceForSearch(x,q)}))
    .filter(row=>row.r >= 0)
    .sort((a,b)=>b.r-a.r || n(b.x.score)-n(a.x.score));
  const total = results.length;
  results = results.slice(0,24);
  meta.innerHTML = `<span>“${esc(q)}” 找到 <b>${total}</b> 条匹配${searchPlatform==='all'?'':` · ${esc(platformLabel(searchPlatform))}`}</span><b>按关键词相关度 + 内容综合分排序</b>`;
  if(!results.length){
    wrap.innerHTML = `<div class="search-empty"><strong>没有找到对应案例</strong><span>试试更短的词，例如“自然光”“眼部特写”“OLENS”“Jump Cut”或切换到“全部平台”。</span></div>`;
    return;
  }
  wrap.innerHTML = `<div class="search-result-grid">${results.map(({x})=>{
    const tags = matchedTags(x,q);
    const rank = x.platform_rank ? `${platformLabel(x.platform)} #${x.platform_rank}` : platformLabel(x.platform);
    return `<article class="search-result-card">
      ${coverMarkup(x, rank)}
      <div class="search-result-body">
        <div class="search-result-kicker"><span>${esc(platformLabel(x.platform))}</span><span>${esc(formatWeek(x))}</span></div>
        <h3>${esc(x.brand || 'Unknown')}</h3>
        <p>${esc(x.caption || '—')}</p>
        <div class="search-result-author">${esc(x.author || '')} · ${esc(x.region || '')}</div>
        <div class="search-tags">${(tags.length?tags:arr(x.search_tags).slice(0,4)).map(t=>`<span>${esc(t)}</span>`).join('')}</div>
        <div class="search-result-metrics"><span>${metricText(x,'views')} 触达</span><span>${pct(x.engagement_rate)} 互动</span><span>${isRankEligible(x) ? `热度 ${heatText(x)}` : '仅案例库'}</span></div>
      </div>
    </article>`;
  }).join('')}</div>${total>24?`<div class="search-more">当前先展示相关度最高的 24 条，共 ${total} 条匹配。</div>`:''}`;
}

function runSearch(){
  const input = document.getElementById('globalSearch');
  renderSearchResults(input?.value || '');
}

function setupSearch(){
  const input = document.getElementById('globalSearch');
  if(!input) return;
  input.addEventListener('input', ()=>{
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 140);
  });
  input.addEventListener('keydown', e=>{
    if(e.key === 'Escape'){
      input.value='';
      runSearch();
      input.blur();
    }
  });
  document.getElementById('searchClear')?.addEventListener('click', ()=>{
    input.value='';
    runSearch();
    input.focus();
  });
  document.querySelectorAll('[data-search-query]').forEach(btn=>btn.addEventListener('click', ()=>{
    input.value=btn.dataset.searchQuery || '';
    runSearch();
    input.focus();
  }));
  document.querySelectorAll('[data-search-platform]').forEach(btn=>btn.addEventListener('click', ()=>{
    searchPlatform=btn.dataset.searchPlatform || 'all';
    document.querySelectorAll('[data-search-platform]').forEach(x=>x.classList.toggle('active',x===btn));
    runSearch();
  }));
  renderSearchResults('');
}

async function loadArchive(){
  if(window.LENS_RADAR_ARCHIVE){
    archiveDataset = window.LENS_RADAR_ARCHIVE;
    return;
  }
  try{
    const res=await fetch('data/archive.json',{cache:'no-store'});
    if(res.ok) archiveDataset=await res.json();
  }catch(_err){
    archiveDataset = {posts: dataset?.posts || []};
  }
  if(!archiveDataset) archiveDataset = {posts: dataset?.posts || []};
}

async function boot(){
  try{
    if (window.LENS_RADAR_DATA) {
      dataset = window.LENS_RADAR_DATA;
    } else {
      const res=await fetch('data/weekly.json',{cache:'no-store'});
      if(!res.ok) throw new Error(`HTTP ${res.status}`);
      dataset=await res.json();
    }
    await loadArchive();
    document.getElementById('dateRange').textContent=`${dataset.meta.date_start} → ${dataset.meta.date_end}`;
    if(dataset.meta.is_demo) document.getElementById('demoBadge').classList.remove('hidden');
    document.getElementById('generatedAt').textContent=`Generated ${new Date(dataset.meta.generated_at).toLocaleString('zh-CN')}`;
    setupSearch();
    renderKpis(dataset);
    renderSignals();
    renderPlatformBoards();
    renderPodium();
    renderBars('regionBars', dataset.stats?.regions);
    renderBars('typeBars', dataset.stats?.content_types);
  }catch(err){
    document.body.innerHTML=`<div style="font-family:sans-serif;padding:40px"><h2>无法读取周报数据</h2><p>${esc(err.message)}</p><p>请先运行 <code>python scripts/weekly_update.py --mode demo</code>。</p></div>`;
  }
}
boot();
