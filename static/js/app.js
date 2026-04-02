/* ============================================================
   发票归集系统 — 前端逻辑
   ============================================================ */

// ─── 全局状态 ─────────────────────────────────────────────────────────────────
const state = {
  invoices: [],          // 已上传发票列表
  collectionResult: null,// 当前归集结果
  categories: [],        // 费用大类配置
  rules: [],             // 分类规则
  modelsConfig: null,    // 模型服务配置
  sortableInstances: [], // SortableJS 实例引用（便于销毁重建）
  selectedInvoiceIds: new Set(), // 看板中多选的发票
  selectionAnchorId: null, // Shift 多选锚点
  lang: localStorage.getItem('invoice_collect_lang') || 'zh',
};

let _dragContext = null; // 记录拖拽开始时的选择上下文

// 大类图标映射
const CAT_ICONS = {
  travel: '✈️', meeting: '📋', material: '📦', other: '🗂️',
};
const CAT_COLORS = {
  travel: 'travel', meeting: 'meeting', material: 'material', other: 'other',
};
const CATEGORY_NAME_I18N = {
  travel: { zh: '差旅费', en: 'Travel' },
  meeting: { zh: '会议费', en: 'Meeting' },
  material: { zh: '材料费', en: 'Materials' },
  other: { zh: '其他费用', en: 'Other' },
};
// 分类方式标签
const I18N = {
  zh: {
    appTitle: '发票归集系统', btnProcess: '开始归集', processOptions: '归集选项', config: '配置',
    uploadedInvoices: '已上传发票', uploadHint: '点击或拖拽上传发票', uploadSupport: '支持 PDF、图片、OFD',
    noInvoices: '暂无发票，请先上传', selectedCount: '已选 {count} 张', totalBadge: '共 {count} 张发票',
    clearSelection: '清空选择', batchDelete: '批量删除', batchDeleteSelected: '批量删除已选发票',
    classificationMethods: '分类方式', methodContentAnalysis: '内容分析', methodContentAnalysisHint: '从项目名称/备注推断费用类型',
    methodRuleMatch: '规则匹配', methodRuleMatchHint: '依据 rules.yml 中的自定义规则',
    methodLLM: '大模型（LLM）', methodLLMHint: '当内容分析和规则均未命中时使用',
    forceReclassify: '强制重新分类', forceReclassifyHint: '对已分类的发票也重新执行',
    statusDone: '已抽取', statusPending: '待抽取', statusError: '抽取失败',
    loadingProcessing: '处理中...', loadingUploadExtract: '上传并抽取发票信息...',
    uploadProgressTitle: '正在上传并处理发票…', uploadSendingHint: '正在上传 {count} 个文件…',
    uploadExtractHint: '正在识别票面并抽取字段，请稍候…', uploadDoneTitle: '处理完成',
    uploadStepSend: '上传文件', uploadStepExtract: '识别与抽取', uploadStepFinish: '完成',
    progressStepPrepare: '准备中', progressStepClassify: '发票分类', progressStepGroup: '分组',
    progressStepSave: '保存结果', progressStepDone: '归集完成',
    btnProgressCancel: '取消', progressCancelling: '取消中…',
    progressCancelFailed: '取消请求失败：{error}',
    progressCancelledTitle: '归集已取消', progressCancelledDetail: '本次归集已取消，未保存任何结果。',
    progressCancelledToast: '已取消本次归集',
    categoryGrouping: '分组',
    uploadSuccess: '成功上传 {count} 张发票', uploadFailed: '上传失败：{error}',
    emptyBoard: '点击「开始归集」按钮对发票进行自动归集分类',
    ungrouped: '未分组', addGroup: '＋ 新建分组', dropHere: '拖拽发票到此处',
    unclassified: '未分类', invoiceCount: '{count} 张', unknownType: '未知类型',
    tagRule: '规则', tagAI: 'AI', tagManual: '手动', tagDefault: '默认', tagPending: '待处理',
    selectGroup: '选中({count})', unselectGroup: '取消({count})', selectGroupTitle: '选中本组发票',
    unselectGroupTitle: '取消选中本组发票', noGroupInvoice: '该分组暂无发票',
    noGroupInvoiceWarn: '该分组暂无可选择的发票', viewDetail: '查看详情', delete: '删除',
    selectCategoryTitle: '选中该费用类型全部发票', unselectCategoryTitle: '取消选中该费用类型全部发票',
    confirmBatchDelete: '确认删除选中的 {count} 张发票？此操作不可撤销。', batchDeleteOk: '已删除 {count} 张发票',
    batchDeleteFail: '批量删除失败：{error}', languageSwitchTitle: '切换语言',
    requestFailed: '请求失败',
  },
  en: {
    appTitle: 'Invoice Collection System', btnProcess: 'Start Collection', processOptions: 'Collection Options', config: 'Settings',
    uploadedInvoices: 'Uploaded Invoices', uploadHint: 'Click or drag files to upload', uploadSupport: 'Supports PDF, images, OFD',
    noInvoices: 'No invoices yet, please upload first', selectedCount: 'Selected {count}', totalBadge: '{count} invoices',
    clearSelection: 'Clear Selection', batchDelete: 'Batch Delete', batchDeleteSelected: 'Delete selected invoices',
    classificationMethods: 'Classification Methods', methodContentAnalysis: 'Content Analysis', methodContentAnalysisHint: 'Infer expense type from item name/remarks',
    methodRuleMatch: 'Rule Matching', methodRuleMatchHint: 'Use custom rules in rules.yml',
    methodLLM: 'LLM', methodLLMHint: 'Fallback when analysis/rules do not match',
    forceReclassify: 'Force Reclassify', forceReclassifyHint: 'Reclassify already-classified invoices',
    statusDone: 'Extracted', statusPending: 'Pending', statusError: 'Failed',
    loadingProcessing: 'Processing...', loadingUploadExtract: 'Uploading and extracting invoice data...',
    uploadProgressTitle: 'Uploading and processing…', uploadSendingHint: 'Uploading {count} file(s)…',
    uploadExtractHint: 'Extracting fields on server, please wait…', uploadDoneTitle: 'Done',
    uploadStepSend: 'Upload', uploadStepExtract: 'Extract', uploadStepFinish: 'Done',
    progressStepPrepare: 'Preparing', progressStepClassify: 'Classification', progressStepGroup: 'Grouping',
    progressStepSave: 'Saving', progressStepDone: 'Complete',
    btnProgressCancel: 'Cancel', progressCancelling: 'Cancelling…',
    progressCancelFailed: 'Could not cancel: {error}',
    progressCancelledTitle: 'Collection cancelled',
    progressCancelledDetail: 'Cancelled. No changes were saved.',
    progressCancelledToast: 'Collection was cancelled',
    categoryGrouping: 'Groups',
    uploadSuccess: 'Uploaded {count} invoice(s)', uploadFailed: 'Upload failed: {error}',
    emptyBoard: 'Click "Start Collection" to classify invoices',
    ungrouped: 'Ungrouped', addGroup: '+ New Group', dropHere: 'Drag invoices here',
    unclassified: 'Unclassified', invoiceCount: '{count}', unknownType: 'Unknown type',
    tagRule: 'Rule', tagAI: 'AI', tagManual: 'Manual', tagDefault: 'Default', tagPending: 'Pending',
    selectGroup: 'Select({count})', unselectGroup: 'Unselect({count})', selectGroupTitle: 'Select this group',
    unselectGroupTitle: 'Unselect this group', noGroupInvoice: 'No invoices in this group',
    noGroupInvoiceWarn: 'No invoice to select in this group', viewDetail: 'View', delete: 'Delete',
    selectCategoryTitle: 'Select all invoices in this category', unselectCategoryTitle: 'Unselect all invoices in this category',
    confirmBatchDelete: 'Delete selected {count} invoice(s)? This action cannot be undone.', batchDeleteOk: 'Deleted {count} invoice(s)',
    batchDeleteFail: 'Batch delete failed: {error}', languageSwitchTitle: 'Switch language',
    requestFailed: 'Request failed',
  },
};

function t(key, vars = {}) {
  const langPack = I18N[state.lang] || I18N.zh;
  const template = langPack[key] ?? I18N.zh[key] ?? key;
  return String(template).replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? `{${k}}`));
}

function classifiedByLabels() {
  return {
    rule: ['tag-rule', t('tagRule')],
    llm: ['tag-llm', t('tagAI')],
    manual: ['tag-manual', t('tagManual')],
    default: ['tag-default', t('tagDefault')],
    pending: ['tag-default', t('tagPending')],
  };
}

function displayCategoryName(categoryId, fallbackName = '') {
  const key = CATEGORY_NAME_I18N[categoryId];
  if (key) return key[state.lang] || fallbackName || categoryId;
  return fallbackName || categoryId;
}
// 发票类型图标
function invoiceIcon(type) {
  if (!type) return '🧾';
  if (/机票|行程|航空/.test(type)) return '✈️';
  if (/火车|高铁|动车|铁路/.test(type)) return '🚆';
  if (/船|轮/.test(type)) return '🚢';
  if (/住宿|酒店|宾馆/.test(type)) return '🏨';
  if (/餐|饮食|食/.test(type)) return '🍽️';
  if (/出租|网约|打车/.test(type)) return '🚖';
  if (/会议|培训/.test(type)) return '📋';
  return '🧾';
}

// ─── API Helper ───────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || t('requestFailed'));
  }
  return res.json();
}

// ─── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type = '') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById('toastContainer').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ─── Upload overlay（节点式进度，与归集弹窗一致）──────────────────────────────
function progressModalSteps() {
  return document.querySelectorAll('#progressSteps .progress-step');
}

function resetUploadOverlaySteps() {
  document.querySelectorAll('#uploadProgressSteps .progress-step').forEach(el => {
    el.classList.remove('active', 'done', 'error');
  });
}

/**
 * @param {'upload'|'extract'|'done'|'error_upload'|'error_extract'} phase
 */
function setUploadOverlayPhase(phase, detail = '') {
  const steps = document.querySelectorAll('#uploadProgressSteps .progress-step');
  const detailEl = document.getElementById('loadingDetailText');
  if (detailEl && detail !== undefined) detailEl.textContent = detail || '';
  steps.forEach((el, i) => {
    el.classList.remove('active', 'done', 'error');
    if (phase === 'upload') {
      if (i === 0) el.classList.add('active');
    } else if (phase === 'extract') {
      if (i === 0) el.classList.add('done');
      if (i === 1) el.classList.add('active');
    } else if (phase === 'done') {
      el.classList.add('done');
    } else if (phase === 'error_upload') {
      if (i === 0) el.classList.add('error');
    } else if (phase === 'error_extract') {
      if (i === 0) el.classList.add('done');
      if (i === 1) el.classList.add('error');
    }
  });
}

function showUploadOverlay() {
  document.getElementById('uploadProgressTitle').textContent = t('uploadProgressTitle');
  resetUploadOverlaySteps();
  document.getElementById('loadingOverlay').classList.remove('hidden');
}

function hideUploadOverlay() {
  document.getElementById('loadingOverlay').classList.add('hidden');
  resetUploadOverlaySteps();
  const detailEl = document.getElementById('loadingDetailText');
  if (detailEl) detailEl.textContent = '';
}

/** 使用 XHR 以便在上传完成后切换到「抽取」节点 */
function uploadInvoicesXHR(formData) {
  return new Promise((resolve, reject) => {
    let bodySent = false;
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/invoices/upload');
    xhr.responseType = 'json';
    xhr.upload.onload = () => {
      bodySent = true;
      setUploadOverlayPhase('extract', t('uploadExtractHint'));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response);
        return;
      }
      let msg = xhr.statusText || t('requestFailed');
      if (xhr.response && typeof xhr.response === 'object' && xhr.response.detail != null) {
        const d = xhr.response.detail;
        if (typeof d === 'string') msg = d;
        else if (Array.isArray(d)) msg = d.map(x => (x && (x.msg || x.message)) || JSON.stringify(x)).join('; ');
        else msg = JSON.stringify(d);
      }
      reject(Object.assign(new Error(msg), { _phase: bodySent ? 'extract' : 'upload' }));
    };
    xhr.onerror = () => {
      reject(Object.assign(new Error(t('requestFailed')), { _phase: bodySent ? 'extract' : 'upload' }));
    };
    xhr.send(formData);
  });
}

// ─── Modal ────────────────────────────────────────────────────────────────────
function openModal(id) { document.getElementById(id).classList.remove('hidden'); }
function closeModal(id) { document.getElementById(id).classList.add('hidden'); }
// Close on overlay click（归集进行中不允许关闭进度弹窗）
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', e => {
    if (e.target !== overlay) return;
    if (overlay.id === 'progressModal') {
      // 仅当归集结束后才允许点背景关闭
      const footerVisible = document.getElementById('progressFooter').style.display !== 'none';
      if (footerVisible) closeProgressModal();
    } else {
      overlay.classList.add('hidden');
    }
  });
});

// ─── Tab ──────────────────────────────────────────────────────────────────────
function switchTab(activeId) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(activeId).classList.add('active');
  const idx = ['tabCategories','tabRules','tabModels'].indexOf(activeId);
  document.querySelectorAll('.tab-btn')[idx]?.classList.add('active');
}

// ─── Upload ───────────────────────────────────────────────────────────────────
const uploadZone = document.getElementById('uploadZone');
const fileInput = document.getElementById('fileInput');

uploadZone.addEventListener('click', () => fileInput.click());

uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('drag-over'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
uploadZone.addEventListener('drop', e => {
  e.preventDefault();
  uploadZone.classList.remove('drag-over');
  handleFiles(e.dataTransfer.files);
});
fileInput.addEventListener('change', () => handleFiles(fileInput.files));

async function handleFiles(files) {
  if (!files.length) return;
  const formData = new FormData();
  for (const f of files) formData.append('files', f);

  showUploadOverlay();
  setUploadOverlayPhase('upload', t('uploadSendingHint', { count: files.length }));
  try {
    const uploaded = await uploadInvoicesXHR(formData);
    setUploadOverlayPhase('done');
    document.getElementById('uploadProgressTitle').textContent = t('uploadDoneTitle');
    toast(t('uploadSuccess', { count: uploaded.length }), 'success');
    await loadInvoices();
    await new Promise(r => setTimeout(r, 400));
  } catch (e) {
    const phase = e && e._phase === 'upload' ? 'error_upload' : 'error_extract';
    setUploadOverlayPhase(phase, String(e.message || e));
    toast(t('uploadFailed', { error: e.message || e }), 'error');
    await new Promise(r => setTimeout(r, 600));
  } finally {
    hideUploadOverlay();
    fileInput.value = '';
  }
}

// ─── Load & Render Invoices (left panel) ─────────────────────────────────────
async function loadInvoices() {
  state.invoices = await api('GET', '/api/invoices');
  renderInvoiceList();
  updateTotalBadge();
}

function renderInvoiceList() {
  const el = document.getElementById('invoiceList');
  if (!state.invoices.length) {
    el.innerHTML = `<div class="empty-state">${t('noInvoices')}</div>`;
    return;
  }
  el.innerHTML = state.invoices.map(inv => `
    <div class="invoice-item" data-id="${inv.id}" title="${inv.filename}">
      <div class="invoice-thumb">${invoiceIcon(inv.invoice_type)}</div>
      <div class="invoice-meta">
        <div class="invoice-name">${escHtml(inv.filename)}</div>
        <div class="invoice-sub">
          ${inv.total_amount != null ? '¥' + inv.total_amount.toFixed(2) : ''}
          ${inv.issue_date ? ' · ' + inv.issue_date.slice(0, 10) : ''}
        </div>
      </div>
      <div class="invoice-status status-${inv.extract_status}" title="${statusLabel(inv.extract_status)}"></div>
    </div>
  `).join('');
}

function statusLabel(s) {
  return { done: t('statusDone'), pending: t('statusPending'), error: t('statusError') }[s] || s;
}

function updateTotalBadge() {
  document.getElementById('totalBadge').textContent = t('totalBadge', { count: state.invoices.length });
}

function getVisibleInvCardIds() {
  return Array.from(document.querySelectorAll('.inv-card')).map(el => parseInt(el.dataset.id)).filter(Number.isFinite);
}

function clearSelection() {
  state.selectedInvoiceIds.clear();
  state.selectionAnchorId = null;
  refreshSelectionUI();
}

function refreshSelectionUI() {
  const validIds = new Set(getVisibleInvCardIds());
  for (const id of Array.from(state.selectedInvoiceIds)) {
    if (!validIds.has(id)) state.selectedInvoiceIds.delete(id);
  }
  if (state.selectionAnchorId != null && !validIds.has(state.selectionAnchorId)) {
    state.selectionAnchorId = null;
  }

  document.querySelectorAll('.inv-card').forEach(card => {
    const id = parseInt(card.dataset.id);
    const selected = state.selectedInvoiceIds.has(id);
    card.classList.toggle('selected', selected);
    const btn = card.querySelector('.inv-select-btn');
    if (btn) {
      btn.classList.toggle('active', selected);
      btn.setAttribute('aria-pressed', selected ? 'true' : 'false');
    }
  });

  const selectedCount = state.selectedInvoiceIds.size;
  const badge = document.getElementById('selectedCountBadge');
  const clearBtn = document.getElementById('btnClearSelection');
  const delBtn = document.getElementById('btnBatchDelete');
  badge.textContent = t('selectedCount', { count: selectedCount });
  clearBtn.disabled = selectedCount === 0;
  delBtn.disabled = selectedCount === 0;

  // 同步分组级选择按钮状态（用于按组批量删除）
  document.querySelectorAll('.group-select-btn').forEach(btn => {
    const categoryId = btn.dataset.category || '';
    const groupId = btn.dataset.group || '';
    const ids = getGroupInvoiceIds(categoryId, groupId);
    const selectedInGroup = ids.filter(id => state.selectedInvoiceIds.has(id)).length;
    const allSelected = ids.length > 0 && selectedInGroup === ids.length;
    btn.classList.toggle('active', allSelected);
    btn.disabled = ids.length === 0;
    btn.textContent = allSelected ? t('unselectGroup', { count: selectedInGroup }) : t('selectGroup', { count: ids.length });
    btn.title = ids.length === 0
      ? t('noGroupInvoice')
      : (allSelected ? t('unselectGroupTitle') : t('selectGroupTitle'));
  });

  // 同步大类级选择框状态（用于按费用类型批量选择）
  document.querySelectorAll('.col-select-btn').forEach(btn => {
    const categoryId = btn.dataset.category || '';
    const ids = getCategoryInvoiceIds(categoryId);
    const selectedInCategory = ids.filter(id => state.selectedInvoiceIds.has(id)).length;
    const allSelected = ids.length > 0 && selectedInCategory === ids.length;
    btn.classList.toggle('active', allSelected);
    btn.setAttribute('aria-pressed', allSelected ? 'true' : 'false');
    btn.disabled = ids.length === 0;
    btn.title = allSelected ? t('unselectCategoryTitle') : t('selectCategoryTitle');
  });
}

function getGroupInvoiceIds(categoryId, groupId = '') {
  const targetCategory = String(categoryId || '');
  const targetGroup = String(groupId || '');
  const zone = Array.from(document.querySelectorAll('.drop-zone')).find(el =>
    (el.dataset.category || '') === targetCategory && (el.dataset.group || '') === targetGroup
  );
  if (!zone) return [];
  return Array.from(zone.querySelectorAll('.inv-card'))
    .map(el => parseInt(el.dataset.id))
    .filter(Number.isFinite);
}

function getCategoryInvoiceIds(categoryId) {
  const targetCategory = String(categoryId || '');
  return Array.from(document.querySelectorAll(`.drop-zone[data-category="${targetCategory}"] .inv-card`))
    .map(el => parseInt(el.dataset.id))
    .filter(Number.isFinite);
}

function toggleGroupSelect(event, categoryId, groupId = '') {
  event.stopPropagation();
  const ids = getGroupInvoiceIds(categoryId, groupId);
  if (!ids.length) {
    toast(t('noGroupInvoiceWarn'), 'warning');
    return;
  }
  const allSelected = ids.every(id => state.selectedInvoiceIds.has(id));
  ids.forEach(id => {
    if (allSelected) state.selectedInvoiceIds.delete(id);
    else state.selectedInvoiceIds.add(id);
  });
  state.selectionAnchorId = ids[ids.length - 1] || null;
  refreshSelectionUI();
}

function toggleCategorySelect(event, categoryId) {
  event.stopPropagation();
  const ids = getCategoryInvoiceIds(categoryId);
  if (!ids.length) return;
  const allSelected = ids.every(id => state.selectedInvoiceIds.has(id));
  ids.forEach(id => {
    if (allSelected) state.selectedInvoiceIds.delete(id);
    else state.selectedInvoiceIds.add(id);
  });
  state.selectionAnchorId = ids[ids.length - 1] || null;
  refreshSelectionUI();
}

function handleInvCardClick(event, invoiceId) {
  const orderedIds = getVisibleInvCardIds();
  if (!orderedIds.length) return;

  if (event.shiftKey && state.selectionAnchorId != null && orderedIds.includes(state.selectionAnchorId)) {
    const a = orderedIds.indexOf(state.selectionAnchorId);
    const b = orderedIds.indexOf(invoiceId);
    const [start, end] = a < b ? [a, b] : [b, a];
    state.selectedInvoiceIds = new Set(orderedIds.slice(start, end + 1));
  } else if (event.ctrlKey || event.metaKey) {
    if (state.selectedInvoiceIds.has(invoiceId)) state.selectedInvoiceIds.delete(invoiceId);
    else state.selectedInvoiceIds.add(invoiceId);
    state.selectionAnchorId = invoiceId;
  } else {
    state.selectedInvoiceIds = new Set([invoiceId]);
    state.selectionAnchorId = invoiceId;
  }
  refreshSelectionUI();
}

function toggleInvSelect(event, invoiceId) {
  event.stopPropagation();
  if (state.selectedInvoiceIds.has(invoiceId)) state.selectedInvoiceIds.delete(invoiceId);
  else state.selectedInvoiceIds.add(invoiceId);
  state.selectionAnchorId = invoiceId;
  refreshSelectionUI();
}

async function batchDeleteSelectedInvoices() {
  const ids = Array.from(state.selectedInvoiceIds);
  if (!ids.length) return;
  if (!confirm(t('confirmBatchDelete', { count: ids.length }))) return;

  try {
    await api('POST', '/api/invoices/batch-delete', { invoice_ids: ids });
    toast(t('batchDeleteOk', { count: ids.length }), 'success');
    clearSelection();
    await Promise.all([loadInvoices(), loadCollectionResult()]);
  } catch (e) {
    toast(t('batchDeleteFail', { error: e }), 'error');
  }
}

// ─── Process (归集) + SSE Progress Modal ─────────────────────────────────────

let _progressES = null; // 当前 EventSource 引用
let _collectionTaskId = null;
let _progressSSEIntentionalClose = false;

// 归集选项下拉面板
function toggleProcessOptions(e) {
  e.stopPropagation();
  document.getElementById('processOptionsPanel').classList.toggle('hidden');
}

// 点击页面其他地方关闭选项面板
document.addEventListener('click', (e) => {
  const panel = document.getElementById('processOptionsPanel');
  if (!panel.classList.contains('hidden') &&
      !document.getElementById('processBtnGroup').contains(e.target)) {
    panel.classList.add('hidden');
  }

  // 点击非发票卡片区域时，清空多选
  const invCard = e.target.closest('.inv-card');
  const batchPanel = e.target.closest('#batchActions');
  if (!invCard && !batchPanel && state.selectedInvoiceIds.size) {
    clearSelection();
  }
});

document.getElementById('btnClearSelection').addEventListener('click', clearSelection);
document.getElementById('btnBatchDelete').addEventListener('click', batchDeleteSelectedInvoices);

document.getElementById('btnProcess').addEventListener('click', async () => {
  if (!state.invoices.length) { toast(t('noInvoices'), 'warning'); return; }
  // 关闭选项面板
  document.getElementById('processOptionsPanel').classList.add('hidden');

  const body = {
    force_reclassify: document.getElementById('optForceReclassify').checked,
    use_subcategory:  document.getElementById('optSubcategory').checked,
    use_rules:        document.getElementById('optRules').checked,
    use_llm:          document.getElementById('optLLM').checked,
  };

  // 至少要选一种分类方式
  if (!body.use_subcategory && !body.use_rules && !body.use_llm) {
    toast('请至少选择一种分类方式', 'warning'); return;
  }

  try {
    const res = await api('POST', '/api/collections/process', body);
    const taskId = res.data?.task_id;
    if (!taskId) { toast(res.message, 'warning'); return; }
    openProgressModal();
    startProgressSSE(taskId);
  } catch (e) {
    toast(`启动归集失败：${e}`, 'error');
  }
});

// ── 进度弹窗控制 ──────────────────────────────────────────────────────────────
function openProgressModal() {
  // 重置状态
  document.getElementById('progressBarFill').style.width = '0%';
  document.getElementById('progressBarFill').className = 'progress-bar-fill';
  document.getElementById('progressPct').textContent = '0%';
  document.getElementById('progressTitle').textContent = '正在归集…';
  document.getElementById('progressIcon').textContent = '⚙️';
  document.getElementById('progressCurrent').textContent = '任务初始化中…';
  document.getElementById('progressCurrent').className = 'progress-current';
  document.getElementById('progressLog').innerHTML = '';
  document.getElementById('progressError').classList.add('hidden');
  document.getElementById('progressErrorDetail').textContent = '';
  document.getElementById('progressErrorDetail').classList.add('hidden');
  document.getElementById('progressFooter').style.display = 'none';
  document.getElementById('progressClose').classList.add('hidden');
  const cancelBtn = document.getElementById('progressCancel');
  if (cancelBtn) {
    cancelBtn.classList.remove('hidden');
    cancelBtn.disabled = false;
    cancelBtn.textContent = t('btnProgressCancel');
  }
  const viewBtn = document.getElementById('btnViewResult');
  if (viewBtn) viewBtn.style.display = '';
  // 重置步骤点（仅归集弹窗，勿影响上传遮罩）
  progressModalSteps().forEach(el => {
    el.classList.remove('active', 'done', 'error');
  });
  openModal('progressModal');
}

function closeProgressModal() {
  _progressSSEIntentionalClose = true;
  if (_progressES) { _progressES.close(); _progressES = null; }
  _collectionTaskId = null;
  closeModal('progressModal');
}

async function cancelCollectionProcess() {
  const btn = document.getElementById('progressCancel');
  if (!btn || btn.classList.contains('hidden') || btn.disabled) return;
  if (!_collectionTaskId) return;
  btn.disabled = true;
  btn.textContent = t('progressCancelling');
  try {
    await api('POST', `/api/collections/cancel/${_collectionTaskId}`);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = t('btnProgressCancel');
    toast(t('progressCancelFailed', { error: e.message }), 'warning');
  }
}

function toggleErrorDetail() {
  const detail = document.getElementById('progressErrorDetail');
  const btn = document.getElementById('errorToggleBtn');
  const hidden = detail.classList.toggle('hidden');
  btn.textContent = hidden ? '展开 ▼' : '收起 ▲';
}

// ── SSE 订阅 ──────────────────────────────────────────────────────────────────
function startProgressSSE(taskId) {
  if (_progressES) { _progressSSEIntentionalClose = true; _progressES.close(); }

  _collectionTaskId = taskId;
  _progressSSEIntentionalClose = false;
  _progressES = new EventSource(`/api/collections/stream/${taskId}`);

  _progressES.onmessage = (e) => {
    try {
      const evt = JSON.parse(e.data);
      handleProgressEvent(evt);
    } catch (err) {
      console.error('SSE parse error', err);
    }
  };

  _progressES.onerror = () => {
    const es = _progressES;
    _progressES = null;
    if (es) es.close();
    if (!_progressSSEIntentionalClose) {
      appendLog('SSE 连接中断', 'error');
    }
    _progressSSEIntentionalClose = false;
    _collectionTaskId = null;
  };
}

function handleProgressEvent(evt) {
  const { step, total, percent, title, message, status, error } = evt;

  const displayMsg = status === 'cancelled' ? t('progressCancelledDetail') : message;

  // 进度条
  const fill = document.getElementById('progressBarFill');
  fill.style.width = `${percent}%`;
  document.getElementById('progressPct').textContent = `${percent}%`;

  // 步骤点状态（仅归集弹窗）
  progressModalSteps().forEach(el => {
    const s = parseInt(el.dataset.step, 10);
    if (Number.isNaN(s)) return;
    if (status === 'error' || status === 'cancelled') {
      if (s < step) {
        el.classList.remove('active');
        el.classList.add('done');
      } else if (s === step) {
        el.classList.remove('active');
        el.classList.add('error');
      } else {
        el.classList.remove('active', 'done', 'error');
      }
    } else {
      if (s < step) el.classList.add('done');
      else if (s === step) { el.classList.remove('done'); el.classList.add('active'); }
      else el.classList.remove('active', 'done', 'error');
    }
  });

  // 当前步骤描述
  const currentEl = document.getElementById('progressCurrent');
  currentEl.textContent = displayMsg;
  let curCls = 'progress-current';
  if (status === 'error') curCls += ' error';
  else if (status === 'cancelled') curCls += ' cancelled';
  currentEl.className = curCls;

  // 滚动日志
  const logType = status === 'error' ? 'error' : (status === 'done' ? 'done' : (status === 'cancelled' ? 'cancelled' : ''));
  appendLog(displayMsg, logType);

  // 标题
  document.getElementById('progressTitle').textContent =
    status === 'done' ? '归集完成 ✓' :
    status === 'error' ? '归集出错 ✕' :
    status === 'cancelled' ? t('progressCancelledTitle') :
    title || '正在归集…';
  document.getElementById('progressIcon').textContent =
    status === 'done' ? '✅' : status === 'error' ? '❌' : status === 'cancelled' ? '⏹' : '⚙️';

  const cancelBtn = document.getElementById('progressCancel');
  if (cancelBtn && (status === 'done' || status === 'error' || status === 'cancelled')) {
    cancelBtn.classList.add('hidden');
  }

  // 完成 / 错误处理
  if (status === 'done') {
    fill.classList.add('done');
    document.getElementById('progressFooter').style.display = 'flex';
    document.getElementById('progressClose').classList.remove('hidden');
    _progressSSEIntentionalClose = true;
    if (_progressES) { _progressES.close(); _progressES = null; }
    _collectionTaskId = null;
    progressModalSteps().forEach(el => el.classList.add('done'));
    toast('归集完成！', 'success');
  }

  if (status === 'error') {
    fill.classList.add('error');
    document.getElementById('progressFooter').style.display = 'flex';
    document.getElementById('progressClose').classList.remove('hidden');
    document.getElementById('btnViewResult').style.display = 'none';
    // 显示错误详情区域
    const errBox = document.getElementById('progressError');
    errBox.classList.remove('hidden');
    if (error) {
      document.getElementById('progressErrorDetail').textContent = error;
    }
    _progressSSEIntentionalClose = true;
    if (_progressES) { _progressES.close(); _progressES = null; }
    _collectionTaskId = null;
    toast('归集过程出错，请查看进度弹窗', 'error');
  }

  if (status === 'cancelled') {
    fill.classList.add('cancelled');
    document.getElementById('progressFooter').style.display = 'flex';
    document.getElementById('progressClose').classList.remove('hidden');
    document.getElementById('btnViewResult').style.display = 'none';
    document.getElementById('progressError').classList.add('hidden');
    _progressSSEIntentionalClose = true;
    if (_progressES) { _progressES.close(); _progressES = null; }
    _collectionTaskId = null;
    toast(t('progressCancelledToast'), 'warning');
    loadCollectionResult();
  }
}

function appendLog(msg, type = '') {
  const log = document.getElementById('progressLog');
  const now = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  const entry = document.createElement('div');
  entry.className = `log-entry${type ? ' log-' + type : ''}`;
  entry.innerHTML = `<span class="log-time">${now}</span><span class="log-msg">${escHtml(msg)}</span>`;
  log.appendChild(entry);
  log.scrollTop = log.scrollHeight;
}

// ─── Load & Render Collection Result (board) ─────────────────────────────────
async function loadCollectionResult() {
  state.collectionResult = await api('GET', '/api/collections/result');
  renderBoard();
}

function renderBoard() {
  const board = document.getElementById('boardContainer');
  // 销毁旧的 Sortable 实例
  state.sortableInstances.forEach(s => s.destroy());
  state.sortableInstances = [];

  const result = state.collectionResult;
  if (!result) { board.innerHTML = ''; return; }

  const cols = [];
  const displayCategories = buildDisplayCategories(result);

  // 未分类列（如有）
  if (result.unclassified_invoices?.length) {
    cols.push(renderUnclassifiedCol(result.unclassified_invoices));
  }

  // 各大类列
  displayCategories.forEach(cat => {
    cols.push(renderCategoryCol(cat));
  });

  if (!cols.length) {
    board.innerHTML = `<div class="empty-state" style="margin:auto;padding:60px">${t('emptyBoard')}</div>`;
    return;
  }

  board.innerHTML = cols.join('');
  refreshBoardColumnSummaries();

  // 初始化拖拽
  initDragDrop();
  // 重绘后同步多选样式
  refreshSelectionUI();
}

function buildDisplayCategories(result) {
  const existing = new Map((result.categories || []).map(cat => [cat.category_id, cat]));
  const categories = (state.categories || []).map(cfg => {
    const matched = existing.get(cfg.id);
    if (matched) return matched;
    return {
      category_id: cfg.id,
      category_name: cfg.name,
      groupable: !!cfg.groupable,
      groups: [],
      ungrouped_invoices: [],
      total_amount: 0,
    };
  });

  // 兼容后端返回了前端配置中不存在的大类
  (result.categories || []).forEach(cat => {
    if (!categories.some(item => item.category_id === cat.category_id)) {
      categories.push(cat);
    }
  });
  return categories;
}

function renderCategoryCol(cat) {
  const colorClass = CAT_COLORS[cat.category_id] || 'other';
  const icon = CAT_ICONS[cat.category_id] || '🗂️';
  const totalAmt = `¥${Number(cat.total_amount || 0).toFixed(2)}`;

  let bodyHtml = '';

  if (cat.groupable) {
    bodyHtml += `<div class="col-grouping-banner">${t('categoryGrouping')}</div>`;
    bodyHtml += cat.groups.map((g, gi) => renderGroupCard(g, cat)).join('');
    // 无组散票容器始终保留，便于空列时也可拖拽进入
    bodyHtml += `
      <div class="group-card">
        <div class="group-header" onclick="toggleGroup(this)">
          <span class="group-icon">📄</span>
          <span class="group-name">${t('ungrouped')}</span>
          <span class="group-count">${t('invoiceCount', { count: cat.ungrouped_invoices?.length || 0 })}</span>
          <button class="group-select-btn"
                  data-category="${cat.category_id}"
                  data-group=""
                  onclick="toggleGroupSelect(event, '${cat.category_id}', '')"
                  title="${t('selectGroupTitle')}">${t('selectGroup', { count: cat.ungrouped_invoices?.length || 0 })}</button>
          <span class="group-toggle">▼</span>
        </div>
        <div class="group-body drop-zone" data-category="${cat.category_id}" data-group="">
          ${cat.ungrouped_invoices?.map(inv => renderInvCard(inv, cat.category_id, null)).join('') || ''}
          ${!cat.ungrouped_invoices?.length ? `<div class="empty-state">${t('dropHere')}</div>` : ''}
        </div>
      </div>`;
    // 新建分组按钮
    bodyHtml += `<button class="add-rule-btn" onclick="openCreateGroup('${cat.category_id}')">${t('addGroup')}</button>`;
  } else {
    // 无分组（材料费/其他）
    bodyHtml = `
      <div class="col-drop-zone drop-zone" data-category="${cat.category_id}" data-group="">
        ${cat.ungrouped_invoices?.map(inv => renderInvCard(inv, cat.category_id, null)).join('') || ''}
        ${!cat.ungrouped_invoices?.length ? `<div class="empty-state">${t('dropHere')}</div>` : ''}
      </div>`;
  }

  return `
    <div class="board-col" id="col-${cat.category_id}">
      <div class="col-header ${colorClass}">
        <button class="col-select-btn inv-select-btn"
                data-category="${cat.category_id}"
                onclick="toggleCategorySelect(event, '${cat.category_id}')"
                title="${t('selectCategoryTitle')}"
                aria-pressed="false">✓</button>
        <span style="font-size:16px">${icon}</span>
        <span class="col-title">${escHtml(displayCategoryName(cat.category_id, cat.category_name))}</span>
        <span class="col-amount">${totalAmt}</span>
      </div>
      ${bodyHtml}
    </div>`;
}

function renderGroupCard(group, cat) {
  const icon = cat.category_id === 'travel' ? '🗺️' : '📋';
  const dateRange = group.start_date
    ? `${group.start_date}${group.end_date && group.end_date !== group.start_date ? ' ~ ' + group.end_date : ''}`
    : '';
  return `
    <div class="group-card" id="group-card-${group.id}">
      <div class="group-header" onclick="toggleGroup(this)">
        <span class="group-icon">${icon}</span>
        <span class="group-name" ondblclick="renameGroup(${group.id}, this)">${escHtml(group.name)}</span>
        ${dateRange ? `<span class="group-count text-muted" style="font-size:10px">${dateRange}</span>` : ''}
        <span class="group-count">${t('invoiceCount', { count: group.invoices.length })}</span>
        <button class="group-select-btn"
                data-category="${cat.category_id}"
                data-group="${group.id}"
                onclick="toggleGroupSelect(event, '${cat.category_id}', '${group.id}')"
                title="${t('selectGroupTitle')}">${t('selectGroup', { count: group.invoices.length })}</button>
        <span class="group-toggle">▼</span>
      </div>
      <div class="group-body drop-zone" data-category="${cat.category_id}" data-group="${group.id}">
        ${group.invoices.map(inv => renderInvCard(inv, cat.category_id, group.id)).join('')}
      </div>
    </div>`;
}

function renderUnclassifiedCol(invoices) {
  return `
    <div class="board-col" id="col-unclassified">
      <div class="col-header unclassified">
        <span style="font-size:16px">❓</span>
        <span class="col-title">${t('unclassified')}</span>
        <span class="col-amount">${t('invoiceCount', { count: invoices.length })}</span>
      </div>
      <div class="col-drop-zone drop-zone" data-category="unclassified" data-group="">
        ${invoices.map(inv => renderInvCard(inv, 'unclassified', null)).join('')}
      </div>
    </div>`;
}

function renderInvCard(inv, categoryId, groupId) {
  const labels = classifiedByLabels();
  const [tagClass, tagLabel] = labels[inv.classified_by] || labels.default;
  const amt = inv.total_amount != null ? `¥${Number(inv.total_amount).toFixed(2)}` : '';
  const date = (inv.issue_date || '').slice(0, 10);
  const selectedClass = state.selectedInvoiceIds.has(inv.id) ? 'selected' : '';
  return `
    <div class="inv-card ${selectedClass}"
         data-id="${inv.id}"
         data-category="${categoryId}"
         data-group="${groupId || ''}"
         onclick="handleInvCardClick(event, ${inv.id})"
         ondblclick="showInvDetail(${inv.id})">
      <button class="inv-select-btn ${state.selectedInvoiceIds.has(inv.id) ? 'active' : ''}"
              onclick="toggleInvSelect(event, ${inv.id})"
              title="选择/取消选择">✓</button>
      <div class="inv-icon">${invoiceIcon(inv.invoice_type)}</div>
      <div class="inv-body">
        <div class="inv-type">${escHtml(inv.invoice_type || inv.filename || t('unknownType'))}</div>
        <div class="inv-seller">${escHtml(inv.seller_name || '')}</div>
        <div class="inv-row">
          <span class="inv-date">${date}</span>
          <span class="inv-amount">${amt}</span>
        </div>
        <span class="inv-tag ${tagClass}">${tagLabel}</span>
      </div>
      <div class="inv-actions">
        <button onclick="showInvDetail(${inv.id}); event.stopPropagation();" title="${t('viewDetail')}">🔍</button>
        <button onclick="deleteInvoice(${inv.id}); event.stopPropagation();" title="${t('delete')}">🗑️</button>
      </div>
    </div>`;
}

// ─── Drag & Drop (SortableJS) ─────────────────────────────────────────────────
function initDragDrop() {
  const dropZones = document.querySelectorAll('.drop-zone');
  dropZones.forEach(zone => {
    const sortable = Sortable.create(zone, {
      group: 'invoices',
      animation: 150,
      ghostClass: 'sortable-ghost',
      dragClass: 'sortable-drag',
      handle: '.inv-card',
      onStart(evt) {
        const draggedId = parseInt(evt.item.dataset.id);
        const selectedIds = Array.from(state.selectedInvoiceIds);
        const useBatch = selectedIds.length > 1 && selectedIds.includes(draggedId);

        const origins = {};
        const ids = useBatch ? selectedIds : [draggedId];
        ids.forEach(id => {
          const el = document.querySelector(`.inv-card[data-id="${id}"]`);
          origins[id] = {
            category: el?.dataset.category || '',
            group: el?.dataset.group || '',
          };
        });
        _dragContext = { draggedId, ids, useBatch, origins };
      },
      onEnd(evt) {
        const card = evt.item;
        const targetZone = evt.to;
        const targetCategory = targetZone.dataset.category;
        const targetGroup = targetZone.dataset.group ? parseInt(targetZone.dataset.group) : null;
        const context = _dragContext || {
          draggedId: parseInt(card.dataset.id),
          ids: [parseInt(card.dataset.id)],
          useBatch: false,
          origins: {
            [parseInt(card.dataset.id)]: {
              category: card.dataset.category || '',
              group: card.dataset.group || '',
            },
          },
        };

        // 批量拖拽时，把其他选中卡片也移动到目标容器
        if (context.useBatch) {
          context.ids
            .filter(id => id !== context.draggedId)
            .forEach(id => {
              const el = document.querySelector(`.inv-card[data-id="${id}"]`);
              if (!el) return;
              targetZone.appendChild(el);
            });
        }

        const movedIds = context.ids.filter(id => {
          const origin = context.origins[id] || { category: '', group: '' };
          const sameCategory = origin.category === targetCategory;
          const sameGroup = (origin.group || '') === (targetZone.dataset.group || '');
          return !(sameCategory && sameGroup);
        });

        if (!movedIds.length) {
          _dragContext = null;
          return;
        }

        // 乐观更新 DOM 属性
        movedIds.forEach(id => {
          const el = document.querySelector(`.inv-card[data-id="${id}"]`);
          if (!el) return;
          el.dataset.category = targetCategory;
          el.dataset.group = targetGroup || '';
        });
        refreshBoardColumnSummaries();

        if (movedIds.length === 1) {
          moveInvoice(movedIds[0], targetCategory, targetGroup);
        } else {
          moveInvoicesBatch(movedIds, targetCategory, targetGroup);
        }
        _dragContext = null;
      },
    });
    state.sortableInstances.push(sortable);
  });
}

function parseAmountText(text) {
  const normalized = String(text || '').replace(/[^\d.-]/g, '');
  const amount = Number(normalized);
  return Number.isFinite(amount) ? amount : 0;
}

function ensureDropZoneEmptyState(zone) {
  if (!zone) return;
  const hasCards = zone.querySelector('.inv-card');
  const empty = zone.querySelector('.empty-state');
  if (hasCards && empty) {
    empty.remove();
  } else if (!hasCards && !empty) {
    zone.insertAdjacentHTML('beforeend', `<div class="empty-state">${t('dropHere')}</div>`);
  }
}

function refreshBoardColumnSummaries() {
  document.querySelectorAll('.drop-zone').forEach(ensureDropZoneEmptyState);

  document.querySelectorAll('.board-col[id^="col-"]').forEach(col => {
    if (col.id === 'col-unclassified') return;
    const total = Array.from(col.querySelectorAll('.inv-amount'))
      .reduce((sum, amountEl) => sum + parseAmountText(amountEl.textContent), 0);
    const amountEl = col.querySelector('.col-amount');
    if (amountEl) {
      amountEl.textContent = `¥${total.toFixed(2)}`;
    }
  });
}

async function moveInvoice(invoiceId, categoryId, groupId) {
  try {
    await api('PATCH', '/api/collections/move', {
      invoice_id: invoiceId,
      target_category_id: categoryId,
      target_group_id: groupId,
    });
    toast('已更新发票归属', 'success');
    await loadCollectionResult();
  } catch (e) {
    toast(`移动失败：${e}`, 'error');
    // 回滚：重新渲染
    await loadCollectionResult();
  }
}

async function moveInvoicesBatch(invoiceIds, categoryId, groupId) {
  try {
    await api('PATCH', '/api/collections/move/batch', {
      invoice_ids: invoiceIds,
      target_category_id: categoryId,
      target_group_id: groupId,
    });
    toast(`已批量更新 ${invoiceIds.length} 张发票归属`, 'success');
    await loadCollectionResult();
  } catch (e) {
    toast(`批量移动失败：${e}`, 'error');
    await loadCollectionResult();
  }
}

// ─── Group Utilities ──────────────────────────────────────────────────────────
function toggleGroup(header) {
  const body = header.nextElementSibling;
  const toggle = header.querySelector('.group-toggle');
  body.classList.toggle('collapsed');
  toggle.classList.toggle('collapsed');
}

async function renameGroup(groupId, nameEl) {
  const current = nameEl.textContent;
  const newName = prompt('修改分组名称：', current);
  if (!newName || newName === current) return;
  try {
    await api('PATCH', `/api/collections/groups/${groupId}`, { name: newName });
    nameEl.textContent = newName;
    toast('分组名称已更新', 'success');
  } catch (e) {
    toast(`更新失败：${e}`, 'error');
  }
}

let _pendingGroupCategoryId = null;
function openCreateGroup(categoryId) {
  _pendingGroupCategoryId = categoryId;
  const sel = document.getElementById('newGroupCategory');
  sel.innerHTML = state.categories
    .filter(c => c.groupable)
    .map(c => `<option value="${c.id}" ${c.id === categoryId ? 'selected' : ''}>${c.name}</option>`)
    .join('');
  document.getElementById('newGroupName').value = '';
  openModal('createGroupModal');
}

async function confirmCreateGroup() {
  const name = document.getElementById('newGroupName').value.trim();
  const categoryId = document.getElementById('newGroupCategory').value;
  if (!name) { toast('请输入分组名称', 'warning'); return; }
  try {
    await api('POST', '/api/collections/groups', { name, category_id: categoryId, group_type: 'manual' });
    toast('分组已创建', 'success');
    closeModal('createGroupModal');
    await loadCollectionResult();
  } catch (e) {
    toast(`创建失败：${e}`, 'error');
  }
}

// ─── Invoice Detail ───────────────────────────────────────────────────────────
let _currentDetailInvoice = null; // 暂存当前详情发票对象

async function showInvDetail(invoiceId) {
  const inv = await api('GET', `/api/invoices/${invoiceId}`);
  _currentDetailInvoice = inv;

  const fields = [
    ['发票类型', inv.invoice_type], ['发票号码', inv.invoice_number],
    ['开票日期', inv.issue_date], ['销售方', inv.seller_name],
    ['购买方', inv.buyer_name], ['金额', inv.amount != null ? `¥${inv.amount}` : null],
    ['税额', inv.tax_amount != null ? `¥${inv.tax_amount}` : null],
    ['价税合计', inv.total_amount != null ? `¥${inv.total_amount}` : null],
    ['货物/服务', inv.items_description],
    ['备注', inv.remarks],
    ['出发城市', inv.departure_city], ['到达城市', inv.arrival_city],
    ['出发时间', inv.departure_time], ['到达时间', inv.arrival_time],
    ['文件名', inv.filename], ['抽取状态', statusLabel(inv.extract_status)],
  ];

  document.getElementById('detailModalBody').innerHTML = `
    <div style="display:flex;gap:12px;margin-bottom:14px;align-items:flex-start">
      <div style="font-size:40px;line-height:1">${invoiceIcon(inv.invoice_type)}</div>
      <div>
        <div style="font-size:16px;font-weight:600">${escHtml(inv.invoice_type || '未知类型')}</div>
        <div class="text-muted" style="font-size:12px;margin-top:3px">${escHtml(inv.filename)}</div>
      </div>
    </div>
    <hr class="divider" />
    ${fields.filter(([, v]) => v != null && v !== '').map(([k, v]) => `
      <div class="detail-row">
        <div class="detail-label">${k}</div>
        <div class="detail-value">${escHtml(String(v))}</div>
      </div>`).join('')}
  `;
  openModal('detailModal');
}

// ─── Invoice Preview ──────────────────────────────────────────────────────────
function openPreview() {
  if (!_currentDetailInvoice) return;
  const inv = _currentDetailInvoice;
  const filename = inv.filename || '';
  const ext = filename.split('.').pop().toLowerCase();
  const fileUrl = `/api/invoices/${inv.id}/file`;

  document.getElementById('previewFilename').textContent = filename;
  document.getElementById('previewDownloadLink').href = fileUrl;
  document.getElementById('previewDownloadLink').setAttribute('download', filename);

  const body = document.getElementById('previewBody');

  if (['jpg', 'jpeg', 'png', 'bmp', 'tiff', 'tif', 'gif'].includes(ext)) {
    body.innerHTML = `<img src="${fileUrl}" alt="${escHtml(filename)}" />`;
  } else if (ext === 'pdf') {
    body.innerHTML = `<iframe src="${fileUrl}" title="${escHtml(filename)}"></iframe>`;
  } else if (ext === 'ofd') {
    body.innerHTML = `
      <div class="preview-ofd-notice">
        <div class="ofd-icon">📄</div>
        <h3>OFD 格式无法在浏览器中直接预览</h3>
        <p>OFD（开放版式文档）需要专用软件查看。<br/>
           请点击上方「下载」按钮保存到本地后，<br/>
           使用 <strong>福昕阅读器</strong>、<strong>数科OFD阅读器</strong> 等软件打开。</p>
        <a class="btn btn-primary" href="${fileUrl}" download="${escHtml(filename)}" style="margin-top:16px;display:inline-flex">
          下载原件
        </a>
      </div>`;
  } else {
    body.innerHTML = `
      <div class="preview-ofd-notice">
        <div class="ofd-icon">📎</div>
        <h3>该文件类型暂不支持在线预览</h3>
        <p>请点击「下载」按钮保存后使用对应软件查看。</p>
      </div>`;
  }

  openModal('previewModal');
}

async function deleteInvoice(invoiceId) {
  if (!confirm('确认删除该发票？此操作不可撤销。')) return;
  try {
    await api('DELETE', `/api/invoices/${invoiceId}`);
    state.selectedInvoiceIds.delete(invoiceId);
    if (state.selectionAnchorId === invoiceId) state.selectionAnchorId = null;
    toast('已删除', 'success');
    await loadInvoices();
    await loadCollectionResult();
  } catch (e) {
    toast(`删除失败：${e}`, 'error');
  }
}

// ─── Config Modal ─────────────────────────────────────────────────────────────
document.getElementById('btnConfig').addEventListener('click', async () => {
  await loadConfigData();
  openModal('configModal');
});

async function loadConfigData() {
  const [cats, rules, models] = await Promise.all([
    api('GET', '/api/config/categories'),
    api('GET', '/api/config/rules'),
    api('GET', '/api/config/models'),
  ]);

  state.categories = cats.categories;
  state.rules = rules.rules;
  state.modelsConfig = models;

  renderCategoryEditor(cats.categories);
  renderRuleEditor(rules.rules);
  renderModelsEditor(models);
}

// ── Categories Editor ─────────────────────────────────────────────────────────
function renderCategoryEditor(categories) {
  document.getElementById('categoryList').innerHTML = categories.map((c, i) => `
    <div class="rule-item" data-idx="${i}">
      <div class="rule-item-header">
        <span style="font-size:16px">🏷️</span>
        <input class="rule-name-input" value="${escAttr(c.name)}" data-field="name" placeholder="大类名称" />
        <input class="rule-name-input" value="${escAttr(c.id)}" data-field="id" placeholder="ID（英文）" style="width:90px" />
        <button class="rule-delete-btn" onclick="removeCategory(${i})">🗑️</button>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label class="form-label">描述</label>
          <input class="form-input" value="${escAttr(c.description || '')}" data-cat-idx="${i}" data-field="description" />
        </div>
        <div class="form-group" style="flex:0.5">
          <label class="form-label">支持分组</label>
          <select class="form-select" data-cat-idx="${i}" data-field="groupable" ${c.id !== 'other' ? 'disabled' : ''}>
            ${c.id === 'other'
              ? '<option value="false" selected>否（其他费用固定不分组）</option>'
              : '<option value="true" selected>是（默认支持分组）</option>'}
          </select>
        </div>
      </div>
    </div>`).join('');
}

function addCategory() {
  state.categories.push({ id: `cat_${Date.now()}`, name: '新大类', groupable: true, description: '' });
  renderCategoryEditor(state.categories);
}

function removeCategory(idx) {
  const category = state.categories[idx];
  const categoryName = category?.name || `第 ${idx + 1} 个大类`;
  if (!confirm(`确认删除费用大类「${categoryName}」吗？`)) return;
  state.categories.splice(idx, 1);
  renderCategoryEditor(state.categories);
}

function collectCategories() {
  const items = document.querySelectorAll('#categoryList .rule-item');
  return Array.from(items).map(item => {
    const name = item.querySelector('[data-field="name"]').value.trim();
    const id = item.querySelector('[data-field="id"]').value.trim();
    const desc = item.querySelector('[data-field="description"]')?.value.trim() || '';
    const isOther = id === 'other';
    const groupable = isOther ? false : (item.querySelector('[data-field="groupable"]')?.value === 'true');
    return { id, name, groupable, description: desc };
  });
}

// ── Rules Editor ──────────────────────────────────────────────────────────────
const RULE_FIELDS = ['invoice_type', 'seller_name', 'buyer_name', 'items_description',
  'departure_city', 'arrival_city'];
const MATCH_TYPES = ['contains', 'regex', 'equals'];

function renderRuleEditor(rules) {
  document.getElementById('ruleList').innerHTML = rules.map((r, ri) => `
    <div class="rule-item" data-rule-idx="${ri}">
      <div class="rule-item-header">
        <span class="rule-drag-handle">⠿</span>
        <input class="rule-name-input" value="${escAttr(r.name)}" placeholder="规则名称" data-field="name" />
        <select data-field="target_category" style="font-size:12px;padding:4px 6px;border:1px solid #d1d5db;border-radius:4px">
          ${state.categories.map(c => `<option value="${c.id}" ${r.target_category === c.id ? 'selected' : ''}>${c.name}</option>`).join('')}
        </select>
        <input type="number" value="${r.priority}" data-field="priority" placeholder="优先级"
               style="width:55px;font-size:12px;padding:4px 6px;border:1px solid #d1d5db;border-radius:4px" />
        <button class="rule-delete-btn" onclick="removeRule(${ri})">🗑️</button>
      </div>
      <div class="condition-list" id="cond-list-${ri}">
        ${r.conditions.map((cond, ci) => renderConditionRow(ri, ci, cond)).join('')}
      </div>
      <div style="display:flex;align-items:center;gap:12px;margin-top:4px">
        <button class="add-cond-btn" onclick="addCondition(${ri})">＋ 添加条件</button>
        <label style="font-size:12px;color:#6b7280">
          逻辑:
          <select data-field="condition_logic" style="font-size:12px;padding:2px 4px;border:1px solid #d1d5db;border-radius:4px">
            <option value="OR" ${r.condition_logic !== 'AND' ? 'selected' : ''}>OR（任一条件）</option>
            <option value="AND" ${r.condition_logic === 'AND' ? 'selected' : ''}>AND（全部条件）</option>
          </select>
        </label>
      </div>
    </div>`).join('');
}

function renderConditionRow(ri, ci, cond) {
  return `
    <div class="condition-item" data-cond-idx="${ci}">
      <select class="cond-field">
        ${RULE_FIELDS.map(f => `<option value="${f}" ${cond.field === f ? 'selected' : ''}>${f}</option>`).join('')}
      </select>
      <select class="cond-type">
        ${MATCH_TYPES.map(t => `<option value="${t}" ${cond.match_type === t ? 'selected' : ''}>${t}</option>`).join('')}
      </select>
      <input class="cond-value" value="${escAttr(cond.value)}" placeholder="匹配值" />
      <button class="cond-remove" onclick="removeCondition(${ri}, ${ci})">✕</button>
    </div>`;
}

function addRule() {
  state.rules.push({
    id: `rule_${Date.now()}`, name: '新规则', priority: 50,
    conditions: [{ field: 'invoice_type', match_type: 'contains', value: '' }],
    condition_logic: 'OR', target_category: state.categories[0]?.id || 'other',
  });
  renderRuleEditor(state.rules);
}

function removeRule(idx) {
  const rule = state.rules[idx];
  const ruleName = rule?.name || `第 ${idx + 1} 条规则`;
  if (!confirm(`确认删除分类规则「${ruleName}」吗？`)) return;
  state.rules.splice(idx, 1);
  renderRuleEditor(state.rules);
}

function addCondition(ruleIdx) {
  // Collect current state first
  state.rules = collectRules();
  state.rules[ruleIdx].conditions.push({ field: 'invoice_type', match_type: 'contains', value: '' });
  renderRuleEditor(state.rules);
}

function removeCondition(ruleIdx, condIdx) {
  state.rules = collectRules();
  state.rules[ruleIdx].conditions.splice(condIdx, 1);
  renderRuleEditor(state.rules);
}

function collectRules() {
  const ruleItems = document.querySelectorAll('#ruleList .rule-item');
  return Array.from(ruleItems).map(item => {
    const name = item.querySelector('[data-field="name"]').value.trim();
    const target = item.querySelector('[data-field="target_category"]').value;
    const priority = parseInt(item.querySelector('[data-field="priority"]').value) || 50;
    const logic = item.querySelector('[data-field="condition_logic"]').value;
    const condItems = item.querySelectorAll('.condition-item');
    const conditions = Array.from(condItems).map(ci => ({
      field: ci.querySelector('.cond-field').value,
      match_type: ci.querySelector('.cond-type').value,
      value: ci.querySelector('.cond-value').value.trim(),
    }));
    return { id: `rule_${Date.now()}_${Math.random()}`, name, priority, conditions, condition_logic: logic, target_category: target };
  });
}

// ── Models Editor ─────────────────────────────────────────────────────────────
function renderModelsEditor(models) {
  document.getElementById('llmBaseUrl').value = models.llm?.base_url || '';
  document.getElementById('llmModel').value = models.llm?.model || '';
  document.getElementById('llmApiKey').value = models.llm?.api_key || '';
  document.getElementById('llmTimeout').value = models.llm?.timeout || 60;
  document.getElementById('nuHost').value = models.nuextract?.host || '';
  document.getElementById('nuPort').value = models.nuextract?.port || '';
  document.getElementById('nuTimeout').value = models.nuextract?.timeout || 60;
}

async function saveConfig() {
  const activeTab = document.querySelector('.tab-pane.active').id;
  try {
    if (activeTab === 'tabCategories') {
      const cats = collectCategories();
      await api('PUT', '/api/config/categories', { categories: cats });
      toast('费用大类配置已保存', 'success');
    } else if (activeTab === 'tabRules') {
      const rules = collectRules();
      await api('PUT', '/api/config/rules', { rules });
      toast('分类规则已保存', 'success');
    } else if (activeTab === 'tabModels') {
      const models = {
        llm: {
          base_url: document.getElementById('llmBaseUrl').value,
          api_key: document.getElementById('llmApiKey').value,
          model: document.getElementById('llmModel').value,
          timeout: parseInt(document.getElementById('llmTimeout').value),
        },
        nuextract: {
          host: document.getElementById('nuHost').value,
          port: parseInt(document.getElementById('nuPort').value),
          timeout: parseInt(document.getElementById('nuTimeout').value),
        },
      };
      await api('PUT', '/api/config/models', models);
      toast('模型服务配置已保存并生效', 'success');
    }
  } catch (e) {
    toast(`保存失败：${e}`, 'error');
  }
}

// ─── Utility ──────────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function escAttr(str) { return escHtml(str); }

function applyLanguage() {
  document.documentElement.lang = state.lang === 'en' ? 'en' : 'zh-CN';
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.dataset.i18n;
    if (key) el.textContent = t(key);
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    const key = el.dataset.i18nTitle;
    if (key) el.setAttribute('title', t(key));
  });

  const clearBtn = document.getElementById('btnClearSelection');
  const delBtn = document.getElementById('btnBatchDelete');
  const langLabel = document.getElementById('langToggleLabel');
  if (clearBtn) clearBtn.textContent = t('clearSelection');
  if (delBtn) delBtn.textContent = t('batchDelete');
  if (langLabel) langLabel.textContent = state.lang === 'zh' ? 'EN' : '中文';
  document.getElementById('btnLangToggle')?.setAttribute('title', t('languageSwitchTitle'));

  updateTotalBadge();
  refreshSelectionUI();
  renderInvoiceList();
  renderBoard();
}

function switchLanguage() {
  state.lang = state.lang === 'zh' ? 'en' : 'zh';
  localStorage.setItem('invoice_collect_lang', state.lang);
  applyLanguage();
}

document.getElementById('btnLangToggle')?.addEventListener('click', switchLanguage);

// ─── Init ─────────────────────────────────────────────────────────────────────
(async function init() {
  try {
    applyLanguage();
    // 加载大类配置（用于拖拽逻辑中的验证）
    const cats = await api('GET', '/api/config/categories');
    state.categories = cats.categories;

    await loadInvoices();
    await loadCollectionResult();
    applyLanguage();
  } catch (e) {
    console.error('初始化失败：', e);
  }
})();
