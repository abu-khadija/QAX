/* QAX web app — the two tracks (scan, functional QA E2E). Loaded by app.html. */
const $ = id => document.getElementById(id);
const esc = s => String(s==null?"":s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
/* /api calls carry X-QAX-Token when the server requires one (QAX_API_TOKEN). The token is asked
   once, kept for this tab only (sessionStorage), and never put in a URL. */
function _tok(){ try{ return sessionStorage.getItem('qaxTok')||''; }catch(e){ return ''; } }
async function toggleSetup(){
  // what is configured on this server (AI provider, Jira, Xray, browser, live writes) — names and
  // status only, never a value; each line says how to change it (all in .env on the server)
  const box=$('setup'), btn=$('setupbtn'); const open=box.style.display==='none';
  box.style.display=open?'block':'none'; btn.setAttribute('aria-expanded', open);
  if(!open) return;
  box.innerHTML='<div class="hk">Checking…</div>';
  try{
    const d=await (await api('/api/doctor')).json(); const icon={ok:'✅',warn:'⚠️',fail:'❌'};
    box.innerHTML='<div class="sec-title" style="margin-top:0">Setup on this server</div><div class="hk">'
      +(d.checks||[]).map(c=>icon[c.status]+' <b>'+esc(c.name)+'</b> — '+esc(c.detail)+(c.fix?'<br><span style="color:var(--muted);margin-left:22px">→ '+esc(c.fix)+'</span>':'')).join('<br>')
      +'</div><div class="hint">Settings live in <code>.env</code> next to QAX (secrets never leave this machine and are never shown here). '
      +'Restart the server after a change. Command line: <code>python main.py doctor</code>.</div>'
      +'<div class="sec-title">Use QAX outside this page</div><div class="hk">'
      +'<b>In CI</b> (GitHub Actions, GitLab, Jenkins): <code>python main.py execute --file cases.json --project NAME --base-url $APP_URL --yes --junit qax-junit.xml</code>'
      +' — the run fails the job when a case fails; ready-made file: <code>ci/github_action_e2e.yml</code>.<br>'
      +'<b>Only what a change can break</b>: add <code>--changed-since origin/main --repo .</code><br>'
      +'<b>From a coding agent</b> (Claude Code, Cursor, any MCP client): <code>python main.py mcp</code> — QAX becomes a tool the agent calls to check its own change. Setup: <code>docs/MCP.md</code>.<br>'
      +'<b>Full accessibility audit from the command line</b>: <code>QAX_A11Y=axe</code>.</div>';
  }catch(e){ box.innerHTML='<div class="hk">⚠️ '+esc(String(e))+'</div>'; }
}
async function openArtifact(path){
  // a network log (HAR) or another saved artifact, through api() so a token-protected server works
  try{ const r=await api('/api/artifact?path='+encodeURIComponent(path)); if(!r.ok) return;
    const b=await r.blob(); const a=document.createElement('a'); a.href=URL.createObjectURL(b);
    a.download=path.split('/').pop(); a.click(); }catch(e){}
}
async function loadArtifacts(){
  // screenshots go through api() so a token-protected server still shows them (an <img src>
  // cannot carry the X-QAX-Token header, and the token never goes in a URL)
  for(const el of document.querySelectorAll('img[data-art]:not([src])')){
    try{ const r=await api('/api/artifact?path='+encodeURIComponent(el.dataset.art)); if(!r.ok) continue;
      const u=URL.createObjectURL(await r.blob()); el.src=u; const a=el.closest('a'); if(a) a.href=u; }catch(e){}
  }
}
async function api(path, opts){
  opts=Object.assign({}, opts||{}); opts.headers=Object.assign({}, opts.headers||{});
  if(_tok()) opts.headers['X-QAX-Token']=_tok();
  let r=await fetch(path, opts);
  if(r.status===401){
    const t=window.prompt('This QAX server needs its API token (QAX_API_TOKEN):');
    if(t){ try{ sessionStorage.setItem('qaxTok', t.trim()); }catch(e){}
      opts.headers['X-QAX-Token']=t.trim(); r=await fetch(path, opts); }
  }
  return r;
}
let _cfg={live_export:false};
api('/api/config').then(r=>r.ok?r.json():{}).then(c=>{ _cfg=c||_cfg; }).catch(()=>{});

let _es=null;
const ICON={start:'▸',ok:'✓',info:'›',warn:'!',error:'✕'};
function logLine(step,status,detail){
  const el=document.createElement('div');
  el.className='logline '+(status||'info');
  el.innerHTML='<span class="ic">'+(ICON[status]||'›')+'</span>'
    +'<span class="st">'+esc(status||'')+'</span>'
    +'<span>'+esc(detail||step)+'</span>';
  const box=$('live'); box.appendChild(el); box.scrollTop=box.scrollHeight;
}
function setMode(m){
  // Two tracks: the security scan, and functional QA (generation is its step 1).
  // 'gen' is kept as an alias (old links, /app#generate) → functional QA.
  if(m==='gen'){ m='func'; }
  const scan = m==='scan';
  $('panel-scan').style.display = scan?'':'none';
  $('panel-func').style.display = scan?'none':'';
  $('mode-scan').classList.toggle('active', scan);
  $('mode-func').classList.toggle('active', !scan);
  $('results').style.display='none';
  $('history').style.display = (scan && ($('runs').children.length)) ? 'block' : 'none';
  const have = (window._lastTests||[]).length;
  $('genresults').style.display = (!scan && have) ? 'block' : 'none';
  if(!scan) $('funcstate').innerHTML = have ? ('<b>'+have+'</b> test case(s) ready — review them, then run them below.') : '';
  $('scanning').style.display='none'; $('err').style.display='none';
}
function showErr(m){ const el=$('err'); el.textContent=m; el.style.display='block'; el.scrollIntoView({block:'center',behavior:'smooth'}); }

window._lastTests=[];
function loadTests(input){
  const f=input.files&&input.files[0]; if(!f) return;
  const rd=new FileReader();
  rd.onload=async ()=>{ try{
      let d=null; try{ d=JSON.parse(rd.result); }catch(e){ d=null; }
      if(!d || (!Array.isArray(d) && !d.test_cases)){
        // not a cases file: maybe a browser recording (Chrome Recorder JSON, Playwright codegen)
        const r=await api('/api/import-recording',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:rd.result})});
        const x=await r.json();
        if(!x.case){ showErr('Not a test-cases file or a browser recording: '+((x.warnings||[]).join('; ')||x.error||'')); return; }
        $('err').style.display='none'; renderTests([...(window._lastTests||[]),x.case]);
        $('funcstate').innerHTML='<b>Recording imported</b> ('+esc(x.format)+') as <b>'+esc(x.case.test_case_id)+'</b> — '
          +x.case.automation_steps.length+' steps'+(x.needs_env.length?'. Set <code>'+x.needs_env.map(esc).join(', ')+'</code> on the server (the recorded password was dropped)':'')
          +((x.warnings||[]).length?'<br>⚠️ '+x.warnings.map(esc).join('<br>⚠️ '):'');
        return;
      }
      const cases=Array.isArray(d)?d:(d.test_cases||[]);
      if(!Array.isArray(cases)||!cases.length){ showErr('No test cases in that file.'); return; }
      $('err').style.display='none'; renderTests(cases.filter(c=>c&&typeof c==='object'));
      $('funcstate').innerHTML='<b>'+window._lastTests.length+'</b> test case(s) loaded — review them, then run them below.';
    }catch(e){ showErr('Not a valid JSON file: '+e); } };
  rd.readAsText(f);
}
function renderTests(cases){
  window._lastTests=cases||[];
  // pick the engine the cases are written for (a browser-cases file used to stay on HTTP and
  // be reported as "will not run" — UX walk 2026-10-20)
  const ui=(cases||[]).filter(c=>(c.automation_steps||[]).length&&!(c.api_steps||[]).length).length;
  const apiN=(cases||[]).filter(c=>(c.api_steps||[]).length).length;
  if($('exengine')&&(ui||apiN)){ $('exengine').value= ui>=apiN?'ui':'api'; if(typeof engineHint==='function') engineHint(); }
  $('genresults').style.display='block';
  $('gentool').style.display = (cases&&cases.length)?'flex':'none';
  $('gencount').textContent = cases.length ? '· '+cases.length : '';
  if(!cases.length){ $('tests').innerHTML='<div class="hint">No test cases generated — try a more specific story.</div>'; $('genjira').innerHTML=''; return; }
  $('tests').innerHTML = cases.map(tc=>{
    const raw=(tc.manual_steps||tc.steps||tc.test_steps||tc.automation_steps||[]);
    const steps=Array.isArray(raw)?raw:[];
    const fmtStep=s=>{
      if(typeof s==='string') return s;
      if(s.action||s.step||s.description) return s.action||s.step||s.description;
      const act=(s.playwright_action||'').replace(/_/g,' ');
      const sel=String(s.selector_key||s.selector||'').replace(/^TODO_/,'');
      const val=s.value?(' → "'+s.value+'"'):'';
      const asrt=(s.assertion_type&&!act.includes('fill')&&!act.includes('navigat'))?(' ['+s.assertion_type+(s.assertion_value?(' '+s.assertion_value):'')+']'):'';
      return ((act+' '+sel).trim()+val+asrt).trim()||JSON.stringify(s);
    };
    const stepsH = steps.length ? '<ol class="steps">'+steps.map(s=>'<li>'+esc(fmtStep(s))+'</li>').join('')+'</ol>' : '';
    const exp = tc.expected_result||tc.expected||tc.expectedResult||'';
    const pri = tc.priority||tc.severity||'';
    const rev = tc.needs_review||tc.needsReview;
    const v=tc._verdict;
    return '<div class="tc"><div class="th"><span class="tt">'+esc(tc.title||tc.name||'Test case')+'</span>'
      +(v?'<span class="vd '+esc(v.status)+'">'+esc(v.label)+'</span>':'<span class="ai">AI proposal · not run</span>')
      +(pri?'<span class="pri">'+esc(pri)+'</span>':'')
      +(rev?'<span class="rev">needs review</span>':'')+'</div>'
      +stepsH
      +(exp?'<div class="exp"><div class="k">Expected</div>'+esc(exp)+'</div>':'')
      +(v&&v.evidence?'<div class="ev">'+esc(v.evidence)+'</div>':'')
      +(v&&v.shots&&v.shots.length?'<div class="shots">'+v.shots.map(s=>'<a href="#" target="_blank" data-art="'+esc(s.p)
          +'" title="step '+s.n+' · '+esc(s.st)+'"><img alt="step '+s.n+'" data-art="'+esc(s.p)
          +'"><span>'+s.n+'</span></a>').join('')+'</div>':'')
      +(v&&v.trace?'<div class="hint">Trace: <code>playwright show-trace '+esc(v.trace)+'</code></div>':'')
      +'</div>';
  }).join('');
  $('execpanel').style.display='block';
  renderCoverage();
  loadArtifacts();
  $('genjira').innerHTML = 'Review, run them against your app below, then <b>push to Jira / Xray</b> with the button above.';
}

async function doExport(items, kind, btn, msgEl){
  if(!items || !items.length){ msgEl.style.display='block'; msgEl.innerHTML='Nothing to file yet.'; return; }
  // Live filing only when the operator enabled it on the server AND confirms here.
  const live = !!(_cfg.live_export && window.confirm('File '+items.length+' item(s) for real in Jira/Xray? Cancel = dry-run preview.'));
  const orig=btn.textContent; btn.disabled=true; btn.textContent='Filing…';
  msgEl.style.display='block'; msgEl.innerHTML='Filing to Jira/Xray…';
  try{
    const r=await api('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({items,kind,target:'xray',dry_run:!live})});
    const d=await r.json();
    if(d.error){ msgEl.innerHTML='⚠️ '+esc(d.error); }
    else {
      const dry=d.dry_run, n=d.exported_count||0;
      const noun=(d.kind==='findings')?('bug'+(n!==1?'s':'')):('test case'+(n!==1?'s':''));
      const where=(d.kind==='findings')?'Jira':esc((d.target||'xray').toUpperCase());
      msgEl.innerHTML=(dry?'🧪 <b>Dry run</b> — would file ':'✅ filed ')+n+' '+noun+' in <b>'+where+'</b>'
        +(d.references&&d.references.length?' ('+d.references.slice(0,3).map(esc).join(', ')+(d.references.length>3?'…':'')+')':'')
        +(dry?('. Nothing was filed. '+(_cfg.live_export?'Confirm the prompt to file for real.'
              :'Live filing from the web UI is off — set <code>QAX_ALLOW_LIVE_EXPORT=true</code> (plus Jira/Xray credentials) on the server, or use the CLI with <code>--live</code>.')):'.');
    }
  }catch(e){ msgEl.innerHTML='⚠️ Export failed: '+esc(String(e)); }
  finally{ btn.disabled=false; btn.textContent=orig; }
}
function engineHint(){
  const ui=$('exengine').value==='ui'; $('exprojf').style.display=ui?'':'none';
  if(ui && !$('mapproj').value) $('mapproj').value=_cfg.project||'';
  $('exprojname').textContent=$('mapproj').value||'— (enter a project name at the top)';
  if(ui && !_cfg.browser) $('exhint').innerHTML='⚠️ Playwright is not installed on the server: <code>pip install playwright && playwright install chromium</code>';
}
function fqProject(){
  // a project name from the URL's host, unless the user typed one
  const p=$('mapproj'); if(p.dataset.touched) return;
  try{ const h=new URL($('mapurl').value.trim()).host.replace(/[^A-Za-z0-9]+/g,'-').replace(/^-|-$/g,''); p.value=h||''; }catch(e){}
}
window._fqCriteria=[]; window._fqCoverage=null;
async function analyzeAll(){
  const url=$('mapurl').value.trim(), repo=$('fqrepo').value.trim(), story=$('story').value.trim(), code=$('code').value.trim();
  if(!url && !repo && !story && !code){ showErr('Give QAX something to test: the app URL, a repository, a user story or code.'); return; }
  if(url && !$('mapauth').checked){ showErr('Confirm you own this app or are authorized to test it.'); return; }
  $('err').style.display='none'; $('genresults').style.display='none'; $('fqreport').style.display='none';
  const btn=$('fqgo'); btn.disabled=true; const orig=btn.textContent; btn.textContent='Working…';
  $('scanning').style.display='block'; $('scanmsg').textContent='Understanding your inputs and writing test cases…'; $('live').innerHTML='';
  if(url) logLine('read','start','reading the app at '+url+' (links, menus, forms)');
  if(repo) logLine('repo','start','reading the repository');
  if(story) logLine('story','start','numbering the acceptance criteria');
  logLine('generate','start','writing test cases (ISTQB techniques, traced to the criteria)');
  try{
    const r=await api('/api/functional/analyze',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url,repo,story,code,project:$('mapproj').value.trim(),username:$('mapuser').value,
        password:$('mappass').value,explore_actions:$('mapexplore').checked,authorized:$('mapauth').checked})});
    const d=await r.json();
    renderUnderstanding(d.understanding||{});
    if(d.error && !(d.tests||[]).length){ showErr(d.error); }
    if((d.tests||[]).length){
      logLine('generate','ok',d.tests.length+' test case(s) written'+(d.invalid?(' · '+d.invalid+' rejected by validation'):''));
      window._fqCoverage=d.coverage||null; renderTests(d.tests);
      if(url){ $('exurl').value=url; $('exengine').value='ui'; engineHint(); if(!$('mapproj').value) $('mapproj').value=(d.understanding||{}).project||''; engineHint(); $('exro').checked=false; $('exauth').checked=$('mapauth').checked; }
    }
  }catch(e){ showErr('Failed: '+e); }
  finally{ btn.disabled=false; btn.textContent=orig; $('scanning').style.display='none'; $('mappass').value=''; loadOverview(); }
}
function renderUnderstanding(u){
  window._fqCriteria=u.criteria||[]; window._fqStoryKey=u.story_key||'';
  const pages=u.pages||[];
  let h='<div class="sec-title" style="margin-top:14px">2 · What QAX understood</div><div class="hk">';
  if(pages.length) h+='<b>The app:</b> '+pages.length+' page(s), '+pages.reduce((a,p)=>a+p.elements,0)+' element(s)'
    +(u.login_ok?' · logged in':(u.login_ok===false?' · ⚠️ login did not succeed':''))+'<br><span style="color:var(--muted)">'
    +pages.slice(0,14).map(p=>esc(p.path)).join(' · ')+'</span><br>';
  if((u.skipped_links||[]).length) h+='Not followed while reading (would change data): '+u.skipped_links.slice(0,6).map(esc).join(', ')+'<br>';
  if(u.writes) h+='⚠️ '+u.writes+' write request(s) were sent while reading<br>';
  if(u.repo) h+='<b>The repository:</b> '+esc(u.repo.overview||u.repo.folder)+((u.repo.risk_areas||[]).length?'<br>Risk areas: '+u.repo.risk_areas.map(esc).join(', '):'')+'<br>';
  if(u.story_key) h+='<b>Story:</b> '+esc(u.story_key)+' (read from Jira; the results and bugs go back to it)<br>';
  if((u.criteria||[]).length) h+='<b>Acceptance criteria:</b><br>'+u.criteria.map(c=>'<code>'+esc(c.id)+'</code> '+esc(c.text)).join('<br>')+'<br>';
  if((u.security_risks||[]).length){
    h+='<b>Security risks in these criteria</b> (security cases are written for them too):<br>'
      +u.security_risks.map(r=>'<code>'+esc(r.criterion)+'</code> '+esc(r.risk)+' — <span style="color:var(--muted)">'+esc(r.check)+'</span>').join('<br>')
      +($('mapurl').value.trim()?'<br><button class="btn2" style="margin-top:6px" onclick="proveWithScanner()">🛡️ Prove them on the running app with the scanner</button>':'')+'<br>';
  }
  (u.notes||[]).forEach(n=>{ h+='⚠️ '+esc(n)+'<br>'; });
  h+='</div>'; $('fqunderstand').innerHTML=h;
}
async function recordSession(forgetIt){
  // SSO / 2FA apps: the operator logs in by hand once in a real window; runs reuse the session
  const out=$('sessstate'), project=$('mapproj').value.trim(), url=$('mapurl').value.trim();
  if(!project){ out.textContent='Enter a project name first.'; return; }
  if(!forgetIt && (!url || !$('mapauth').checked)){ out.textContent='Enter the app URL and confirm you are authorized.'; return; }
  out.textContent=forgetIt?'Deleting…':'A browser window opened on this machine — log in, then close it.';
  try{
    const r=await api('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:forgetIt?'forget':'record',project,url,authorized:$('mapauth').checked})});
    const d=await r.json();
    out.innerHTML=d.error?('⚠️ '+esc(d.error)):(d.saved?('✅ Session saved '+esc(d.when||'')+' — runs start logged in. <a href="#" onclick="recordSession(true);return false">forget it</a>'):(forgetIt?'Session deleted.':esc(d.message||'Not saved.')));
  }catch(e){ out.textContent='⚠️ '+e; }
}
async function setQuarantine(caseId, on){
  const project=(window._overviewProject||$('mapproj').value||'').trim(); if(!project) return;
  await api('/api/quarantine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project,case_id:caseId,on})});
  loadOverview();
}
function hostProject(url){ try{ return new URL(url).host.replace(/[^A-Za-z0-9]+/g,'-').replace(/^-|-$/g,''); }catch(e){ return ''; } }
document.addEventListener('DOMContentLoaded', ()=>{
  try{ const p=localStorage.getItem('qaxProject'); if(p){ window._overviewProject=p; if(!$('mapproj').value) $('mapproj').value=p; loadOverview(); } }catch(e){}
  const u=$('url'); if(u) u.addEventListener('change', ()=>{ const p=hostProject(u.value.trim()); if(p){ window._overviewProject=p; loadOverview(); } });
  const m=$('mapproj'); if(m) m.addEventListener('change', ()=>{ window._overviewProject=m.value.trim(); loadOverview(); });
});
async function loadOverview(){
  // the project's quality from what QAX measured — every card says where its data comes from
  const box=$('fqoverview');
  const project=(window._overviewProject||$('mapproj').value||'').trim();
  try{ if(project) localStorage.setItem('qaxProject', project); }catch(e){}
  if(!project){ box.innerHTML=''; return; }
  try{
    const appUrl=(($('panel-scan')&&$('panel-scan').style.display!=='none')?$('url').value:$('mapurl').value)||'';
    const r=await api('/api/overview?project='+encodeURIComponent(project)+'&url='+encodeURIComponent(appUrl.trim())); const d=await r.json();
    if(d.error){ box.innerHTML=''; return; }
    const go={run:"$('execpanel').scrollIntoView({block:'start'})",analyze:"$('mapurl').focus()",
              bugs:"($('fqreport')||$('execpanel')).scrollIntoView()",scan:"setMode('scan')"};
    const spark=pts=>{ if(!pts||pts.length<2) return ''; const w=120,h=26,n=pts.length;
      const xy=pts.map((v,k)=>[(k*(w-4)/(n-1)+2).toFixed(1),(h-2-(v/100)*(h-4)).toFixed(1)]);
      return '<svg class="spark" viewBox="0 0 '+w+' '+h+'" width="'+w+'" height="'+h+'" role="img" aria-label="pass rate over the last '+n+' runs">'
        +'<polyline points="'+xy.map(p=>p.join(',')).join(' ')+'"/><circle cx="'+xy[n-1][0]+'" cy="'+xy[n-1][1]+'" r="2.5"/></svg>'; };
    const card=c=>'<div class="ovcard '+esc(c.state)+'"><h4>'+esc(c.title)+'</h4><div class="v">'+esc(c.value)+'</div>'
        +(c.points?spark(c.points):'')
        +(c.rows&&c.rows.length?'<div class="rq">'+c.rows.slice(0,8).map(x=>'<code>'+esc(x.id)+'</code> <b>'+esc(x.verdict)+'</b> '+esc(x.text).slice(0,60)
            +(c.id==='flaky'?' <a href="#" onclick="setQuarantine(\''+esc(x.id)+'\','+(!x.quarantined)+');return false">'+(x.quarantined?'release':'quarantine')+'</a>':'')).join('<br>')+'</div>':'')
        +'<div class="d">'+esc(c.detail||'')+'</div></div>';
    const groups=d.groups||[];
    box.innerHTML='<div class="ovhead"><div class="sec-title" style="margin:0">Project overview · '+esc(d.project)+'</div>'
      +'<span class="hint" style="margin:0">From saved runs only — nothing invented (AI cost is an estimate from token counts)</span></div>'
      +groups.map(g=>'<div class="ovgroup"><div class="ovgtitle"><b>'+esc(g.title)+'</b> <span>'+esc(g.hint)+'</span></div>'
        +'<div class="ovgrid">'+g.cards.map(card).join('')+'</div></div>').join('')
      +((d.setup||[]).length?'<div class="ovsetup"><b>'+(groups.length?'Not measured yet':'To fill this overview')+'</b><ul>'
        +d.setup.map(c=>'<li><span>'+esc(c.title)+'</span> — '+esc(c.detail)
          +(go[c.action]?' <a href="#" onclick="'+go[c.action]+';return false">Start</a>':'')+'</li>').join('')+'</ul></div>':'');
  }catch(e){ box.innerHTML=''; }
}
function proveWithScanner(){
  // the same app, the other track: deterministic probes prove access-control and injection risks
  $('url').value=$('mapurl').value.trim(); setMode('scan'); $('url').scrollIntoView({block:'center'});
}
function renderCoverage(){
  const c=window._fqCoverage, el=$('fqcoverage'); if(!el) return;
  if(!c || !Object.keys(c.by_criterion||{}).length){ el.innerHTML=''; return; }
  const ac=window._fqCriteria||[], txt=id=>(ac.find(a=>a.id===id)||{}).text||'';
  const tech=Object.entries(c.techniques||{}).map(([k,v])=>esc(k)+' ×'+v).join(' · ');
  el.innerHTML='<div class="hk"><b>Requirement coverage: '+c.percent+'%</b>'
    +(c.uncovered.length?' — <b style="color:var(--p2)">not covered: '+c.uncovered.map(esc).join(', ')+'</b>':' — every criterion has a case')
    +'<br>'+Object.entries(c.by_criterion).map(([k,v])=>'<code>'+esc(k)+'</code> '+esc(txt(k)).slice(0,70)+' → '+(v.length?v.map(esc).join(', '):'<b>no case</b>')).join('<br>')
    +(tech?'<br><span style="color:var(--muted)">Techniques: '+tech+'</span>':'')+'</div>';
}
function _csvCell(v){ v=(v==null?'':(typeof v==='object'?JSON.stringify(v):String(v))); return '"'+v.replace(/"/g,'""')+'"'; }
function downloadCases(kind){
  const t=window._lastTests||[]; if(!t.length) return;
  let body, type, name;
  if(kind==='json'){ body=JSON.stringify({test_cases:t.map(x=>{const y={...x}; delete y._verdict; return y;})},null,2); type='application/json'; name='qax-test-cases.json'; }
  else {
    const rows=[['ID','Title','Priority','Type','Description','Preconditions','Steps','Expected']];
    t.forEach(x=>{ const st=(x.manual_steps||[]); rows.push([x.test_case_id,x.title,x.priority,x.test_type,x.description,
      (x.preconditions||[]).join('\n'), st.map(s=>s.step_number+'. '+s.action).join('\n'), st.map(s=>s.step_number+'. '+(s.expected_result||'')).join('\n')]); });
    body=rows.map(r=>r.map(_csvCell).join(',')).join('\n'); type='text/csv'; name='qax-test-cases.csv';
  }
  const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([body],{type})); a.download=name; a.click();
}
window._fqLastRun=null;
function renderReport(d){
  window._fqLastRun=d;
  const box=$('fqreport'); const res=d.results||[]; if(!res.length){ box.style.display='none'; return; }
  const byId={}; (window._lastTests||[]).forEach(t=>{ byId[t.test_case_id]=t; });
  // only failures the APP caused are bug candidates; test problems and environment trouble are
  // listed apart (they are fixed elsewhere, never filed as bugs)
  const fails=res.filter(r=>r.status==='FAIL'&&(!r.cause||r.cause==='app'||r.cause==='unclear')&&!r.quarantined),
        notBugs=res.filter(r=>r.status!=='PASS'&&(r.cause==='test'||r.cause==='environment')),
        problems=res.filter(r=>(r.page_errors||[]).length);
  const ac=window._fqCriteria||[], cov=window._fqCoverage;
  let acRows='';
  if(cov && ac.length){
    acRows='<div class="sec-title" style="margin-top:12px">Requirements</div><div class="hk">'+ac.map(a=>{
      const ids=(cov.by_criterion||{})[a.id]||[]; const st=ids.map(i=>(res.find(r=>r.test_case_id===i)||{}).status).filter(Boolean);
      const v=!ids.length?'NOT COVERED':st.includes('FAIL')?'FAILED':st.length&&st.every(x=>x==='PASS')?'PASSED':'NOT FULLY RUN';
      return '<code>'+esc(a.id)+'</code> '+esc(a.text).slice(0,80)+' → <b>'+v+'</b>'; }).join('<br>')+'</div>';
  }
  let h='<div class="sec-title">5 · Report</div><div class="hk"><b>'+(d.passed||0)+' passed · '+(d.failed||0)+' failed · '
    +((d.skipped||0)+(d.errors||0)+(d.not_run||0))+' not run</b> (not passes)'
    +(d.changes&&d.changes.runs_before?'<br>Since the last run: <b>'+d.changes.regressions.length+' newly failing</b>'
      +(d.changes.regressions.length?' ('+d.changes.regressions.map(esc).join(', ')+')':'')+' · '+d.changes.fixed.length+' fixed'
      +(d.changes.flaky.length?' · ⚠️ flaky: '+d.changes.flaky.map(esc).join(', '):''):'')
    +(d.writes_sent?'<br>'+d.writes_sent+' request(s) that change data were sent to the app — only what a case’s cleanup steps delete is undone':'')
    +(d.cost?'<br>Took '+esc(String(d.cost.duration_s))+' s · '+(d.cost.calls?esc(String(d.cost.calls))+' AI call(s), ≈ $'+Number(d.cost.estimated_cost_usd||0).toFixed(4):'no AI calls'):'')
    +(res.some(r=>r.cleanup)?'<br>Cleanup: '+res.filter(r=>r.cleanup).map(r=>esc(r.test_case_id)+' '+(r.cleanup.status==='done'?'✅ undone':'⚠️ incomplete — data left')).join(' · '):'')
    +(res.some(r=>r.visual&&r.visual.status!=='identical')?'<br>Screens: '+res.filter(r=>r.visual&&r.visual.status!=='identical').map(r=>esc(r.test_case_id)+' '
      +(r.visual.status==='baseline_saved'?'baseline saved':(r.visual.status==='changed'?'<b>changed '+r.visual.pct+'%</b> (a lead — open the diff)':esc(r.visual.status)))).join(' · '):'')
    +(res.some(r=>r.healed_steps)?'<br>⚠️ '+res.filter(r=>r.healed_steps).length+' case(s) passed only after AI healing changed a selector ('
      +res.filter(r=>r.healed_steps).map(r=>esc(r.test_case_id)).join(', ')+') — review the healed step before trusting it':'')+'</div>'+acRows;
  if(problems.length) h+='<div class="sec-title" style="margin-top:12px">Problems seen in the pages</div><div class="hk">'
    +problems.map(r=>'<b>'+esc(r.test_case_id)+'</b>: '+(r.page_errors||[]).slice(0,4).map(e=>esc(e.kind+': '+e.text)).join(' · ')).join('<br>')+'</div>';
  const qpages={}; res.forEach(r=>((r.quality||{}).pages||[]).forEach(p=>{ if(!qpages[p.url]) qpages[p.url]=p; }));
  const qlist=Object.values(qpages);
  if(qlist.length){
    const fmt=v=>v==null?'–':v;
    h+='<div class="sec-title" style="margin-top:12px">Accessibility &amp; performance (observations — not verdicts)</div><div class="hk">'
      +qlist.map(p=>{ const pf=p.performance||{};
        return '<b>'+esc(p.url)+'</b> — LCP '+fmt(pf.lcp_ms)+' ms · load '+fmt(pf.load_ms)+' ms · CLS '+fmt(pf.cls)
          +(p.slow.length?' · <b style="color:var(--p2)">slow: '+p.slow.map(esc).join(', ')+'</b>':'')
          +(p.accessibility.length?'<br>'+p.accessibility.slice(0,8).map(i=>'• '+esc(i.rule)+' <span style="color:var(--muted)">(WCAG '+esc(i.wcag)+(i.impact?' · '+esc(i.impact):'')+') '+esc(i.target)+'</span>').join('<br>')
            +(p.accessibility.length>8?'<br>… '+(p.accessibility.length-8)+' more':''):'<br>no accessibility problem found by '+esc(p.a11y_engine||'the basic checks')); }).join('<br><br>')
      +'<br><span style="color:var(--muted)">Automated checks ('+esc((qlist[0]||{}).a11y_engine||'basic')+') — not a full accessibility audit (no screen reader, keyboard or focus-order testing). QAX_A11Y=axe runs axe-core.</span></div>';
  }
  // what to do next, from the verdicts (rules, in the order a QA lead would act)
  const nxt=[];
  if(d.changes&&d.changes.regressions&&d.changes.regressions.length) nxt.push('Fix what newly fails first: '+d.changes.regressions.map(esc).join(', '));
  if(fails.filter(r=>r.cause!=='unclear').length) nxt.push('File the '+fails.filter(r=>r.cause!=='unclear').length+' bug(s) below to Jira');
  if(fails.filter(r=>r.cause==='unclear').length) nxt.push('Look at the screenshot of '+fails.filter(r=>r.cause==='unclear').map(r=>esc(r.test_case_id)).join(', ')+' — app change or stale case?');
  const tp=res.filter(r=>r.cause==='test'); if(tp.length) nxt.push('Fix '+tp.length+' test case(s) that could not run'+(tp.some(r=>/No element named/.test(JSON.stringify(r.steps||[])))?' — re-read the app (Understand) so its elements are known':''));
  const ev=res.filter(r=>r.cause==='environment'); if(ev.length) nxt.push('Check the environment, then re-run '+ev.length+' case(s): '+(ev.some(r=>r.throttled)?'the app rate-limited QAX — wait or raise the limit on staging':'the app or browser did not answer'));
  if(cov&&(cov.uncovered||[]).length) nxt.push('Write cases for the criteria with none: '+cov.uncovered.map(esc).join(', '));
  if(d.changes&&d.changes.flaky&&d.changes.flaky.length) nxt.push('Quarantine or stabilise the flaky case(s): '+d.changes.flaky.map(esc).join(', '));
  if(!nxt.length&&res.length&&res.every(r=>r.status==='PASS')) nxt.push('All passed — run again after the next change; the overview shows what changes');
  if(nxt.length) h+='<div class="sec-title" style="margin-top:12px">Next</div><div class="hk">'+nxt.map(x=>'→ '+x).join('<br>')+'</div>';
  if(notBugs.length){
    const by={test:[],environment:[]}; notBugs.forEach(r=>by[r.cause].push(r));
    h+='<div class="sec-title" style="margin-top:12px">Not bugs in the app ('+notBugs.length+')</div><div class="hk">'
      +(by.test.length?'<b>Test problems</b> — fix the case, then re-run: '+by.test.map(r=>esc(r.test_case_id)+' <span style="color:var(--muted)">('+esc((r.steps||[]).find(s=>s.status!=='PASS')?.actual_result||r.error||'').slice(0,70)+')</span>').join(' · ')+'<br>':'')
      +(by.environment.length?'<b>Environment</b> — the app or browser did not answer, or rate-limited QAX: '+by.environment.map(r=>esc(r.test_case_id)).join(', '):'')+'</div>';
  }
  h+='<div class="sec-title" style="margin-top:12px">Bugs found ('+fails.length+')</div>';
  if(fails.length){
    h+='<div class="hk">'+fails.map(r=>{ const f=(r.steps||[]).find(s=>s.status==='FAIL')||{};
      const unclear=r.cause==='unclear';
      return '<label class="bugrow"><input type="checkbox" class="bugpick" value="'+esc(r.test_case_id)+'"'+(unclear?'':' checked')+'><span><b>'
        +esc((byId[r.test_case_id]||{}).title||r.title||r.test_case_id)+'</b> — step '+esc(f.step_number)+': '+esc(f.actual_result||r.error||'failed')
        +(unclear?' <span style="color:var(--p2)">· unclear: the app changed or the case is stale — check the screenshot before filing</span>':'')
        +(r.network_log?' <a href="#" onclick="openArtifact(\''+esc(r.network_log)+'\');return false">network log</a>':'')
        +' <a href="#" title="Steps, evidence, files to look at and the command that verifies the fix — for Claude Code, Codex or Cursor" onclick="downloadFixBrief(\''+esc(r.test_case_id)+'\');return false">fix brief for your coding agent</a>'+'</span></label>'; }).join('')
      +'<div class="row" style="justify-content:flex-end;gap:8px;margin-top:8px"><select id="fqlang" title="Language of the bugs and the report" style="width:auto"><option value="en">English</option><option value="ar">العربية</option></select><button class="btn2" onclick="downloadE2EReport()">⤓ Report (HTML)</button><button class="btn2" onclick="downloadJUnit()" title="For CI dashboards (GitHub Actions, GitLab, Jenkins)">⤓ JUnit XML</button>'
      +'<button class="btn" id="fqbugsbtn" onclick="fileBugs()">⤴ File selected bugs to Jira</button></div><div id="fqbugsout"></div></div>';
  } else h+='<div class="hk">No failed case. <select id="fqlang" style="width:auto"><option value="en">English</option><option value="ar">العربية</option></select> <button class="btn2" onclick="downloadE2EReport()">⤓ Report (HTML)</button> <button class="btn2" onclick="downloadJUnit()" title="For CI dashboards (GitHub Actions, GitLab, Jenkins)">⤓ JUnit XML</button></div>';
  box.innerHTML=h; box.style.display='block';
}
async function fileBugs(){
  const d=window._fqLastRun||{}; const pick=[...document.querySelectorAll('.bugpick:checked')].map(x=>x.value);
  const results=(d.results||[]).filter(r=>pick.includes(r.test_case_id)); const out=$('fqbugsout');
  if(!results.length){ out.innerHTML='Select at least one bug.'; return; }
  const live=!!(_cfg.live_export && window.confirm('File '+results.length+' bug(s) for real in Jira? Cancel = dry-run preview.'));
  out.innerHTML='Filing…';
  try{
    const r=await api('/api/functional/bugs',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({results,tests:window._lastTests,url:$('exurl').value.trim(),dry_run:!live,project:$('mapproj').value.trim(),
        story_key:(window._fqStoryKey||''),lang:($('fqlang')||{}).value||'en',criteria:window._fqCriteria||[],coverage:window._fqCoverage||{},
        all_results:(d.results||[]).map(r=>({test_case_id:r.test_case_id,status:r.status}))})});
    const j=await r.json();
    if(j.error){ out.innerHTML='⚠️ '+esc(j.error); return; }
    out.innerHTML=(j.dry_run?'🧪 <b>Dry run</b> — nothing was filed. These bugs would be created:':'✅ Filed:')
      +(j.bugs||[]).map((b,i)=>{ const rr=(j.results||[])[i]||{}; return '<details style="margin-top:6px"><summary>'+esc(rr.jira_key||'')+' '+esc(b.title)
          +(rr.linked_to?' → linked to '+esc(rr.linked_to):'')+'</summary><pre class="ev">'+esc(b.text)+'</pre></details>'; }).join('')
      +(j.story_comment?'<details style="margin-top:6px" open><summary>Requirement verdicts '+(j.dry_run?'that would be commented on ':'commented on ')+esc(j.story_comment.story_key)+'</summary><pre class="ev">'+esc(j.story_comment.text)+'</pre></details>':'')
      +(j.regression_suite?'<div class="hint">These cases were added to the regression suite: <code>'+esc(j.regression_suite)+'</code></div>':'')
      +(j.dry_run&&!_cfg.live_export?'<div class="hint">Live filing is off — set <code>QAX_ALLOW_LIVE_EXPORT=true</code> (and Jira credentials) on the server.</div>':'');
  }catch(e){ out.innerHTML='⚠️ '+esc(String(e)); }
}
const _RW={en:{title:'Functional QA report',app:'App',passed:'passed',failed:'failed',notrun:'not run',id:'ID',tc:'Test case',
  verdict:'Verdict',failure:'Failure',covers:'Covers',req:'Requirements',note:'A case that did not fully run is never counted as passed.'},
  ar:{title:'تقرير اختبار وظيفي شامل',app:'التطبيق',passed:'نجح',failed:'فشل',notrun:'لم يُنفَّذ',id:'الرقم',tc:'حالة الاختبار',
  verdict:'النتيجة',failure:'سبب الفشل',covers:'يغطي',req:'المتطلبات',note:'الحالة التي لم تُنفَّذ بالكامل لا تُحسب ناجحة أبدًا.'}};
async function downloadFixBrief(id){
  // one failed case as a Markdown brief a coding agent can act on (core/fix_brief.py) — nothing is run
  const res=((window._fqLastRun||{}).results||[]).find(r=>r.test_case_id===id); if(!res) return;
  const tc=(window._lastTests||[]).find(t=>t.test_case_id===id)||null;
  try{ const r=await api('/api/fix-brief',{method:'POST',headers:{'Content-Type':'application/json'},
         body:JSON.stringify({result:res,case:tc,url:$('exurl').value.trim(),project:$('mapproj').value.trim(),
                              repo:(($('exrepo')||{}).value||'').trim()})});
    const d=await r.json(); if(!d.markdown) return;
    const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([d.markdown],{type:'text/markdown'}));
    a.download='qax-fix-'+id+'.md'; a.click(); }catch(e){}
}
async function releaseCheck(){
  // one decision for this change: the cases it can affect + the security scan → GO / NO-GO / NOT PROVEN
  const out=$('exout'), url=$('exurl').value.trim();
  if(!window._lastTests.length){ out.innerHTML='<div class="hk">Write or load test cases first (steps 1–3).</div>'; return; }
  if(!$('exauth').checked){ out.innerHTML='<div class="hk">Confirm you are authorized to test this app (the check also scans it).</div>'; return; }
  const btn=$('exrc'); btn.disabled=true;
  out.innerHTML='<div class="hk">Release check running — the affected cases in the browser, then a read-only security scan. This takes a few minutes.</div>';
  try{
    const r=await api('/api/release-check',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({tests:window._lastTests,url,project:$('mapproj').value.trim(),a11y:$('exa11y').value,
        changed_since:(($('exsince')||{}).value||'').trim(),repo:(($('exrepo')||{}).value||'').trim(),
        authorized:$('exauth').checked})});
    const d=await r.json();
    if(d.error){ out.innerHTML='<div class="hk">⚠️ '+esc(d.error)+'</div>'; return; }
    window._lastRelease=d;
    const cls={'GO':'ok','NO-GO':'bad','NOT PROVEN':'warn'}[d.verdict]||'warn';
    out.innerHTML='<div class="rcverdict '+cls+'"><div class="rcv">'+esc(d.verdict)+'</div><div>'+(d.reasons||[]).map(esc).join('<br>')
      +'<br><span class="hint" style="margin:0">'+esc(d.selection.why)+' · '+(d.security.ran?d.security.confirmed+' confirmed security finding(s), '+d.security.blocking+' blocking':'security scan did not run')
      +' · '+d.duration_s+' s</span></div></div>'
      +((d.blockers||[]).length?'<div class="hk"><b>Blocking</b><br>'+d.blockers.map(b=>'• '+esc(b.kind)+' <code>'+esc(b.id)+'</code> '+esc(b.title)+' — '+esc(b.why)).join('<br>')+'</div>':'')
      +((d.not_proven||[]).length?'<div class="hk"><b>Did not run to a verdict</b> (not counted as passed)<br>'+d.not_proven.map(n=>'• '+esc(n.kind)+' <code>'+esc(n.id)+'</code> '+esc(n.title)+' — '+esc(n.why)).join('<br>')+'</div>':'')
      +'<div class="row" style="justify-content:flex-end;gap:8px"><button class="btn2" onclick="downloadReleaseMd()">⤓ Report for the PR (Markdown)</button></div>';
  }catch(e){ out.innerHTML='<div class="hk">⚠️ '+esc(String(e))+'</div>'; }
  finally{ btn.disabled=false; }
}
function downloadReleaseMd(){
  const d=window._lastRelease; if(!d) return;
  const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([d.report_md],{type:'text/markdown'}));
  a.download='qax-release-check.md'; a.click();
}
async function downloadJUnit(){
  // the last run's results as JUnit XML, for a CI dashboard (the same file `main.py execute --junit` writes)
  try{ const r=await api('/api/junit',{method:'POST',headers:{'Content-Type':'application/json'},
         body:JSON.stringify({results:(window._fqLastRun||{}).results||[]})}); if(!r.ok) return;
    const a=document.createElement('a'); a.href=URL.createObjectURL(await r.blob()); a.download='qax-junit.xml'; a.click(); }catch(e){}
}
function downloadE2EReport(){
  const L=(($('fqlang')||{}).value==='ar')?'ar':'en', W=_RW[L];
  const d=window._fqLastRun||{}, t=window._lastTests||[], when=new Date().toISOString().slice(0,16).replace('T',' ');
  const byId={}; t.forEach(x=>{byId[x.test_case_id]=x;});
  const row=r=>{ const f=(r.steps||[]).find(s=>s.status==='FAIL')||{}; return '<tr><td>'+esc(r.test_case_id)+'</td><td>'+esc((byId[r.test_case_id]||{}).title||r.title||'')
    +'</td><td><b>'+esc(r.status)+'</b></td><td>'+esc(f.actual_result||r.error||'')+'</td><td>'+esc(((byId[r.test_case_id]||{}).description||'').split('.')[0])+'</td></tr>'; };
  const ac=window._fqCriteria||[], cov=window._fqCoverage, res=d.results||[];
  const reqs=(cov&&ac.length)?'<h2>'+W.req+'</h2><ul>'+ac.map(a=>{ const ids=(cov.by_criterion||{})[a.id]||[];
      const st=ids.map(i=>(res.find(r=>r.test_case_id===i)||{}).status).filter(Boolean);
      const v=!ids.length?'NOT COVERED':st.includes('FAIL')?'FAILED':st.length&&st.every(x=>x==='PASS')?'PASSED':'NOT FULLY RUN';
      return '<li><b>'+esc(a.id)+'</b> '+esc(a.text)+' → <b>'+v+'</b></li>'; }).join('')+'</ul>':'';
  const html='<!doctype html><html lang="'+L+'" dir="'+(L==='ar'?'rtl':'ltr')+'"><meta charset="utf-8"><title>QAX E2E report</title><style>body{font:14px system-ui;margin:24px;color:#111}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:6px;text-align:start;vertical-align:top}</style>'
    +'<h1>'+W.title+'</h1><p>'+W.app+': <b dir="ltr">'+esc($('exurl').value)+'</b> · '+esc(when)+'</p>'
    +'<p><b>'+(d.passed||0)+' '+W.passed+' · '+(d.failed||0)+' '+W.failed+' · '+((d.skipped||0)+(d.errors||0)+(d.not_run||0))+' '+W.notrun+'</b></p>'
    +reqs+'<table><tr><th>'+W.id+'</th><th>'+W.tc+'</th><th>'+W.verdict+'</th><th>'+W.failure+'</th><th>'+W.covers+'</th></tr>'+res.map(row).join('')+'</table><p>'+W.note+'</p></html>';
  const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([html],{type:'text/html'})); a.download='qax-e2e-report.html'; a.click();
}
async function runTests(preview){
  const out=$('exout'), url=$('exurl').value.trim();
  if(!window._lastTests.length){ out.innerHTML='<div class="hk">Write or load test cases first (steps 1–3).</div>'; return; }
  if(!preview && !$('exauth').checked){ out.innerHTML='<div class="hk">Confirm you are authorized to test this app.</div>'; return; }
  const btn=$('exgo'); btn.disabled=true; out.innerHTML='<div class="hk">'+(preview?'Planning…':'Running…')+'</div>';
  try{
    const r=await api('/api/execute',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({tests:window._lastTests,url,preview,read_only:$('exro').checked,
        engine:$('exengine').value,project:$('mapproj').value.trim(),insecure:$('exinsecure').checked,
        watch:$('exwatch').checked,trace:$('extrace').checked,
        device:$('exdevice').value,browser:$('exbrowser').value,workers:+$('exworkers').value,visual:$('exvisual').checked,a11y:$('exa11y').value,
        changed_since:($('exsince')||{}).value?.trim()||'',repo:($('exrepo')||{}).value?.trim()||'',
        authorized:$('exauth').checked,token:$('extoken').value.trim()})});
    const d=await r.json(); const p=d.plan||{};
    let h='';
    if(d.error) h+='<div class="hk">⚠️ '+esc(d.error)+'</div>';
    if(p.selection) h+='<div class="hk"><b>Change-based selection</b> — '+esc(p.selection.why)
      +((p.selection.skipped||[]).length?'<br><span style="color:var(--muted)">Skipped: '+p.selection.skipped.map(x=>esc(x.id)+' ('+esc(x.reason).slice(0,60)+')').join(' · ')+'</span>':'')+'</div>';
    h+='<div class="hk"><b>Plan</b> — '+(p.runnable||[]).length+' runnable '+(p.engine==='ui'?'in the browser':'over HTTP')+' against '+esc(p.url||url)
      +(p.read_only?' (read-only)':'')+'; '+(p.not_run||[]).length+' will not run'
      +((p.not_run||[]).length?':<br>'+p.not_run.map(n=>'• '+esc(n.title||n.test_case_id)+' — '+esc(n.reason)).join('<br>'):'')+'</div>';
    if(!preview && d.results){
      h+='<div class="hk"><b>Result</b> — '+(d.passed||0)+' passed · '+(d.failed||0)+' failed · '
        +((d.skipped||0)+(d.errors||0)+(d.not_run||0))+' not run (not passes)'
        +(d.writes_sent?('<br>⚠️ '+d.writes_sent+' request(s) that can change data were sent to the app — QAX does not undo data created through the UI.'):'')+'</div>';
      const byId={}; (d.results||[]).forEach(x=>{ byId[x.test_case_id]=x; });
      const notRun={}; (p.not_run||[]).forEach(n=>{ notRun[n.test_case_id]=n.reason; });
      window._lastTests.forEach(tc=>{
        const x=byId[tc.test_case_id];
        if(x){ const ev=(x.steps||[]).map(s=>'#'+(s.step_number??'')+' '+(s.action||s.action_type||'')
                 +(s.response_code!=null?(' → '+s.response_code):'')+' '+(s.status||'')
                 +(s.actual_result?(' — '+s.actual_result):'')+(s.error?(' — '+s.error):'')).join('\n')
                 +(x.screenshot?('\nscreenshot: '+x.screenshot):'');
               const shots=(x.steps||[]).filter(s=>s.screenshot).map(s=>({n:s.step_number,p:s.screenshot,st:s.status}));
               tc._verdict={status:x.status,label:x.status==='PASS'?'PASS · executed':x.status, evidence:ev, shots, trace:x.trace||''}; }
        else if(notRun[tc.test_case_id]) tc._verdict={status:'NOTRUN',label:'not run',evidence:notRun[tc.test_case_id]};
      });
      renderTests(window._lastTests);
      renderReport(d);
      loadOverview();
    }
    out.innerHTML=h;
  }catch(e){ out.innerHTML='<div class="hk">⚠️ '+esc(String(e))+'</div>'; }
  finally{ btn.disabled=false; }
}
function fileFindings(){
  const d=window._last||{}; const items=[].concat(d.claims||[]).concat(d.leads||[]);
  doExport(items,'findings',$('filebtn'),$('exportmsg'));
}
function fileTests(){
  doExport(window._lastTests||[],'tests',$('gentfilebtn'),$('genjira'));
}

function runScan(){
  const url=$('url').value.trim();
  if(!url){ showError('Enter the URL of your running app (for example http://localhost:8000).'); $('url').focus(); return; }
  if(!$('authorized').checked){ showError('Confirm you own this app or are authorized to test it.'); $('authorized').focus(); return; }
  $('genresults').style.display='none'; $('plan').style.display='none';
  $('go').disabled=true;
  $('err').style.display='none'; $('results').style.display='none';
  $('live').innerHTML=''; $('scanmsg').textContent='Scanning '+url+' …';
  $('scanning').style.display='block';
  if(_es) _es.abort();
  _es=new AbortController();
  streamScan(_es);
}
// The scan is streamed over a POST (fetch) so credentials never appear in a URL.
async function streamScan(ctl){
  try{
    const resp=await api('/api/scan/stream',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({..._scanFields(), repo:$('repo').value.trim(), authorized:'true'}), signal:ctl.signal});
    if(!resp.ok){ let j={}; try{ j=await resp.json(); }catch{} finish(); showError(j.error||('Scan request failed (HTTP '+resp.status+')')); return; }
    const reader=resp.body.getReader(), dec=new TextDecoder(); let buf='';
    for(;;){
      const {value,done}=await reader.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      let i;
      while((i=buf.indexOf('\n\n'))>=0){
        const chunk=buf.slice(0,i); buf=buf.slice(i+2);
        const line=chunk.split('\n').find(l=>l.startsWith('data:')); if(!line) continue;
        let m; try{ m=JSON.parse(line.slice(5).trim()); }catch{ continue; }
        if(m.type==='progress'){ logLine(m.step,m.status,m.detail); }
        else if(m.type==='result'){ finish(); render(m.payload); }
        else if(m.type==='error'){ finish(); showError(m.error||'Scan error'); }
        else if(m.type==='done'){ finish(); }
      }
    }
    finish();
  }catch(e){
    if(e && e.name==='AbortError') return;
    finish(); if($('results').style.display!=='block' && $('err').style.display!=='block') showError('Lost connection to the scanner backend.');
  }
}
// Everything the scan/plan endpoints accept, in one place (sent as a JSON body).
function _scanFields(){
  return {url:$('url').value.trim(), token:$('token').value.trim(), token_b:$('tokenb').value.trim(),
    cookie:$('cookie').value.trim(), spec:$('specurl').value.trim(), headers:$('hdrs').value,
    endpoints:$('eplist').value, examples:$('exbodies').value, read_only:$('readonly').checked, insecure:$('insecure').checked};
}
function finish(){ if(_es){ try{ _es.abort(); }catch{} _es=null; } $('scanning').style.display='none'; $('go').disabled=false; setTimeout(loadRuns,600); }
function showError(m){ const el=$('err'); el.textContent='⚠  '+m; el.style.display='block'; el.scrollIntoView({block:'center',behavior:'smooth'}); }
// an error about a field goes away as soon as the user changes the inputs (it was left standing
// after the box it asked for was ticked — UX walk 2026-10-20)
document.addEventListener('change', e=>{ if(e.target.closest && e.target.closest('.panel')) { const el=$('err'); if(el) el.style.display='none'; } });

function render(d){
  const sc = d.scorecard||{}, cost = sc.cost||{};
  const nClaims = (d.claims||[]).length, nLeads=(d.leads||[]).length;
  // verdict
  const v=$('verdict');
  if(nClaims>0){
    v.className='verdict bad';
    v.innerHTML='<span class="vicon">🛑</span><div><div class="big">'+nClaims+' confirmed issue'+(nClaims>1?'s':'')+'</div>'
      +'<div class="sub">Proven exploitable on '+esc(d.url)+' — plus '+nLeads+' lead'+(nLeads!==1?'s':'')+' to review.</div></div>';
  }else{
    v.className='verdict good';
    v.innerHTML='<span class="vicon">✓</span><div><div class="big">No confirmed issues</div>'
      +'<div class="sub">The app held up to the scan. QAX reports only what it can prove — '+nLeads+' low-confidence lead'+(nLeads!==1?'s':'')+'.</div></div>';
  }
  // stats
  const cstr = cost && cost.tracked ? ('$'+cost.estimated_usd) : '—';
  $('stats').innerHTML = [
    ['n p1',nClaims,'Confirmed · deterministic'],
    ['n p3',nLeads,'Leads · AI / hygiene'],
    ['n',sc.tests_run??'—','Generated tests run'],
    ['n',(sc.duration_sec??'—')+'s','Duration'],
    ['n',cstr,'LLM cost'],
    ['n '+(sc.end_to_end?'ok':''),sc.end_to_end?'yes':'no','End-to-end'],
  ].map(([c,n,l])=>'<div class="stat"><div class="'+c+'">'+esc(n)+'</div><div class="l">'+l+'</div></div>').join('');
  // layers
  const bl=d.by_layer||{};
  $('layers').innerHTML = Object.keys(bl).length
    ? Object.entries(bl).map(([k,v])=>'<span class="chip"><b>'+esc(v)+'</b> '+esc(k)+'</span>').join('') : '';
  // housekeeping — transparency about test data QAX wrote on the target
  const hk=d.housekeeping||{};
  $('housekeeping').innerHTML = (hk.created||0)
    ? '<div class="hk">🧹 QAX created '+hk.created+' disposable test record'+(hk.created>1?'s':'')
      +' on the target and cleaned up '+(hk.cleaned||0)+'.'
      +((hk.cleaned||0)<(hk.created||0) ? ' The rest need manual cleanup (all use qax* test identifiers).' : ' Nothing left behind.')
      +'</div>'
    : '';
  $('housekeeping').innerHTML += notTestedHTML(d);
  // UI tour gallery
  const scr=d.screens||[];
  $('tour').innerHTML = scr.length
    ? '<div class="sec-title">UI tour — '+scr.length+' screen'+(scr.length>1?'s':'')+' captured</div><div class="tourgrid">'
      + scr.map(s=>{const errs=((s.errors||[]).length)+((s.net_errors||[]).length);
          return '<div class="shot"><img src="'+esc(s.screenshot_url)+'" loading="lazy" alt="">'
            +'<div class="cap"><span class="u" title="'+esc(s.url||'')+'">'+esc((s.title||s.url||'screen').slice(0,30))+'</span>'
            +(errs?'<span class="bad">'+errs+' ⚠</span>':'<span class="okb">✓</span>')+'</div></div>';
        }).join('') + '</div>'
    : '';
  // findings
  const order={P1:0,P2:1,P3:2};
  const card = b => {
    const sev=(b.severity||'P?').toUpperCase();
    return '<div class="finding sev-'+esc(sev)+'"><div class="top">'
      +'<span class="sev '+esc(sev)+'">'+esc(sev)+'</span>'
      +'<span class="title">'+esc(b.title)+'</span>'
      +'<span class="meta">'+(b.source?'<span class="tag">'+esc(b.source)+'</span>':'')+'</span></div>'
      +(b.target?'<div class="target">'+esc(b.target)+'</div>':'')
      +(b.evidence?'<div class="ev">'+esc(b.evidence)+'</div>':'')
      +((b.remediation&&b.remediation.fix)?'<div class="fix"><div class="k">Why it matters</div>'
        +'<div class="why">'+esc(b.remediation.why)+'</div>'
        +'<div class="k">How to fix</div><div class="how">'+esc(b.remediation.fix)+'</div></div>':'')
      +'</div>';
  };
  let html='';
  const claims=(d.claims||[]).slice().sort((a,b)=>order[(a.severity||'P3').toUpperCase()]-order[(b.severity||'P3').toUpperCase()]);
  if(claims.length){ html+='<div class="sec-title">Confirmed — proven exploitable</div>'+claims.map(card).join(''); }
  if((d.leads||[]).length){ html+='<div class="sec-title">Leads — flagged, not confirmed</div>'+(d.leads).map(card).join(''); }
  $('findings').innerHTML=html;
  window._last=d; $('rtool').style.display='flex';
  $('results').style.display='block';
  $('results').scrollIntoView({behavior:'smooth',block:'start'});
}

/* ---- shareable / printable report (self-contained HTML) ---- */
function buildReportHTML(d){
  const sc=d.scorecard||{}, cost=sc.cost||{};
  const nC=(d.claims||[]).length, nL=(d.leads||[]).length;
  const when=new Date().toLocaleString();
  const cstr = cost&&cost.tracked ? ('$'+cost.estimated_usd) : '—';
  const sevColor={P1:'#d11f3f',P2:'#c77700',P3:'#5b6b84'};
  const fRow = b=>{const s=(b.severity||'P?').toUpperCase();return(
    '<div class="f" style="border-left:4px solid '+(sevColor[s]||'#5b6b84')+'">'
    +'<div class="fh"><span class="sev" style="background:'+(sevColor[s]||'#5b6b84')+'1a;color:'+(sevColor[s]||'#5b6b84')+';border:1px solid '+(sevColor[s]||'#5b6b84')+'55">'+esc(s)+'</span>'
    +'<span class="ft">'+esc(b.title)+'</span>'+(b.source?'<span class="src">'+esc(b.source)+'</span>':'')+'</div>'
    +(b.target?'<div class="tg">'+esc(b.target)+'</div>':'')
    +(b.evidence?'<div class="ev">'+esc(b.evidence)+'</div>':'')
    +((b.remediation&&b.remediation.fix)?'<div class="rfix"><div class="rk">Why it matters</div>'
      +'<div class="rwhy">'+esc(b.remediation.why)+'</div><div class="rk">How to fix</div><div>'+esc(b.remediation.fix)+'</div></div>':'')
    +'</div>');};
  const order={P1:0,P2:1,P3:2};
  const claims=(d.claims||[]).slice().sort((a,b)=>order[(a.severity||'P3').toUpperCase()]-order[(b.severity||'P3').toUpperCase()]);
  const stat=(n,l)=>'<td><div class="n">'+esc(n)+'</div><div class="l">'+l+'</div></td>';
  const layers=Object.entries(d.by_layer||{}).map(([k,v])=>esc(v)+' '+esc(k)).join(' · ')||'—';
  return '<!doctype html><html><head><meta charset="utf-8"><title>QAX report — '+esc(d.url)+'</title>'
  +'<style>'
  +'*{box-sizing:border-box}body{font-family:-apple-system,Segoe UI,Roboto,Inter,sans-serif;color:#17202e;background:#f5f7fb;margin:0;padding:40px}'
  +'.doc{max-width:820px;margin:0 auto;background:#fff;border:1px solid #e4e9f2;border-radius:14px;padding:38px;box-shadow:0 10px 40px -24px #15213a}'
  +'.brand{font-weight:800;font-size:20px;color:#4f46e5;letter-spacing:-.5px}.brand span{color:#06b6d4}'
  +'.sub{color:#6b7689;font-size:12.5px;margin-top:2px}'
  +'h1{font-size:22px;margin:22px 0 4px}.meta{color:#6b7689;font-size:13px;margin-bottom:18px}'
  +'.verdict{border-radius:12px;padding:16px 18px;font-weight:700;font-size:18px;margin:10px 0 18px}'
  +'.vbad{background:#fdecef;color:#b3243c;border:1px solid #f3c2cd}.vgood{background:#e8f8f1;color:#17795a;border:1px solid #bce9d6}'
  +'table.stats{width:100%;border-collapse:collapse;margin:0 0 10px}table.stats td{border:1px solid #e4e9f2;border-radius:0;padding:12px;text-align:center;width:16.6%}'
  +'.stats .n{font-size:19px;font-weight:800}.stats .l{font-size:10.5px;color:#6b7689;text-transform:uppercase;letter-spacing:.4px;margin-top:3px}'
  +'.lay{font-size:12.5px;color:#6b7689;margin:4px 0 20px}'
  +'.sct{font-size:12px;color:#6b7689;text-transform:uppercase;letter-spacing:.5px;font-weight:700;margin:22px 0 10px;border-top:1px solid #eef1f7;padding-top:16px}'
  +'.f{background:#fbfcfe;border:1px solid #e9edf5;border-radius:10px;padding:13px 15px;margin-bottom:10px}'
  +'.fh{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.sev{font-size:11px;font-weight:800;padding:2px 8px;border-radius:6px}'
  +'.ft{font-weight:600;font-size:14.5px}.src{margin-left:auto;font-size:11px;color:#6b7689;background:#eef1f7;border:1px solid #e0e6f0;border-radius:6px;padding:2px 8px}'
  +'.tg{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#0a74b8;margin-top:6px}'
  +'.ev{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#2a3344;background:#f1f4f9;border:1px solid #e4e9f2;border-radius:8px;padding:10px 11px;margin-top:9px;white-space:pre-wrap;word-break:break-word}'
  +'.rfix{background:#eef4ff;border:1px solid #d6e2f7;border-radius:8px;padding:10px 12px;margin-top:9px;font-size:12.5px}'
  +'.rk{color:#3b5bdb;font-weight:700;font-size:10.5px;text-transform:uppercase;letter-spacing:.4px}.rwhy{color:#3a4557;margin:3px 0 8px}'
  +'.rgal{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}'
  +'.rshot{border:1px solid #e4e9f2;border-radius:8px;overflow:hidden}.rshot img{width:100%;height:100px;object-fit:cover;object-position:top;display:block;border-bottom:1px solid #eef1f7}'
  +'.rcap{padding:6px 8px;font-size:11px;color:#6b7689;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}'
  +'.foot{color:#8a95a7;font-size:11.5px;margin-top:26px;border-top:1px solid #eef1f7;padding-top:14px}'
  +'@media print{body{background:#fff;padding:0}.doc{border:0;box-shadow:none;max-width:none}}'
  +'</style></head><body><div class="doc">'
  +'<div class="brand">QA<span>X</span></div><div class="sub">deterministic exploit scanner — security &amp; QA report</div>'
  +'<h1>Scan report</h1><div class="meta">Target: <b>'+esc(d.url)+'</b> · Generated '+esc(when)+' · Quick scan</div>'
  +(nC>0
     ? '<div class="verdict vbad">🛑 '+nC+' confirmed issue'+(nC>1?'s':'')+' — proven exploitable ('+nL+' lead'+(nL!==1?'s':'')+' to review)</div>'
     : '<div class="verdict vgood">✓ No confirmed issues — the app held up ('+nL+' low-confidence lead'+(nL!==1?'s':'')+')</div>')
  +'<table class="stats"><tr>'+stat(nC,'Confirmed · deterministic')+stat(nL,'Leads · may vary')+stat(sc.tests_run??'—','Tests')+stat((sc.duration_sec??'—')+'s','Duration')+stat(cstr,'LLM cost')+stat(sc.end_to_end?'yes':'no','End-to-end')+'</tr></table>'
  +'<div class="lay">Caught by: '+layers+'</div>'
  +(claims.length?'<div class="sct">Confirmed — proven exploitable</div>'+claims.map(fRow).join(''):'')
  +((d.leads||[]).length?'<div class="sct">Leads — flagged, not confirmed</div>'+(d.leads).map(fRow).join(''):'')
  +((d.screens||[]).length?'<div class="sct">UI tour — '+(d.screens.length)+' screen'+(d.screens.length>1?'s':'')+' captured</div><div class="rgal">'
    +(d.screens).map(s=>{const src=s.dataurl||s.screenshot_url||'';const errs=((s.errors||[]).length)+((s.net_errors||[]).length);
       return '<div class="rshot">'+(src?'<img src="'+esc(src)+'" alt="">':'')
         +'<div class="rcap">'+esc((s.title||s.url||'screen').slice(0,40))+(errs?' · '+errs+' issue'+(errs>1?'s':''):' · ✓')+'</div></div>';}).join('')
    +'</div>':'')
  +(((d.housekeeping||{}).created||0)?'<div class="sct">Test-data footprint</div><div class="rwhy">QAX created '
     +d.housekeeping.created+' disposable test record(s) on the target and cleaned up '+(d.housekeeping.cleaned||0)
     +'. Any remainder uses qax* identifiers and can be removed manually.</div>':'')
  +'<div class="foot">Generated by QAX. Confirmed (P1/P2) issues were proven on the live app; leads (P3) were flagged but not confirmed — reported separately, on purpose. Scan run locally against an app you control.</div>'
  +'</div></body></html>';
}
async function _dataUrl(u){
  try{ const r=await fetch(u); if(!r.ok) return ''; const b=await r.blob();
    return await new Promise(res=>{const fr=new FileReader();fr.onload=()=>res(fr.result);fr.onerror=()=>res('');fr.readAsDataURL(b);}); }
  catch{ return ''; }
}
async function _withShots(d){
  const c=JSON.parse(JSON.stringify(d||{}));
  await Promise.all((c.screens||[]).map(async s=>{ if(s.screenshot_url) s.dataurl=await _dataUrl(s.screenshot_url); }));
  return c;  // self-contained: screenshots inlined as base64 so the report shares/prints standalone
}
async function downloadReport(){
  if(!window._last) return;
  let host='app'; try{ host=new URL(window._last.url).host.replace(/[:.]/g,'-'); }catch{}
  const d=await _withShots(window._last);
  const blob=new Blob([buildReportHTML(d)],{type:'text/html'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='QAX-report-'+host+'-'+new Date().toISOString().slice(0,10)+'.html';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(()=>URL.revokeObjectURL(a.href),4000);
}
async function printReport(){
  if(!window._last) return;
  const d=await _withShots(window._last);
  const w=window.open('','_blank');
  w.document.write(buildReportHTML(d)); w.document.close();
  setTimeout(()=>{ w.focus(); w.print(); },350);
}

// ── Plan preview, read-only honesty, run history (PLAN.md Phase 1 item 7) ──────────────────────────
function notTestedHTML(d){
  const gaps=d.coverage_gaps||[], left=d.left_on_target||[];
  let h='';
  if(d.read_only) h+='<div class="hk">🔒 Read-only scan — QAX sent no write request to the target.</div>';
  if(gaps.length){
    const groups={};
    gaps.forEach(g=>{ const k=g.needs||'other'; (groups[k]=groups[k]||[]).push(g); });
    const hints={'token_b':"a second, ordinary user's token", 'auth-token':'a valid token or session cookie',
      'valid-body':'an example valid request body', 'api-surface':"the app's OpenAPI spec (QAX found few endpoints)",
      'write-permission':'a scan without read-only, once you may write test data'};
    // a check that found something here RAN — read-only only kept it from its write requests
    const found=new Set(((d.claims||[]).map(c=>c.probe||c.source)).filter(Boolean));
    const partly=gaps.filter(g=>found.has(g.probe)||/not \(fully\) run/.test(g.reason||'')).length;
    h+='<div class="sec-title" style="margin-top:18px">Not fully tested — '+gaps.length+' check'+(gaps.length>1?'s':'')
      +(partly?(' ('+partly+' ran in part: only the parts below were skipped)'):'')
      +' <span style="color:var(--muted);font-weight:500">(quiet here means "unknown", not "safe")</span></div>';
    Object.entries(groups).forEach(([k,list])=>{
      h+='<div class="hk"><b>'+esc(list.map(g=>g.probe).join(', '))+'</b><br><span style="color:var(--muted)">To enable: '
        +esc(hints[k]||k)+'</span></div>';
    });
  }
  if(left.length){
    h+='<div class="hk">⚠ '+left.length+' test record'+(left.length>1?'s':'')+' left on the target (see the JSON of this run).</div>';
  }
  return h;
}

async function previewPlan(){
  const url=$('url').value.trim();
  if(!url){ showError('Enter the URL of your running app (for example http://localhost:8000).'); $('url').focus(); return; }
  const box=$('plan'); box.style.display='block'; box.innerHTML='<div class="hint">Reading the app (read-only requests only)…</div>';
  $('planbtn').disabled=true;
  try{
    const r=await api('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(_scanFields())});
    const p=await r.json();
    if(!r.ok){ box.innerHTML='<div class="hk">⚠ '+esc(p.error||'Could not build a plan')+'</div>'; return; }
    const c=p.checks||{}, s=p.surface||{};
    let h='<div class="sec-title" style="margin-top:0">Scan plan — nothing has been attacked yet</div>';
    h+='<div class="stats" style="margin:10px 0"><span class="chip"><b>'+esc(s.endpoint_count)+'</b> endpoints found</span>'
      +'<span class="chip"><b>'+esc(c.will_run)+'</b> of '+esc(c.total)+' checks will run</span>'
      +'<span class="chip"><b>'+((c.skipped_because_read_only||[]).length)+'</b> skipped entirely (read-only)</span>'
      +(c.read_only_note?'':'')+'</div><div class="hint">In read-only, other checks also leave out their write requests (POST/PUT/DELETE), so the result may list them as “not fully tested”.</div>';
    h+='<div class="hint">'+esc(p.note||'')+' Surface from: '+esc(s.source||'')+'.</div>';
    if((s.sample||[]).length) h+='<div class="hint" style="margin-top:8px"><b>Endpoints (sample):</b> <code>'+s.sample.slice(0,12).map(esc).join('</code> <code>')+'</code></div>';
    if((c.writes_data||[]).length) h+='<div class="hint" style="margin-top:8px"><b>Checks that write data:</b> '+c.writes_data.map(esc).join(', ')
      +(p.read_only?' — <i>skipped, because read-only is on</i>':' — <i>will create small test records</i>')+'</div>';
    (p.missing_prerequisites||[]).forEach(m=>{ h+='<div class="hk" style="margin-top:8px">⚠ '+esc(m)+'</div>'; });
    h+='<div class="row" style="margin-top:14px;justify-content:flex-end"><button class="btn" onclick="runScan()">Looks right — run the scan</button></div>';
    box.innerHTML=h;
  }catch(e){ box.innerHTML='<div class="hk">⚠ '+esc(String(e))+'</div>'; }
  finally{ $('planbtn').disabled=false; }
}

async function loadRuns(){
  try{
    const r=await api('/api/runs'); const j=await r.json(); const runs=j.runs||[];
    $('history').style.display=(runs.length && $('panel-scan').style.display!=='none')?'block':'none';
    $('runs').innerHTML=runs.slice(0,15).map(x=>
      '<div class="hk" style="cursor:pointer" role="button" tabindex="0" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();openRun(\''+esc(x.run_id)+'\')}" onclick="openRun(\''+esc(x.run_id)+'\')">'
      +'<b>'+esc(x.scanned_at||'')+'</b> · '+esc(x.url||'')
      +' · <b>'+x.claims+'</b> confirmed · '+x.leads+' leads · '+x.not_tested+' not tested'
      +(x.read_only?' · 🔒 read-only':'')+' <span style="color:var(--muted)">(open)</span></div>').join('');
  }catch(e){ /* history is a convenience; never block the page */ }
}
async function openRun(id){
  try{
    const r=await api('/api/runs/'+encodeURIComponent(id)); if(!r.ok) return;
    const d=await r.json(); $('genresults').style.display='none';
    $('err').style.display='none'; render(d); window._last=d; window.scrollTo({top:0,behavior:'smooth'});
  }catch(e){}
}
loadRuns();
$('url').addEventListener('keydown',e=>{ if(e.key==='Enter') runScan(); });
// ?url=... pre-fills the target (used by `qax demo --web`); only http(s) values are accepted.
(function(){ try{ const u=new URLSearchParams(location.search).get('url'); if(u && /^https?:\/\//i.test(u)) $('url').value=u; }catch(e){} })();
// Deep links from the homepage: /app#functional (and the old /app#generate) open functional QA, /app#scan the scanner.
function _modeFromHash(){ if(location.hash==='#generate') setMode('gen'); else if(location.hash==='#functional') setMode('func'); else if(location.hash==='#scan') setMode('scan'); }
window.addEventListener('hashchange', _modeFromHash); _modeFromHash();
