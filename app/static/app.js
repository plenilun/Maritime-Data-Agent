const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const state = { datasets: [], terms: [], model: null, conversations: [], activeConversationId: null, pendingDeleteId: null };
const CONVERSATIONS_KEY = 'smart-query-conversations';
const ACTIVE_CONVERSATION_KEY = 'smart-query-active-conversation';
const titles = {
  chat: ['问数助手', '选择数据表，用自然语言获得答案'],
  datasets: ['数据表', '管理已上传的数据文件'],
  terms: ['术语库', '统一业务概念和计算口径'],
  settings: ['模型设置', '选择你自己的模型 API'],
};

async function api(url, options = {}) {
  const response = await fetch(url, options);
  let body;
  try { body = await response.json(); } catch { body = { detail: response.statusText }; }
  if (!response.ok) throw new Error(body.detail || '请求失败');
  return body;
}

function toast(text) {
  const el = $('#toast'); el.textContent = text; el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2500);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function createConversation() { return { id: crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`, title: '新对话', datasetId: '', messages: [], updatedAt: Date.now() }; }
function activeConversation() { return state.conversations.find(item => item.id === state.activeConversationId); }
function saveConversations() { localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(state.conversations)); localStorage.setItem(ACTIVE_CONVERSATION_KEY, state.activeConversationId || ''); }
function renderConversationList() { $('#conversation-list').innerHTML=state.conversations.map(item=>`<div class="conversation-item ${item.id===state.activeConversationId?'active':''}" data-conversation-id="${item.id}"><button class="conversation-select" type="button"><span class="conversation-title">${escapeHtml(item.title)}</span><small class="conversation-meta">${item.messages.length} 条消息 · ${item.datasetId?'手动选择':'自动选表'}</small></button><span class="conversation-actions"><button type="button" data-action="rename" title="重命名">重命名</button><button type="button" data-action="delete" class="${state.pendingDeleteId===item.id?'confirm-delete':''}" title="删除">${state.pendingDeleteId===item.id?'确认删除':'删除'}</button></span></div>`).join(''); }
function welcomeHtml() { return '<div class="welcome"><div class="orb">✦</div><h2>想从数据里了解什么？</h2><p>我会检索相关业务术语，生成并执行 SQL，然后用自然语言告诉你结果。</p><div class="examples"><button>这张表一共有多少条数据？</button><button>按类别统计数量，找出最多的三类</button><button>最近一个月的数据趋势如何？</button></div></div>'; }
function renderMessages() { const item=activeConversation();$('#messages').innerHTML=item?.messages.length?'':welcomeHtml();item?.messages.forEach(message=>addMessage(message.role,message.content,message.details,false)); }
function switchConversation(id) { if(!state.conversations.some(item=>item.id===id))return;state.activeConversationId=id;const item=activeConversation();$('#chat-dataset').value=state.datasets.some(d=>d.id===item.datasetId)?item.datasetId:'';if(item.datasetId&&!state.datasets.some(d=>d.id===item.datasetId))item.datasetId='';renderConversationList();renderMessages();saveConversations();showView('chat'); }
function addConversation() { const item=createConversation();item.datasetId=$('#chat-dataset').value||'';state.conversations.unshift(item);state.activeConversationId=item.id;saveConversations();renderConversationList();renderMessages();showView('chat');$('#question').focus(); }

$('#new-conversation').onclick=addConversation;
$('#conversation-list').onclick=e=>{const row=e.target.closest('[data-conversation-id]');if(!row)return;const id=row.dataset.conversationId,action=e.target.closest('[data-action]')?.dataset.action;if(action==='rename'){state.pendingDeleteId=null;const title=row.querySelector('.conversation-title');title.contentEditable='true';title.classList.add('editing');title.focus();document.getSelection()?.selectAllChildren(title);return}if(action==='delete'){if(state.conversations.length===1){toast('至少保留一个对话');return}if(state.pendingDeleteId!==id){state.pendingDeleteId=id;renderConversationList();toast('请再次点击“确认删除”');return}state.pendingDeleteId=null;state.conversations=state.conversations.filter(x=>x.id!==id);if(state.activeConversationId===id)state.activeConversationId=state.conversations[0].id;saveConversations();switchConversation(state.activeConversationId);toast('对话已删除');return}state.pendingDeleteId=null;switchConversation(id)};
function commitConversationTitle(target) { const row=target.closest('[data-conversation-id]'),item=state.conversations.find(x=>x.id===row?.dataset.conversationId),title=target.textContent.trim();if(item&&title){item.title=title.slice(0,40);item.updatedAt=Date.now();saveConversations()}renderConversationList(); }
$('#conversation-list').onkeydown=e=>{if(e.target.matches('.conversation-title.editing')&&e.key==='Enter'){e.preventDefault();commitConversationTitle(e.target)}if(e.target.matches('.conversation-title.editing')&&e.key==='Escape'){e.preventDefault();renderConversationList()}};
$('#conversation-list').onfocusout=e=>{if(e.target.matches('.conversation-title.editing'))commitConversationTitle(e.target)};

function showView(name) {
  $$('.nav').forEach(x => x.classList.toggle('active', x.dataset.view === name));
  $$('.view').forEach(x => x.classList.toggle('active', x.id === `view-${name}`));
  $('#page-title').textContent = titles[name][0]; $('#page-subtitle').textContent = titles[name][1];
  if (name === 'terms') loadTerms();
}

$$('.nav').forEach(x => x.onclick = () => showView(x.dataset.view));
$$('[data-jump]').forEach(x => x.onclick = () => showView(x.dataset.jump));

function datasetOptions(includeAll = false) {
  const head = includeAll ? '<option value="">全部数据表</option>' : '<option value="">自动选择数据表</option>';
  return head + state.datasets.map(d => `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join('');
}

async function loadDatasets() {
  state.datasets = await api('/api/datasets');
  const chatValue = $('#chat-dataset').value;
  $('#chat-dataset').innerHTML = datasetOptions();
  $('#chat-dataset').value = state.datasets.some(d => d.id === chatValue) ? chatValue : '';
  const conversation=activeConversation();if(conversation){$('#chat-dataset').value=state.datasets.some(d=>d.id===conversation.datasetId)?conversation.datasetId:'';if(conversation.datasetId&&!state.datasets.some(d=>d.id===conversation.datasetId))conversation.datasetId='';saveConversations()}
  $('#term-dataset-filter').innerHTML = datasetOptions(true);
  $('[name="dataset_id"]', $('#term-form')).innerHTML = '<option value="">全局术语</option>' + state.datasets.map(d => `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join('');
  $('#dataset-list').innerHTML = state.datasets.length ? state.datasets.map(d => `
    <article class="card"><h3>${escapeHtml(d.name)}</h3><p>${escapeHtml(d.source_file)}</p><small>${d.row_count.toLocaleString()} 行 · ${d.columns.length} 个字段</small>
    <div class="card-footer"><span>${new Date(d.created_at).toLocaleString()}</span><button class="danger" data-delete-dataset="${d.id}">删除</button></div></article>`).join('') : '<div class="empty">还没有数据表，上传一个 CSV 或 Excel 开始问数。</div>';
}

$('#chat-dataset').onchange=()=>{const item=activeConversation();if(item){item.datasetId=$('#chat-dataset').value;item.updatedAt=Date.now();saveConversations()}};

$('#upload-open').onclick = () => $('#upload-dialog').showModal();
$('#term-open').onclick = () => $('#term-dialog').showModal();
$('#term-import-open').onclick = () => $('#term-import-dialog').showModal();
$$('[data-close]').forEach(x => x.onclick = () => x.closest('dialog').close());

$('#upload-form').onsubmit = async e => {
  e.preventDefault(); const button = $('button.primary', e.target); button.disabled = true; button.textContent = '上传中…';
  try { await api('/api/datasets', { method: 'POST', body: new FormData(e.target) }); e.target.reset(); $('#upload-dialog').close(); await loadDatasets(); toast('数据表上传成功'); }
  catch (err) { toast(err.message); } finally { button.disabled = false; button.textContent = '开始上传'; }
};

$('#dataset-list').onclick = async e => {
  const id = e.target.dataset.deleteDataset; if (!id || !confirm('确定删除这个数据表及其关联术语吗？')) return;
  try { await api(`/api/datasets/${id}`, {method:'DELETE'}); await loadDatasets(); toast('数据表已删除'); } catch(err) { toast(err.message); }
};

async function loadTerms() {
  const dataset = $('#term-dataset-filter').value, q = $('#term-search').value.trim();
  state.terms = await api(`/api/terms?dataset_id=${encodeURIComponent(dataset)}&q=${encodeURIComponent(q)}`);
  $('#term-list').innerHTML = state.terms.length ? state.terms.map(t => {
    const ds = state.datasets.find(d => d.id === t.dataset_id);
    return `<div class="term"><b>${escapeHtml(t.term)}<small>${escapeHtml(t.synonyms || '无同义词')}</small></b><span>${escapeHtml(t.definition)}</span><div><small>${escapeHtml(ds?.name || '全局')}</small> <button class="danger" data-delete-term="${t.id}">删除</button></div></div>`;
  }).join('') : '<div class="empty">没有匹配的术语。</div>';
}

let searchTimer; $('#term-search').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadTerms, 250); };
$('#term-dataset-filter').onchange = loadTerms;
$('#term-form').onsubmit = async e => {
  e.preventDefault(); const data = Object.fromEntries(new FormData(e.target)); data.dataset_id ||= null;
  try { await api('/api/terms', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); e.target.reset(); $('#term-dialog').close(); await loadTerms(); toast('术语已保存'); } catch(err) { toast(err.message); }
};
$('#term-import-form').onsubmit = async e => {
  e.preventDefault(); const button = $('button.primary', e.target); button.disabled = true; button.textContent = '导入中…';
  try { const result = await api('/api/terms/import', {method:'POST', body:new FormData(e.target)}); e.target.reset(); $('#term-import-dialog').close(); await loadTerms(); toast(`成功导入 ${result.imported} 条，跳过 ${result.skipped} 条重复术语`); }
  catch(err) { toast(err.message); } finally { button.disabled = false; button.textContent = '开始导入'; }
};
$('#term-list').onclick = async e => { const id=e.target.dataset.deleteTerm;if(!id)return;try{await api(`/api/terms/${id}`,{method:'DELETE'});await loadTerms();toast('术语已删除')}catch(err){toast(err.message)}};

const providers = {
  openai: {base_url:'https://api.openai.com/v1', model:'gpt-4.1-mini'},
  deepseek: {base_url:'https://api.deepseek.com/v1', model:'deepseek-chat'},
  qwen: {base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1', model:'qwen-plus'},
  custom: {base_url:'', model:''},
};
$$('[data-provider]').forEach(button => button.onclick = () => {
  $$('[data-provider]').forEach(x => x.classList.remove('active')); button.classList.add('active');
  const p=providers[button.dataset.provider]; $('#base-url').value=p.base_url; $('#model-name').value=p.model;
});
$('#toggle-key').onclick = () => { const x=$('#api-key');x.type=x.type==='password'?'text':'password';$('#toggle-key').textContent=x.type==='password'?'显示':'隐藏'; };
function readModel() { return {api_key:$('#api-key').value.trim(),base_url:$('#base-url').value.trim(),model:$('#model-name').value.trim()}; }
function saveModel() { state.model=readModel();sessionStorage.setItem('smart-query-model',JSON.stringify(state.model));updateModelStatus(); }
function updateModelStatus(){const el=$('.status');el.classList.toggle('ready',!!state.model);$('#model-status').textContent=state.model?state.model.model:'未配置模型'}
$('#model-form').onsubmit = e => { e.preventDefault();saveModel();toast('模型配置已保存到当前会话');showView('chat'); };
$('#test-model').onclick = async () => { const el=$('#model-result');el.textContent='正在测试连接…';try{const result=await api('/api/model/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(readModel())});el.textContent=`✓ ${result.message}`;}catch(err){el.textContent=`连接失败：${err.message}`;} };

function splitAnswerSections(content) {
  const names = ['直接结论', '统计范围', '结果明细', '统计口径', '可追溯信息', '注意事项'];
  const pattern = new RegExp(`(?:^|\\n)(${names.join('|')})[:：]\\s*\\n?`, 'g');
  const sections = {}; let match, last = null, lastIndex = 0;
  while ((match = pattern.exec(content)) !== null) {
    if (last) sections[last] = content.slice(lastIndex, match.index).trim();
    last = match[1]; lastIndex = pattern.lastIndex;
  }
  if (last) sections[last] = content.slice(lastIndex).trim();
  return sections;
}
function renderSection(title, body) { return body ? `<section class="answer-section"><b>${escapeHtml(title)}</b><div class="answer-section-body">${escapeHtml(body)}</div></section>` : ''; }
function renderAnswerContent(content, details) {
  if (!details) return escapeHtml(content);
  const sections = splitAnswerSections(content);
  if (!sections['直接结论'] && !sections['结果明细']) return escapeHtml(content);
  const visible = `${renderSection('直接结论', sections['直接结论'])}${renderSection('结果明细', sections['结果明细'])}`;
  const folded = `${renderSection('统计范围', sections['统计范围'])}${renderSection('统计口径', sections['统计口径'])}${renderSection('可追溯信息', sections['可追溯信息'])}${renderSection('注意事项', sections['注意事项'])}`;
  return `<div class="answer-main">${visible || escapeHtml(content)}</div>${folded ? `<details class="answer-more"><summary>查看统计范围、统计口径和可追溯信息</summary>${folded}</details>` : ''}`;
}
function addMessage(role, content, details, persist = true) {
  $('.welcome')?.remove(); const wrap=document.createElement('div');wrap.className=`message ${role}`;
  let extra=''; if(details){
    const heads=details.columns.map(x=>`<th>${escapeHtml(x)}</th>`).join('');
    const rows=details.rows.slice(0,30).map(r=>`<tr>${details.columns.map(c=>`<td>${escapeHtml(r[c])}</td>`).join('')}</tr>`).join('');
    const metrics=details.metrics||{},tokens=metrics.total_tokens==null?'模型未返回':metrics.total_tokens.toLocaleString();
    const sqlTime = metrics.sql_generation_cached ? '缓存命中' : formatDuration(metrics.sql_generation_elapsed_ms);
    extra=`<div class="run-stats"><span>总耗时 <b>${formatDuration(metrics.total_elapsed_ms)}</b></span><span>Token <b>${tokens}</b></span>${metrics.sql_generation_cached?'<span>SQL <b>缓存命中</b></span>':''}</div><details class="meta-panel"><summary>查看 SQL 生成过程、SQL 和查询结果</summary><section class="reasoning-summary"><b>SQL 生成依据</b><p>${escapeHtml(details.reasoning_summary||'暂无生成依据记录')}</p></section><div class="metric-grid"><span>SQL 生成<strong>${sqlTime} · ${formatTokens(metrics.sql_generation_tokens)}</strong></span><span>数据库查询<strong>${formatDuration(metrics.query_elapsed_ms)}</strong></span><span>答案生成<strong>${formatDuration(metrics.answer_generation_elapsed_ms)} · ${formatTokens(metrics.answer_generation_tokens)}</strong></span><span>Token 明细<strong>输入 ${formatTokens(metrics.prompt_tokens)} / 输出 ${formatTokens(metrics.completion_tokens)}</strong></span></div><pre>${escapeHtml(details.sql)}</pre>${rows?`<div class="result-table"><table><thead><tr>${heads}</tr></thead><tbody>${rows}</tbody></table></div>`:'<p>查询结果为空</p>'}</details>`;
  }
  const renderedContent = role === 'assistant' ? renderAnswerContent(content, details) : escapeHtml(content);
  wrap.innerHTML=`<div class="bubble">${renderedContent}${extra}</div>`;$('#messages').append(wrap);$('#messages').scrollTop=$('#messages').scrollHeight;if(persist){const item=activeConversation();if(item){item.messages.push({role,content,details:details||null});item.updatedAt=Date.now();if(item.title==='新对话'&&role==='user')item.title=content.slice(0,18);saveConversations();renderConversationList()}}return wrap;
}
function formatDuration(ms){if(ms==null)return'--';return ms<1000?`${ms} ms`:`${(ms/1000).toFixed(2)} s`}
function formatTokens(value){return value==null?'-- Token':`${Number(value).toLocaleString()} Token`}
$('#messages').onclick=e=>{const button=e.target.closest('.examples button');if(button){$('#question').value=button.textContent;$('#question').focus()}};
$('#ask-form').onsubmit = async e => {
  e.preventDefault();const question=$('#question').value.trim(),dataset_id=$('#chat-dataset').value||null;if(!question)return;if(!state.datasets.length){toast('请先上传数据表');showView('datasets');return}if(!state.model){toast('请先配置模型 API');showView('settings');return}
  const conversationId=state.activeConversationId;addMessage('user',question);$('#question').value='';const loading=addMessage('assistant','正在检索术语、生成 SQL 并查询数据…',null,false);$('.send').disabled=true;
  const finish=(content,details=null)=>{loading.remove();const item=state.conversations.find(x=>x.id===conversationId);if(!item)return;if(state.activeConversationId===conversationId)addMessage('assistant',content,details);else{item.messages.push({role:'assistant',content,details});item.updatedAt=Date.now();saveConversations();renderConversationList()}};
  try{const result=await api('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset_id,question,model:state.model})});finish(result.answer,result)}catch(err){finish(`问数失败：${err.message}`)}finally{$('.send').disabled=false}
};

try { state.conversations=JSON.parse(localStorage.getItem(CONVERSATIONS_KEY)||sessionStorage.getItem(CONVERSATIONS_KEY))||[]; } catch {}
if(!Array.isArray(state.conversations)||!state.conversations.length)state.conversations=[createConversation()];
state.activeConversationId=localStorage.getItem(ACTIVE_CONVERSATION_KEY)||sessionStorage.getItem(ACTIVE_CONVERSATION_KEY);if(!state.conversations.some(x=>x.id===state.activeConversationId))state.activeConversationId=state.conversations[0].id;saveConversations();
try { state.model=JSON.parse(sessionStorage.getItem('smart-query-model')); } catch {}
if(state.model){$('#api-key').value=state.model.api_key;$('#base-url').value=state.model.base_url;$('#model-name').value=state.model.model}
updateModelStatus();renderConversationList();renderMessages();loadDatasets().catch(err=>toast(err.message));
