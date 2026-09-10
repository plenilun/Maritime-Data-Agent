const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const state = { datasets: [], terms: [], relationships: [], model: null, conversations: [], activeConversationId: null, pendingDeleteId: null, editingTermId: null, editingRelationshipId: null };
const CONVERSATIONS_KEY = 'smart-query-conversations';
const ACTIVE_CONVERSATION_KEY = 'smart-query-active-conversation';
const MODEL_CONFIG_KEY = 'smart-query-model';
const titles = {
  chat: ['问数助手', '选择数据表，用自然语言获得答案'],
  datasets: ['数据表', '管理已上传的数据文件'],
  terms: ['术语库', '统一业务概念和计算口径'],
  codes: ['业务数值映射', '维护枚举编码、业务名称、同义词和字段绑定'],
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

function createConversation() { return { id: crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`, title: '新对话', datasetId: '', secondaryDatasetId: '', relatedDatasetIds: [], messages: [], updatedAt: Date.now() }; }
function activeConversation() { return state.conversations.find(item => item.id === state.activeConversationId); }
function saveConversations() { localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(state.conversations)); localStorage.setItem(ACTIVE_CONVERSATION_KEY, state.activeConversationId || ''); }
function renderConversationList() { $('#conversation-list').innerHTML=state.conversations.map(item=>`<div class="conversation-item ${item.id===state.activeConversationId?'active':''}" data-conversation-id="${item.id}"><button class="conversation-select" type="button"><span class="conversation-title">${escapeHtml(item.title)}</span><small class="conversation-meta">${item.messages.length} 条消息 · ${item.datasetId?'手动选择':'自动选表'}</small></button><span class="conversation-actions"><button type="button" data-action="rename" title="重命名">重命名</button><button type="button" data-action="delete" class="${state.pendingDeleteId===item.id?'confirm-delete':''}" title="删除">${state.pendingDeleteId===item.id?'确认删除':'删除'}</button></span></div>`).join(''); }
function welcomeHtml() { return '<div class="welcome"><div class="orb">✦</div><h2>想从数据里了解什么？</h2><p>我会检索相关业务术语，生成并执行 SQL，然后用自然语言告诉你结果。</p><div class="examples"><button>这张表一共有多少条数据？</button><button>按类别统计数量，找出最多的三类</button><button>最近一个月的数据趋势如何？</button></div></div>'; }
function renderMessages() { const item=activeConversation();$('#messages').innerHTML=item?.messages.length?'':welcomeHtml();item?.messages.forEach(message=>addMessage(message.role,message.content,message.details,false)); }
function switchConversation(id) { if(!state.conversations.some(item=>item.id===id))return;state.activeConversationId=id;const item=activeConversation();$('#chat-dataset').value=state.datasets.some(d=>d.id===item.datasetId)?item.datasetId:'';const ids=item.relatedDatasetIds||(item.secondaryDatasetId?[item.secondaryDatasetId]:[]);[...$('#chat-secondary-dataset').options].forEach(x=>x.selected=ids.includes(x.value));if(item.datasetId&&!state.datasets.some(d=>d.id===item.datasetId))item.datasetId='';item.relatedDatasetIds=ids.filter(x=>state.datasets.some(d=>d.id===x));renderConversationList();renderMessages();saveConversations();showView('chat'); }
function addConversation() { const item=createConversation();item.datasetId=$('#chat-dataset').value||'';item.relatedDatasetIds=[...$('#chat-secondary-dataset').selectedOptions].map(x=>x.value);item.secondaryDatasetId=item.relatedDatasetIds[0]||'';state.conversations.unshift(item);state.activeConversationId=item.id;saveConversations();renderConversationList();renderMessages();showView('chat');$('#question').focus(); }

$('#new-conversation').onclick=addConversation;
$('#conversation-list').onclick=e=>{const row=e.target.closest('[data-conversation-id]');if(!row)return;const id=row.dataset.conversationId,action=e.target.closest('[data-action]')?.dataset.action;if(action==='rename'){state.pendingDeleteId=null;const title=row.querySelector('.conversation-title');title.contentEditable='true';title.classList.add('editing');title.focus();document.getSelection()?.selectAllChildren(title);return}if(action==='delete'){if(state.conversations.length===1){toast('至少保留一个对话');return}if(state.pendingDeleteId!==id){state.pendingDeleteId=id;renderConversationList();toast('请再次点击"确认删除"');return}state.pendingDeleteId=null;state.conversations=state.conversations.filter(x=>x.id!==id);if(state.activeConversationId===id)state.activeConversationId=state.conversations[0].id;saveConversations();switchConversation(state.activeConversationId);toast('对话已删除');return}state.pendingDeleteId=null;switchConversation(id)};
function commitConversationTitle(target) { const row=target.closest('[data-conversation-id]'),item=state.conversations.find(x=>x.id===row?.dataset.conversationId),title=target.textContent.trim();if(item&&title){item.title=title.slice(0,40);item.updatedAt=Date.now();saveConversations()}renderConversationList(); }
$('#conversation-list').onkeydown=e=>{if(e.target.matches('.conversation-title.editing')&&e.key==='Enter'){e.preventDefault();commitConversationTitle(e.target)}if(e.target.matches('.conversation-title.editing')&&e.key==='Escape'){e.preventDefault();renderConversationList()}};
$('#conversation-list').onfocusout=e=>{if(e.target.matches('.conversation-title.editing'))commitConversationTitle(e.target)};

function showView(name) {
  $$('.nav').forEach(x => x.classList.toggle('active', x.dataset.view === name) && x.dataset.view === name && x.scrollIntoView?.({behavior: 'smooth', block: 'nearest'}));
  $$('.view').forEach(x => x.classList.toggle('active', x.id === `view-${name}`));
  $('#page-title').textContent = titles[name][0]; $('#page-subtitle').textContent = titles[name][1];
  if (name === 'terms') loadTerms();
  if (name === 'codes') window.loadCodeAdmin?.();
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
  const secondaryValue=[...$('#chat-secondary-dataset').selectedOptions].map(x=>x.value);
  $('#chat-secondary-dataset').innerHTML='<option value="">自动选择数据表</option>'+state.datasets.map(d=>`<option value="${d.id}">${escapeHtml(d.name)}</option>`).join('');
  [...$('#chat-secondary-dataset').options].forEach(x=>x.selected=secondaryValue.includes(x.value));
  const conversation=activeConversation();if(conversation){$('#chat-dataset').value=state.datasets.some(d=>d.id===conversation.datasetId)?conversation.datasetId:'';const ids=conversation.relatedDatasetIds||(conversation.secondaryDatasetId?[conversation.secondaryDatasetId]:[]);[...$('#chat-secondary-dataset').options].forEach(x=>x.selected=ids.includes(x.value));if(conversation.datasetId&&!state.datasets.some(d=>d.id===conversation.datasetId))conversation.datasetId='';conversation.relatedDatasetIds=ids.filter(id=>state.datasets.some(d=>d.id===id));saveConversations()}
  $('#term-dataset-filter').innerHTML = datasetOptions(true);
  $('[name="dataset_id"]', $('#term-form')).innerHTML = '<option value="">全局术语</option>' + state.datasets.map(d => `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join('');
  $('#dataset-list').innerHTML = state.datasets.length ? state.datasets.map(d => `
    <article class="card"><h3>${escapeHtml(d.name)}</h3><p>${escapeHtml(d.source_file)}</p><small>${d.row_count.toLocaleString()} 行 · ${d.columns.length} 个字段</small>
    <div class="card-footer" style="align-items:stretch;flex-direction:column;gap:10px"><span>${new Date(d.created_at).toLocaleString()}</span><div style="display:flex;align-items:center;gap:8px"><button class="ghost" data-rename-dataset="${d.id}">重命名</button><button class="ghost" data-preview-dataset="${d.id}">查看</button><button class="danger" style="margin-left:auto;padding:9px 14px" data-delete-dataset="${d.id}">删除</button></div></div></article>`).join('') : '<div class="empty">还没有数据表，上传一个 CSV 或 Excel 开始问数。</div>';
  updateRelationshipDatasetOptions();
  await loadRelationships();
}

$('#chat-dataset').onchange=()=>{const item=activeConversation();if(item){item.datasetId=$('#chat-dataset').value;item.updatedAt=Date.now();saveConversations()}};
$('#chat-secondary-dataset').onchange=()=>{const item=activeConversation();if(item){item.relatedDatasetIds=[...$('#chat-secondary-dataset').selectedOptions].map(x=>x.value);item.secondaryDatasetId=item.relatedDatasetIds[0]||'';item.updatedAt=Date.now();saveConversations()}};

$('#upload-open').onclick = () => $('#upload-dialog').showModal();
$('#term-open').onclick = () => {
  state.editingTermId = null;
  $('#term-form').reset();
  $('.modal-head h3', $('#term-form')).textContent = '新增业务术语';
  $('button.primary', $('#term-form')).textContent = '保存术语';
  $('#term-dialog').showModal();
};
$('#term-import-open').onclick = () => $('#term-import-dialog').showModal();
$('#term-export').onclick = () => {
  const link = document.createElement('a');
  link.href = '/api/terms/export';
  link.download = '';
  document.body.appendChild(link);
  link.click();
  link.remove();
  toast('术语库导出已开始');
};
$$('[data-close]').forEach(x => x.onclick = () => x.closest('dialog').close());

$('#upload-form').onsubmit = async e => {
  e.preventDefault(); const button = $('button.primary', e.target); button.disabled = true; button.textContent = '上传中…';
  try { await api('/api/datasets', { method: 'POST', body: new FormData(e.target) }); e.target.reset(); $('#upload-dialog').close(); await loadDatasets(); toast('数据表上传成功'); }
  catch (err) { toast(err.message); } finally { button.disabled = false; button.textContent = '开始上传'; }
};

$('#dataset-list').onclick = async e => {
  const renameId = e.target.dataset.renameDataset;
  if (renameId) {
    const dataset = state.datasets.find(item => item.id === renameId);
    if (!dataset) return;
    const name = prompt('请输入新的数据表名称', dataset.name);
    if (name === null) return;
    const cleanedName = name.trim();
    if (!cleanedName) { toast('数据表名称不能为空'); return; }
    try {
      await api(`/api/datasets/${renameId}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: cleanedName}),
      });
      await loadDatasets();
      toast('数据表名称已更新');
    } catch (err) { toast(err.message); }
    return;
  }
  const previewId = e.target.dataset.previewDataset;
  if (previewId) {
    const dataset = state.datasets.find(item => item.id === previewId);
    $('#dataset-preview-title').textContent = dataset ? `查看数据表：${dataset.name}` : '查看数据表';
    $('#dataset-preview-meta').textContent = '正在加载数据…';
    $('#dataset-preview-content').innerHTML = '';
    $('#dataset-preview-dialog').showModal();
    try {
      const preview = await api(`/api/datasets/${previewId}/preview?limit=100`);
      $('#dataset-preview-meta').textContent = `共 ${preview.dataset.row_count.toLocaleString()} 行 · 当前显示前 ${preview.rows.length} 行`;
      const heads = preview.columns.map(column => `<th>${escapeHtml(column)}</th>`).join('');
      const rows = preview.rows.map(row => `<tr>${preview.columns.map(column => `<td>${escapeHtml(row[column])}</td>`).join('')}</tr>`).join('');
      $('#dataset-preview-content').innerHTML = preview.rows.length
        ? `<table><thead><tr>${heads}</tr></thead><tbody>${rows}</tbody></table>`
        : '<div class="empty">这张数据表没有数据。</div>';
    } catch (err) {
      $('#dataset-preview-meta').textContent = '';
      $('#dataset-preview-content').innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
    }
    return;
  }
  const id = e.target.dataset.deleteDataset; if (!id || !confirm('确定删除这个数据表及其关联术语吗？')) return;
  try { await api(`/api/datasets/${id}`, {method:'DELETE'}); await loadDatasets(); toast('数据表已删除'); } catch(err) { toast(err.message); }
};

function relationshipDatasetOptions(selected='') {
  return state.datasets.map(d=>`<option value="${d.id}" ${d.id===selected?'selected':''}>${escapeHtml(d.name)}</option>`).join('');
}
function updateRelationshipFields(side) {
  const form=$('#relationship-form'),dataset=state.datasets.find(d=>d.id===form.elements[`${side}_dataset_id`].value);
  const current=form.elements[`${side}_field`].value;
  form.elements[`${side}_field`].innerHTML=(dataset?.columns||[]).map(c=>`<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)} · ${escapeHtml(c.type)}</option>`).join('');
  if((dataset?.columns||[]).some(c=>c.name===current))form.elements[`${side}_field`].value=current;
}
function updateRelationshipDatasetOptions() {
  const form=$('#relationship-form');if(!form)return;
  for(const side of ['left','right']){const select=form.elements[`${side}_dataset_id`],value=select.value;select.innerHTML=relationshipDatasetOptions(value);if(state.datasets.some(d=>d.id===value))select.value=value;updateRelationshipFields(side)}
}
async function loadRelationships() {
  const rules=await api('/api/relationship-rules');
  const allowedFields=new Set((rules.join_key_pairs||[]).flat());
  const fieldMeanings={target_id:'船舶目标标识',mmsi:'船舶 MMSI 标识',imo:'船舶 IMO 编号',event_uuid:'事件唯一标识'};
  const tableJoinFields=state.datasets.map(dataset=>{
    const fields=(dataset.columns||[]).map(column=>column.name).filter(name=>allowedFields.has(name));
    const purpose=rules.table_purposes?.[dataset.name]||rules.table_purposes?.[dataset.table_name];
    return `<li><div><strong>${escapeHtml(dataset.name)}</strong>${purpose?`<span>${escapeHtml(purpose.role)}</span>`:''}</div><div class="table-join-fields">${fields.map(name=>`<code title="${escapeHtml(fieldMeanings[name]||'关联标识')}">${escapeHtml(name)}</code>`).join('')||'<em>无可用 JOIN 字段</em>'}</div></li>`;
  }).join('');
  const actualConnections=[];
  for(let leftIndex=0;leftIndex<state.datasets.length;leftIndex++){
    for(let rightIndex=leftIndex+1;rightIndex<state.datasets.length;rightIndex++){
      const left=state.datasets[leftIndex],right=state.datasets[rightIndex];
      const leftFields=new Set((left.columns||[]).map(x=>x.name)),rightFields=new Set((right.columns||[]).map(x=>x.name));
      const match=(rules.join_key_pairs||[]).find(([leftField,rightField])=>leftFields.has(leftField)&&rightFields.has(rightField));
      const reverse=(rules.join_key_pairs||[]).find(([leftField,rightField])=>leftFields.has(rightField)&&rightFields.has(leftField));
      const selected=match||reverse;
      if(!selected)continue;
      const leftField=match?selected[0]:selected[1],rightField=match?selected[1]:selected[0];
      actualConnections.push(`<li><span class="connection-table">${escapeHtml(left.name)}</span><code>${escapeHtml(leftField)}</code><b>=</b><span class="connection-table">${escapeHtml(right.name)}</span><code>${escapeHtml(rightField)}</code></li>`);
    }
  }
  $('#relationship-rules').innerHTML=`
    <section class="table-join-field-list">
      <h3>各数据表用于 JOIN 的字段</h3>
      <p>先看每张表右侧的关联字段：只有这些字段会参与多表连接。字段相同可以直接对应，<code>target_id</code> 与 <code>mmsi</code> 也允许互相连接。</p>
      <ul>${tableJoinFields||'<li><div><strong>暂无数据表</strong></div></li>'}</ul>
      <div class="join-field-legend"><span><code>target_id</code> 船舶目标标识</span><span><code>mmsi</code> 船舶 MMSI 标识</span><span><code>imo</code> 船舶 IMO 编号</span><span><code>event_uuid</code> 事件唯一标识</span></div>
    </section>
    <section class="actual-connections">
      <h3>当前数据表的实际连接清单 <small>共 ${actualConnections.length} 条</small></h3>
      <p>系统生成多表 SQL 时，只会使用下面列出的表和字段关系。每对数据表按受控规则优先级选择一个连接条件。</p>
      <ul>${actualConnections.join('')||'<li class="connection-empty">当前数据表之间没有找到可用的受控关联字段。</li>'}</ul>
    </section>`;
  state.relationships=await api('/api/relationships');
  $('#relationship-list').innerHTML=state.relationships.length?state.relationships.map(x=>`<article class="relationship-item"><div><b>${escapeHtml(x.name)}</b><small>${escapeHtml(x.left_dataset_name)}.${escapeHtml(x.left_field)} = ${escapeHtml(x.right_dataset_name)}.${escapeHtml(x.right_field)}</small><span>${escapeHtml(x.meaning)}</span><small>粒度：${escapeHtml(x.left_grain)} ↔ ${escapeHtml(x.right_grain)}</small></div><div><span class="relationship-status ${x.enabled?'enabled':''}">${x.enabled?'已启用':'已停用'}</span><button class="ghost" data-check-relationship="${x.id}">检查关联</button><button class="ghost" data-edit-relationship="${x.id}">编辑</button><button class="ghost" data-toggle-relationship="${x.id}">${x.enabled?'停用':'启用'}</button><button class="danger" data-delete-relationship="${x.id}">删除</button></div></article>`).join(''):'<div class="empty">尚未配置表关联关系。系统不会猜测 target_id 或 MMSI 的对应关系。</div>';
}
$('#relationship-form [name=left_dataset_id]').onchange=()=>updateRelationshipFields('left');
$('#relationship-form [name=right_dataset_id]').onchange=()=>updateRelationshipFields('right');
$('#relationship-open').onclick=()=>{state.editingRelationshipId=null;const form=$('#relationship-form');form.reset();updateRelationshipDatasetOptions();$('.modal-head h3',form).textContent='新增表关联关系';$('button.primary',form).textContent='保存关系';$('#relationship-dialog').showModal()};
$('#relationship-form').onsubmit=async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.target));data.enabled=e.target.elements.enabled.checked;const id=state.editingRelationshipId;try{await api(id?`/api/relationships/${id}`:'/api/relationships',{method:id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});e.target.reset();state.editingRelationshipId=null;$('#relationship-dialog').close();await loadRelationships();toast(id?'关系已更新':'关系已保存')}catch(err){toast(err.message)}};
$('#relationship-list').onclick=async e=>{const edit=e.target.dataset.editRelationship,toggle=e.target.dataset.toggleRelationship,del=e.target.dataset.deleteRelationship,check=e.target.dataset.checkRelationship;
  if(edit){const x=state.relationships.find(item=>item.id===edit);if(!x)return;state.editingRelationshipId=edit;const form=$('#relationship-form');form.elements.name.value=x.name;form.elements.left_dataset_id.innerHTML=relationshipDatasetOptions(x.left_dataset_id);form.elements.left_dataset_id.value=x.left_dataset_id;updateRelationshipFields('left');form.elements.left_field.value=x.left_field;form.elements.right_dataset_id.innerHTML=relationshipDatasetOptions(x.right_dataset_id);form.elements.right_dataset_id.value=x.right_dataset_id;updateRelationshipFields('right');form.elements.right_field.value=x.right_field;form.elements.meaning.value=x.meaning;form.elements.left_grain.value=x.left_grain;form.elements.right_grain.value=x.right_grain;form.elements.enabled.checked=x.enabled;$('.modal-head h3',form).textContent='编辑表关联关系';$('button.primary',form).textContent='保存修改';$('#relationship-dialog').showModal();return}
  if(toggle){const x=state.relationships.find(item=>item.id===toggle);try{await api(`/api/relationships/${toggle}/status`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!x.enabled})});await loadRelationships();toast(x.enabled?'关系已停用':'关系已启用')}catch(err){toast(err.message)}return}
  if(del&&confirm('确定删除这条表关联关系吗？')){try{await api(`/api/relationships/${del}`,{method:'DELETE'});await loadRelationships();toast('关系已删除')}catch(err){toast(err.message)}return}
  if(check){$('#relationship-check-result').innerHTML='<p>正在检查字段数据…</p>';$('#relationship-check-dialog').showModal();try{const x=await api(`/api/relationships/${check}/check`);const side=(label,v)=>`<div class="check-card"><b>${label}</b><span>字段类型：${escapeHtml(v.field_type)}</span><span>总行数：${v.total_rows}，空值：${v.null_rows}</span><span>非空唯一键：${v.distinct_non_null}，重复键：${v.duplicate_keys}</span></div>`;$('#relationship-check-result').innerHTML=`<div class="relationship-check-grid">${side('左侧字段',x.left)}${side('右侧字段',x.right)}</div><p>两侧匹配的不同键：<b>${x.matched_distinct_keys}</b></p><p>字段类型${x.type_compatible?'一致':'不一致'}</p><p class="dialog-tip">${escapeHtml(x.advisory)}</p>`}catch(err){$('#relationship-check-result').innerHTML=`<div class="empty">${escapeHtml(err.message)}</div>`}}
};

async function loadTerms() {
  const dataset = $('#term-dataset-filter').value, q = $('#term-search').value.trim();
  state.terms = await api(`/api/terms?dataset_id=${encodeURIComponent(dataset)}&q=${encodeURIComponent(q)}`);
  $('#term-list').innerHTML = state.terms.length ? state.terms.map(t => {
    const ds = state.datasets.find(d => d.id === t.dataset_id);
    return `<div class="term"><b>${escapeHtml(t.term)}<small>${escapeHtml(t.synonyms || '无同义词')}</small></b><span>${escapeHtml(t.definition)}</span><div><small>${escapeHtml(ds?.name || '全局')}</small> <button class="ghost" style="padding:5px 6px;border-radius:6px" data-edit-term="${t.id}">编辑</button> <button class="danger" data-delete-term="${t.id}">删除</button></div></div>`;
  }).join('') : '<div class="empty">没有匹配的术语。</div>';
}

let searchTimer; $('#term-search').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadTerms, 250); };
$('#term-dataset-filter').onchange = loadTerms;
$('#term-form').onsubmit = async e => {
  e.preventDefault(); const data = Object.fromEntries(new FormData(e.target)); data.dataset_id ||= null;
  const id = state.editingTermId;
  const url = id ? `/api/terms/${id}` : '/api/terms';
  try { await api(url, {method:id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); e.target.reset(); state.editingTermId = null; $('#term-dialog').close(); await loadTerms(); toast(id ? '术语已更新' : '术语已保存'); } catch(err) { toast(err.message); }
};
$('#term-import-form').onsubmit = async e => {
  e.preventDefault(); const button = $('button.primary', e.target); button.disabled = true; button.textContent = '导入中…';
  try { const result = await api('/api/terms/import', {method:'POST', body:new FormData(e.target)}); e.target.reset(); $('#term-import-dialog').close(); await loadTerms(); toast(`成功导入 ${result.imported} 条，跳过 ${result.skipped} 条重复术语`); }
  catch(err) { toast(err.message); } finally { button.disabled = false; button.textContent = '开始导入'; }
};
$('#term-list').onclick = async e => {
  const editId = e.target.dataset.editTerm;
  if (editId) {
    const term = state.terms.find(item => item.id === editId);
    if (!term) return;
    state.editingTermId = editId;
    const form = $('#term-form');
    form.elements.term.value = term.term;
    form.elements.definition.value = term.definition;
    form.elements.synonyms.value = term.synonyms || '';
    form.elements.dataset_id.value = term.dataset_id || '';
    $('.modal-head h3', form).textContent = '编辑业务术语';
    $('button.primary', form).textContent = '保存修改';
    $('#term-dialog').showModal();
    return;
  }
  const id=e.target.dataset.deleteTerm;if(!id)return;try{await api(`/api/terms/${id}`,{method:'DELETE'});await loadTerms();toast('术语已删除')}catch(err){toast(err.message)}
};

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
function saveModel() { state.model=readModel();localStorage.setItem(MODEL_CONFIG_KEY,JSON.stringify(state.model));sessionStorage.removeItem(MODEL_CONFIG_KEY);updateModelStatus(); }
function updateModelStatus(){const el=$('.status');el.classList.toggle('ready',!!state.model);$('#model-status').textContent=state.model?state.model.model:'未配置模型'}
$('#model-form').onsubmit = e => { e.preventDefault();saveModel();toast('模型配置已保存到此浏览器');showView('chat'); };
$('#test-model').onclick = async () => { const el=$('#model-result');el.textContent='正在测试连接…';try{const result=await api('/api/model/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(readModel())});el.textContent=`✓ ${result.message}`;}catch(err){el.textContent=`连接失败：${err.message}`;} };

function splitAnswerSections(content) {
  const names = ['直接结论', '统计范围', '结果明细', '统计口径', '可追溯信息', '注意事项'];
  const order = '(?:(?:\\d+|[一二三四五六七八九十]+)[.)、．]|[（(](?:\\d+|[一二三四五六七八九十]+)[）)])';
  const emphasis = '(?:\\*\\*|__)?';
  const pattern = new RegExp(`^[ \\t]*(?:(?:#{1,6}|[-+*])[ \\t]*)?${emphasis}(?:${order}[ \\t]*)?${emphasis}(?:【|\\[)?(${names.join('|')})(?:\\*\\*|__|】|\\])?[ \\t]*(?:[:：][ \\t]*)?(?:\\*\\*|__)?(.*)$`);
  const sections = {}; let current = null;
  for (const line of String(content).replace(/\\r\\n?/g, '\n').split('\n')) {
    const match = line.match(pattern);
    if (match) {
      current = match[1];
      sections[current] = match[2].trim();
    } else if (current) {
      sections[current] = `${sections[current]}\n${line}`.trim();
    }
  }
  return sections;
}
function renderSection(title, body) { return body ? `<section class="answer-section"><b>${escapeHtml(title)}</b><div class="answer-section-body">${escapeHtml(body)}</div></section>` : ''; }
function renderMatchedTerms(details) {
  if (!details) return '';
  const retrieval=details.route?.term_retrieval||{},items=details.terms||[];
  if (!items.length) return `<section class="answer-section matched-terms"><b>命中术语</b><div class="answer-section-body"><small>检索方式：${escapeHtml(retrieval.method||'keyword')}</small><div class="matched-term"><span>未命中相关术语</span></div></div></section>`;
  const body=items.map(x=>`<div class="matched-term"><strong>${escapeHtml(x.term)}</strong><span>${escapeHtml(x.definition)}</span><small>匹配来源：${escapeHtml((x.match_sources||[]).join('＋')||'--')}${x.semantic_score==null?'':` · 相似度：${escapeHtml(x.semantic_score)}`}</small></div>`).join('');
  return `<section class="answer-section matched-terms"><b>命中术语</b><details class="matched-terms-list"><summary>命中 ${items.length} 条 · ${escapeHtml(retrieval.method||'keyword')}，点击展开</summary><div class="answer-section-body">${body}</div></details></section>`;
}
function renderAgentTrace(agent) {
  if (!agent) return '';
  const steps=agent.steps||[],agentState=agent.state||{},plan=agentState.plan||[];
  const planner=agentState.planner||{},activePlan=planner.active_plan||{};
  const memory=agentState.memory||{};
  const planHtml=plan.length?`<details class="agent-plan"><summary>执行计划 · ${plan.length} 步</summary><ol>${plan.map(item=>`<li>${escapeHtml(item)}</li>`).join('')}</ol></details>`:'';
  const stepHtml=steps.length?`<ol class="agent-steps">${steps.map((step,index)=>{const warnings=(step.warnings||[]).map(item=>`<small class="agent-warning">${escapeHtml(item)}</small>`).join('');return `<li class="${escapeHtml(step.status||'')}"><span>${index+1}</span><div><b>${escapeHtml(step.name)}</b><small>${escapeHtml(step.tool||'--')}</small>${step.error?`<small class="agent-error">${escapeHtml(step.error)}</small>`:''}${warnings}</div><em>${formatDuration(step.elapsed_ms)}</em></li>`}).join('')}</ol>`:'<p>暂无 Agent 步骤记录</p>';
  const plannerHtml=planner.name?`<div class="planner-decision"><b>Planner 决策</b><span>${escapeHtml(planner.name)} · ${escapeHtml(activePlan.mode||'bootstrap')}</span><small>${escapeHtml(activePlan.objective||planner.strategy||'--')}</small></div>`:'';
  return `<section class="answer-section agent-trace"><b>Agent 执行轨迹</b><div class="agent-state"><span>当前阶段<strong>${escapeHtml(agentState.phase||'--')}</strong></span><span>规划模式<strong>${escapeHtml(activePlan.mode||'--')}</strong></span><span>SQL 尝试<strong>${escapeHtml(agentState.sql_attempts??0)} 次</strong></span><span>SQL 修复<strong>${agentState.sql_repaired?'已触发':'未触发'}</strong></span><span>记忆召回<strong>${escapeHtml(memory.recalled_count??0)} 条</strong></span><span>记忆保存<strong>${memory.saved_memory_id?escapeHtml(String(memory.saved_memory_id).slice(0,8)):'--'}</strong></span></div>${plannerHtml}${planHtml}${stepHtml}</section>`;
}
function renderAnswerContent(content, details) {
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
    const codeRequests=details.route?.code_lookup_requests||[],codeVersion=details.route?.code_dictionary_version;
    const termDetails=renderMatchedTerms(details);
    const agentTrace=renderAgentTrace(details.agent);
    const relation=details.route?.relationship;
    const relationDetails=relation?`<section class="reasoning-summary"><b>实际表关联</b><p>${escapeHtml(relation.left_dataset_name)}.${escapeHtml(relation.left_field)} = ${escapeHtml(relation.right_dataset_name)}.${escapeHtml(relation.right_field)}</p><p>${escapeHtml(relation.meaning)}</p><p>记录粒度：${escapeHtml(relation.left_grain)} ↔ ${escapeHtml(relation.right_grain)}</p></section>`:'';
    const codeBadge=codeRequests.length?`<span>编码映射 <b>v${escapeHtml(codeVersion||'--')} · ${codeRequests.length} 个字段</b></span>`:'';
    const codeDetails=codeRequests.length?`<section class="reasoning-summary"><b>字段编码映射</b>${codeRequests.map(x=>`<p>${escapeHtml(x.table)}.${escapeHtml(x.column)} · ${escapeHtml(x.code_type)} · ${escapeHtml(x.purpose)}${x.mention?` · "${escapeHtml(x.mention)}" → ${escapeHtml((x.code_values||[]).join(', '))}`:''}</p>`).join('')}</section>`:'';
    extra=`<div class="run-stats"><span>总耗时 <b>${formatDuration(metrics.total_elapsed_ms)}</b></span><span>Token <b>${tokens}</b></span>${codeBadge}${metrics.sql_generation_cached?'<span>SQL <b>缓存命中</b></span>':''}${metrics.sql_repaired?'<span>SQL <b>自动修复</b></span>':''}</div><details class="meta-panel"><summary>查看 Agent 执行轨迹、SQL 和查询结果</summary>${agentTrace}${termDetails}${relationDetails}<section class="reasoning-summary"><b>SQL 生成依据</b><p>${escapeHtml(details.reasoning_summary||'暂无生成依据记录')}</p></section>${codeDetails}<div class="metric-grid"><span>SQL 生成<strong>${sqlTime} · ${formatTokens(metrics.sql_generation_tokens)}</strong></span><span>数据库查询<strong>${formatDuration(metrics.query_elapsed_ms)}</strong></span><span>答案生成<strong>${formatDuration(metrics.answer_generation_elapsed_ms)} · ${formatTokens(metrics.answer_generation_tokens)}</strong></span><span>Token 明细<strong>输入 ${formatTokens(metrics.prompt_tokens)} / 输出 ${formatTokens(metrics.completion_tokens)}</strong></span></div><pre>${escapeHtml(details.sql)}</pre>${rows?`<div class="result-table"><table><thead><tr>${heads}</tr></thead><tbody>${rows}</tbody></table></div>`:'<p>查询结果为空</p>'}</details>`;
  }
  const renderedContent = role === 'assistant' ? renderAnswerContent(content, details) : escapeHtml(content);
  wrap.innerHTML=`<div class="bubble">${renderedContent}${extra}</div>`;$('#messages').append(wrap);$('#messages').scrollTop=$('#messages').scrollHeight;if(persist){const item=activeConversation();if(item){item.messages.push({role,content,details:details||null});item.updatedAt=Date.now();if(item.title==='新对话'&&role==='user')item.title=content.slice(0,18);saveConversations();renderConversationList()}}return wrap;
}
function formatDuration(ms){if(ms==null)return'--';return ms<1000?`${ms} ms`:`${(ms/1000).toFixed(2)} s`}
function formatTokens(value){return value==null?'-- Token':`${Number(value).toLocaleString()} Token`}
$('#messages').onclick=e=>{const button=e.target.closest('.examples button');if(button){$('#question').value=button.textContent;$('#question').focus()}};
$('#ask-form').onsubmit = async e => {
  e.preventDefault();const question=$('#question').value.trim(),dataset_id=$('#chat-dataset').value||null,related_dataset_ids=[...$('#chat-secondary-dataset').selectedOptions].map(x=>x.value),dataset_ids=dataset_id?[dataset_id,...related_dataset_ids.filter(id=>id!==dataset_id)]:[];if(!question)return;if(related_dataset_ids.length&&!dataset_id){toast('手动选择关联表时请先选择主数据表');return}if(!state.datasets.length){toast('请先上传数据表');showView('datasets');return}if(!state.model){toast('请先配置模型 API');showView('settings');return}
  const conversationId=state.activeConversationId;addMessage('user',question);$('#question').value='';const loading=addMessage('assistant','正在检索术语、生成 SQL 并查询数据…',null,false);$('.send').disabled=true;
  const finish=(content,details=null)=>{loading.remove();const item=state.conversations.find(x=>x.id===conversationId);if(!item)return;if(state.activeConversationId===conversationId)addMessage('assistant',content,details);else{item.messages.push({role:'assistant',content,details});item.updatedAt=Date.now();saveConversations();renderConversationList()}};
  try{const secondary_dataset_id=related_dataset_ids[0]||null;const result=await api('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset_id,secondary_dataset_id,dataset_ids,question,model:state.model})});finish(result.answer,result)}catch(err){finish(`问数失败：${err.message}`)}finally{$('.send').disabled=false}
};

try { state.conversations=JSON.parse(localStorage.getItem(CONVERSATIONS_KEY)||sessionStorage.getItem(CONVERSATIONS_KEY))||[]; } catch {}
if(!Array.isArray(state.conversations)||!state.conversations.length)state.conversations=[createConversation()];
state.activeConversationId=localStorage.getItem(ACTIVE_CONVERSATION_KEY)||sessionStorage.getItem(ACTIVE_CONVERSATION_KEY);if(!state.conversations.some(x=>x.id===state.activeConversationId))state.activeConversationId=state.conversations[0].id;saveConversations();
try {
  const savedModel=localStorage.getItem(MODEL_CONFIG_KEY)||sessionStorage.getItem(MODEL_CONFIG_KEY);
  state.model=JSON.parse(savedModel);
  if(state.model&&savedModel&&!localStorage.getItem(MODEL_CONFIG_KEY)){
    localStorage.setItem(MODEL_CONFIG_KEY,savedModel);
    sessionStorage.removeItem(MODEL_CONFIG_KEY);
  }
} catch {}
if(state.model){$('#api-key').value=state.model.api_key;$('#base-url').value=state.model.base_url;$('#model-name').value=state.model.model}
updateModelStatus();renderConversationList();renderMessages();loadDatasets().then(()=>{const requested=new URLSearchParams(location.search).get('view');if(requested&&titles[requested])showView(requested)}).catch(err=>toast(err.message));

// ===== 代码管理后台逻辑 (Code Admin Module) =====
(function(){
  let datasets=[],entries=[],initialized=false;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function loadVersions(){const rows=await api('/api/admin/code-versions');$('#version-list').innerHTML=`<table><thead><tr><th>版本</th><th>来源</th><th>文件名</th><th>导入时间</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows.map(v=>`<tr><td>v${v.version_number}</td><td>${esc(v.source_type)}</td><td>${esc(v.source_filename)}</td><td>${esc(v.imported_at)}</td><td>${v.enabled?'已启用':'未启用'}</td><td>${v.enabled?'':`<button class="ghost small" data-activate="${v.id}">启用</button>`}</td></tr>`).join('')}</tbody></table>`}
  $('#version-list').onclick=async e=>{const id=e.target.dataset.activate;if(!id)return;try{await api(`/api/admin/code-versions/${id}/activate`,{method:'POST'});await Promise.all([loadVersions(),loadCodes()]);toast('版本已启用')}catch(x){toast(x.message)}};
  async function importFile(dry){const file=$('#code-file').files[0];if(!file)return toast('请选择 Excel 文件');const f=new FormData();f.append('file',file);try{const r=await api(`/api/admin/code-import?dry_run=${dry}`,{method:'POST',body:f});$('#import-result').innerHTML=`<pre>${esc(JSON.stringify(r,null,2))}</pre>`;if(!dry){await Promise.all([loadVersions(),loadCodes(),loadBindings()]);toast(r.duplicate_file?'该文件已经导入':'编码字典已导入并启用')}}catch(x){toast(x.message)}}
  $('#preview-import').onclick=()=>importFile(true);
  $('#run-import').onclick=()=>importFile(false);
  const codeTypeNames={AIS_SHIP_TYPE:'船型',NAV_STATUS:'航行状态',RISK_LEVEL_CODE:'风险等级'};
  function renderCodeGroup(type,items){return `<section class="code-group"><div class="code-group-head"><div><b>${esc(type)}&nbsp;&nbsp;${esc(codeTypeNames[type]||'')}</b></div><span>${items.length} 个数值</span></div><div class="code-values"><table><thead><tr><th>数据库数值</th><th>对应含义</th><th>操作</th></tr></thead><tbody>${items.map(x=>`<tr><td><code>${esc(x.code_value)}</code></td><td class="wrap"><b>${esc(x.description)}</b></td><td><button class="ghost small" data-edit="${x.id}">编辑</button> <button class="danger" data-delete="${x.id}">删除</button></td></tr>`).join('')}</tbody></table></div></section>`}
  async function loadCodes(){const q=encodeURIComponent($('#code-search').value.trim()),t=encodeURIComponent($('#code-type-filter').value);entries=await api(`/api/admin/code-entries?q=${q}&code_type=${t}`);const groups=entries.reduce((result,item)=>{(result[item.code_type]||=[]).push(item);return result},{});$('#code-list').innerHTML=Object.keys(groups).length?Object.entries(groups).map(([type,items])=>renderCodeGroup(type,items)).join(''):'<div class="empty">没有匹配的编码项。</div>'}
  $('#load-codes').onclick=loadCodes;$('#code-list').onclick=async e=>{const edit=e.target.dataset.edit,del=e.target.dataset.delete;if(edit){const x=entries.find(i=>i.id===edit),description=prompt('业务名称',x.description);if(description===null)return;const synonyms=prompt('同义词（逗号分隔）',x.synonyms||'');if(synonyms===null)return;try{await api(`/api/admin/code-entries/${edit}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({description,synonyms})});await loadCodes();toast('编码项已更新')}catch(x){toast(x.message)}}if(del&&confirm('确定删除这个编码项吗？')){try{await api(`/api/admin/code-entries/${del}`,{method:'DELETE'});await loadCodes();toast('编码项已删除')}catch(x){toast(x.message)}}};
  async function loadDatasets_admin(){datasets=await api('/api/datasets');const s=$('#binding-add [name=dataset_id]');s.innerHTML=datasets.map(d=>`<option value="${d.id}">${esc(d.name)}</option>`).join('');updateColumns()}
  function updateColumns(){const id=$('#binding-add [name=dataset_id]').value,d=datasets.find(x=>x.id===id);$('#binding-add [name=column_name]').innerHTML=(d?.columns||[]).map(c=>`<option>${esc(c.name)}</option>`).join('')}
  $('#binding-add [name=dataset_id]').onchange=updateColumns;
  async function loadBindings(){const rows=await api('/api/admin/code-bindings');$('#binding-list').innerHTML=`<table><thead><tr><th>数据表</th><th>字段</th><th>编码类型</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows.map(x=>`<tr><td>${esc(x.table_name)}</td><td>${esc(x.column_name)}</td><td>${esc(x.code_type)}</td><td>${x.enabled?'启用':'停用'}</td><td><button class="danger" data-delete="${x.id}">删除</button></td></tr>`).join('')}</tbody></table>`}
  $('#binding-add').onsubmit=async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.target)),d=datasets.find(x=>x.id===data.dataset_id);data.table_name=d.table_name;data.enabled=true;try{await api('/api/admin/code-bindings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});await loadBindings();toast('字段绑定已保存')}catch(x){toast(x.message)}};
  $('#binding-list').onclick=async e=>{const id=e.target.dataset.delete;if(!id||!confirm('确定删除这个字段绑定吗？'))return;try{await api(`/api/admin/code-bindings/${id}`,{method:'DELETE'});await loadBindings();toast('字段绑定已删除')}catch(x){toast(x.message)}};
  window.loadCodeAdmin=()=>{if(initialized)return;initialized=true;Promise.all([loadVersions(),loadCodes(),loadDatasets_admin(),loadBindings()]).catch(x=>toast(x.message))};
  if(document.querySelector('.admin-main'))window.loadCodeAdmin();
})();
