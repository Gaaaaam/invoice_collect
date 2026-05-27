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
  nuExtractTemplates: [],// 抽取模板配置
  sortableInstances: [], // SortableJS 实例引用（便于销毁重建）
  selectedInvoiceIds: new Set(), // 看板中多选的发票
  selectionAnchorId: null, // Shift 多选锚点
  lang: localStorage.getItem('invoice_collect_lang') || 'zh',
  /** 历史归档条目（localStorage 持久化），按加入顺序排列 */
  archiveEntries: [],
};

const ARCHIVE_STORAGE_KEY = 'invoice_collect_archive_v1';
const ARCHIVE_PANEL_KEY = 'invoice_collect_archive_panel_visible';
/** 各费用类型「未分组」自定义显示名（localStorage） */
const UNGROUPED_LABEL_STORAGE_PREFIX = 'invoice_collect_ungrouped_label_v1_';

let _dragContext = null; // 记录拖拽开始时的选择上下文
let _archivePanelUiBound = false;

// 大类图标映射
const CAT_ICONS = {
  travel: '✈️', meeting: '📋', material: '📦', other: '🗂️',
};
/** 看板分组标题前图标（与费用类型一致） */
const GROUP_ICONS = {
  travel: '🌏',
  meeting: '📋',
  material: '📦',
  other: '🗂️',
};

function groupIconForCategory(categoryId) {
  return GROUP_ICONS[categoryId] || '📁';
}

function getUngroupedDisplayName(categoryId) {
  try {
    const raw = localStorage.getItem(UNGROUPED_LABEL_STORAGE_PREFIX + categoryId);
    if (raw != null && String(raw).trim()) return String(raw).trim();
  } catch (_) { /* ignore */ }
  return t('ungrouped');
}

/** 有效金额数值：价税合计优先，否则金额+税额；无任何金额字段则为 null */
function invoiceEffectiveAmountNumber(inv) {
  if (!inv) return null;
  if (inv.total_amount != null && Number.isFinite(Number(inv.total_amount))) {
    return Number(inv.total_amount);
  }
  const na = inv.amount != null && Number.isFinite(Number(inv.amount)) ? Number(inv.amount) : null;
  const nt = inv.tax_amount != null && Number.isFinite(Number(inv.tax_amount)) ? Number(inv.tax_amount) : null;
  if (na != null || nt != null) return (na || 0) + (nt || 0);
  return null;
}

/** 卡片/列表展示用金额 */
function formatInvoiceAmountDisplay(inv) {
  const n = invoiceEffectiveAmountNumber(inv);
  return n != null ? `¥${n.toFixed(2)}` : '';
}

const CAT_COLORS = {
  travel: 'travel', meeting: 'meeting', material: 'material', other: 'other',
};
/** 归入四大费用类型即视为「已归集」（绿点） */
const CLASSIFIED_CATEGORY_IDS = new Set(['travel', 'meeting', 'material', 'other']);

function buildCollectionPlacementMap(result) {
  const m = new Map();
  if (!result) return m;
  for (const inv of result.unclassified_invoices || []) {
    if (inv && inv.id != null) m.set(inv.id, 'unclassified');
  }
  for (const cat of result.categories || []) {
    if (!CLASSIFIED_CATEGORY_IDS.has(cat.category_id)) continue;
    for (const g of cat.groups || []) {
      for (const inv of g.invoices || []) {
        if (inv && inv.id != null) m.set(inv.id, 'classified');
      }
    }
    for (const inv of cat.ungrouped_invoices || []) {
      if (inv && inv.id != null) m.set(inv.id, 'classified');
    }
  }
  return m;
}

/** @returns {'problem'|'collected'|'uncollected'} */
function invoiceDotStatusFromPlacement(inv, placement) {
  if (!inv) return 'uncollected';
  if (inv.extract_status === 'error') return 'problem';
  if (inv.extract_status !== 'done') return 'uncollected';
  return placement.get(inv.id) === 'classified' ? 'collected' : 'uncollected';
}

function invoiceListDotTitle(kind) {
  return { collected: t('legendCollected'), uncollected: t('legendUncollected'), problem: t('legendProblem') }[kind] || '';
}
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
    noInvoices: '暂无发票，请先上传',
    allInvoicesArchived: '当前上传的发票均已归档；在右侧「历史归档」中复原后会回到本列表',
    selectedCount: '已选 {count} 张', totalBadge: '共 {count} 张发票',
    clearSelection: '清空选择', batchDelete: '批量删除', batchDeleteSelected: '批量删除已选发票',
    classificationMethods: '分类方式', methodContentAnalysis: '内容分析', methodContentAnalysisHint: '从项目名称/备注推断费用类型',
    methodRuleMatch: '规则匹配', methodRuleMatchHint: '依据 rules.yml 中的自定义规则',
    methodLLM: '大模型（LLM）', methodLLMHint: '当内容分析和规则均未命中时使用',
    forceReclassify: '强制重新分类', forceReclassifyHint: '对已分类的发票也重新执行',
    statusDone: '已抽取', statusPending: '待抽取', statusError: '抽取失败',
    loadingProcessing: '处理中...',     loadingUploadExtract: '上传并抽取发票信息...',
    uploadProgressTitle: '正在上传并处理发票…', uploadSendingHint: '正在上传 {count} 个文件…',
    uploadClassifyHint: '正在判别发票类型，请稍候…',
    uploadExtractHint: '正在识别票面并抽取字段，请稍候…', uploadDoneTitle: '处理完成',
    uploadStepSend: '上传文件', uploadStepClassify: '判别类型', uploadStepExtract: '识别与抽取', uploadStepFinish: '完成',
    progressStepPrepare: '准备中', progressStepClassify: '发票分类', progressStepGroup: '分组',
    progressStepSave: '保存结果', progressStepDone: '归集完成',
    btnProgressCancel: '取消', progressCancelling: '取消中…',
    progressCancelFailed: '取消请求失败：{error}',
    progressCancelledTitle: '归集已取消', progressCancelledDetail: '本次归集已取消，未保存任何结果。',
    progressCancelledToast: '已取消本次归集',
    categoryGrouping: '分组',
    uploadSuccess: '成功上传 {count} 张发票',
    uploadSplitSuccess: '已从 {files} 个文件中识别 {count} 张发票',
    uploadFailed: '上传失败：{error}',
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
    historyArchive: '历史归档',
    archiveCollapse: '收起',
    archiveExpand: '展开',
    archiveExpandTitle: '展开历史归档',
    archiveDropHint: '分组上使用「归档」移入此处；归档中的发票不会参与「开始归集」。勾选多个分组后可点「批量归档」。',
    btnArchive: '归档',
    btnRestore: '复原',
    batchArchive: '批量归档',
    batchRestore: '批量复原',
    archiveAdded: '已加入历史归档',
    archiveOverlap: '该分组中有发票已在归档区，无法重复归档',
    archiveNoUnclassified: '请放入具体费用类型下的区域，不能放入未分类列',
    archiveRestored: '已从归档区恢复到看板',
    archiveRestoreBadTarget: '该归档条目缺少有效的费用类型，无法复原',
    archiveBatchNoneArchive: '请先勾选要归档的分组',
    archiveBatchNoneRestore: '请先勾选要复原的归档条目',
    archiveBatchArchived: '已批量归档 {count} 个分组',
    archiveBatchRestored: '已批量复原 {count} 个分组',
    invoiceStatusLegend: '列表状态',
    legendCollected: '已归集',
    legendUncollected: '未归集',
    legendProblem: '有问题',
    archiveDateUnknown: '未知日期',
  },
  en: {
    appTitle: 'Invoice Collection System', btnProcess: 'Start Collection', processOptions: 'Collection Options', config: 'Settings',
    uploadedInvoices: 'Uploaded Invoices', uploadHint: 'Click or drag files to upload', uploadSupport: 'Supports PDF, images, OFD',
    noInvoices: 'No invoices yet, please upload first',
    allInvoicesArchived: 'All uploaded invoices are archived; restore from History Archive to show them here again',
    selectedCount: 'Selected {count}', totalBadge: '{count} invoices',
    clearSelection: 'Clear Selection', batchDelete: 'Batch Delete', batchDeleteSelected: 'Delete selected invoices',
    classificationMethods: 'Classification Methods', methodContentAnalysis: 'Content Analysis', methodContentAnalysisHint: 'Infer expense type from item name/remarks',
    methodRuleMatch: 'Rule Matching', methodRuleMatchHint: 'Use custom rules in rules.yml',
    methodLLM: 'LLM', methodLLMHint: 'Fallback when analysis/rules do not match',
    forceReclassify: 'Force Reclassify', forceReclassifyHint: 'Reclassify already-classified invoices',
    statusDone: 'Extracted', statusPending: 'Pending', statusError: 'Failed',
    loadingProcessing: 'Processing...',     loadingUploadExtract: 'Uploading and extracting invoice data...',
    uploadProgressTitle: 'Uploading and processing…', uploadSendingHint: 'Uploading {count} file(s)…',
    uploadClassifyHint: 'Classifying invoice type, please wait…',
    uploadExtractHint: 'Extracting fields on server, please wait…', uploadDoneTitle: 'Done',
    uploadStepSend: 'Upload', uploadStepClassify: 'Classify Type', uploadStepExtract: 'Extract', uploadStepFinish: 'Done',
    progressStepPrepare: 'Preparing', progressStepClassify: 'Classification', progressStepGroup: 'Grouping',
    progressStepSave: 'Saving', progressStepDone: 'Complete',
    btnProgressCancel: 'Cancel', progressCancelling: 'Cancelling…',
    progressCancelFailed: 'Could not cancel: {error}',
    progressCancelledTitle: 'Collection cancelled',
    progressCancelledDetail: 'Cancelled. No changes were saved.',
    progressCancelledToast: 'Collection was cancelled',
    categoryGrouping: 'Groups',
    uploadSuccess: 'Uploaded {count} invoice(s)',
    uploadSplitSuccess: 'Recognized {count} invoice(s) from {files} file(s)',
    uploadFailed: 'Upload failed: {error}',
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
    historyArchive: 'Archive',
    archiveCollapse: 'Hide',
    archiveExpand: 'Show',
    archiveExpandTitle: 'Show archive panel',
    archiveDropHint: 'Use Archive on each group to move it here. Archived invoices are excluded from Start Collection. Select multiple groups for batch archive.',
    btnArchive: 'Archive',
    btnRestore: 'Restore',
    batchArchive: 'Archive selected',
    batchRestore: 'Restore selected',
    archiveAdded: 'Moved to archive',
    archiveOverlap: 'Some invoices are already archived',
    archiveNoUnclassified: 'Drop on a category column, not Unclassified',
    archiveRestored: 'Restored from archive',
    archiveRestoreBadTarget: 'This archive entry has no valid category',
    archiveBatchNoneArchive: 'Select groups to archive first',
    archiveBatchNoneRestore: 'Select archive entries to restore first',
    archiveBatchArchived: 'Archived {count} group(s)',
    archiveBatchRestored: 'Restored {count} group(s)',
    invoiceStatusLegend: 'List status',
    legendCollected: 'Collected',
    legendUncollected: 'Not collected',
    legendProblem: 'Has issues',
    archiveDateUnknown: 'Unknown date',
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
function formatApiErrorMessage(err, fallbackText) {
  if (!err) return fallbackText || t('requestFailed');
  const detail = err.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map(item => {
      if (!item) return '';
      if (typeof item === 'string') return item;
      if (typeof item.msg === 'string') {
        const loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
        return loc ? `${loc}: ${item.msg}` : item.msg;
      }
      return '';
    }).filter(Boolean);
    if (parts.length) return parts.join('；');
  }
  if (detail && typeof detail === 'object') {
    if (typeof detail.msg === 'string') return detail.msg;
    return JSON.stringify(detail);
  }
  if (typeof err.message === 'string' && err.message.trim()) return err.message;
  return fallbackText || t('requestFailed');
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(formatApiErrorMessage(err, res.statusText));
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
 * @param {'upload'|'classify'|'extract'|'done'|'error_upload'|'error_classify'|'error_extract'} phase
 * 步骤索引：0=上传文件, 1=判别类型, 2=识别与抽取, 3=完成
 */
function setUploadOverlayPhase(phase, detail = '') {
  const steps = document.querySelectorAll('#uploadProgressSteps .progress-step');
  const detailEl = document.getElementById('loadingDetailText');
  if (detailEl && detail !== undefined) detailEl.textContent = detail || '';
  steps.forEach((el, i) => {
    el.classList.remove('active', 'done', 'error');
    if (phase === 'upload') {
      if (i === 0) el.classList.add('active');
    } else if (phase === 'classify') {
      if (i === 0) el.classList.add('done');
      if (i === 1) el.classList.add('active');
    } else if (phase === 'extract') {
      if (i <= 1) el.classList.add('done');
      if (i === 2) el.classList.add('active');
    } else if (phase === 'done') {
      el.classList.add('done');
    } else if (phase === 'error_upload') {
      if (i === 0) el.classList.add('error');
    } else if (phase === 'error_classify') {
      if (i === 0) el.classList.add('done');
      if (i === 1) el.classList.add('error');
    } else if (phase === 'error_extract') {
      if (i <= 1) el.classList.add('done');
      if (i === 2) el.classList.add('error');
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

/**
 * 使用 XHR 上传，并在上传完成后依次展示「判别类型」→「识别与抽取」两个阶段节点。
 * 服务端双阶段流程：Step1 分类（约2-5s）→ Step2 抽取（约3-6s）。
 * 此处用 1500ms 延迟在 UI 上区分两个阶段，直观呈现处理进度。
 */
function uploadInvoicesXHR(formData) {
  return new Promise((resolve, reject) => {
    let bodySent = false;
    let classifyTimer = null;
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/invoices/upload');
    xhr.responseType = 'json';
    xhr.upload.onload = () => {
      bodySent = true;
      // 文件体已发送，服务端开始处理：先展示「判别类型」阶段
      setUploadOverlayPhase('classify', t('uploadClassifyHint'));
      // 约 1.5 秒后切换到「识别与抽取」阶段
      classifyTimer = setTimeout(() => {
        setUploadOverlayPhase('extract', t('uploadExtractHint'));
        classifyTimer = null;
      }, 1500);
    };
    xhr.onload = () => {
      if (classifyTimer) { clearTimeout(classifyTimer); classifyTimer = null; }
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
      if (classifyTimer) { clearTimeout(classifyTimer); classifyTimer = null; }
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
  const modal = document.getElementById('configModal');
  if (!modal) return;
  modal.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  modal.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const pane = document.getElementById(activeId);
  if (!pane) return;
  pane.classList.add('active');
  const activeBtn = modal.querySelector(`.tab-btn[data-tab="${activeId}"]`);
  if (activeBtn) activeBtn.classList.add('active');
}

let _configModalUiBound = false;
function bindConfigModalEvents() {
  if (_configModalUiBound) return;
  _configModalUiBound = true;
  const modal = document.getElementById('configModal');
  if (!modal) return;

  modal.querySelectorAll('.tab-btn[data-tab]').forEach(btn => {
    const tabId = btn.dataset.tab;
    btn.onclick = null;
    btn.addEventListener('click', () => switchTab(tabId));
  });

  const addTplBtn = document.getElementById('btnAddNuExtractTemplate');
  if (addTplBtn) {
    addTplBtn.onclick = null;
    addTplBtn.addEventListener('click', addNuExtractTemplate);
  }

  const tplList = document.getElementById('nuextractTemplateList');
  if (tplList) {
    tplList.addEventListener('click', handleTemplateEditorClick);
    tplList.addEventListener('change', handleTemplateEditorChange);
  }
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
    if (uploaded.length > files.length) {
      toast(t('uploadSplitSuccess', { count: uploaded.length, files: files.length }), 'success');
    } else {
      toast(t('uploadSuccess', { count: uploaded.length }), 'success');
    }
    await loadInvoices();
    await new Promise(r => setTimeout(r, 400));
  } catch (e) {
    let errPhase = 'error_extract';
    if (e && e._phase === 'upload') errPhase = 'error_upload';
    else if (e && e._phase === 'classify') errPhase = 'error_classify';
    setUploadOverlayPhase(errPhase, String(e.message || e));
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
  const archived = getArchivedInvoiceIdSet();
  const visible = state.invoices.filter(inv => !archived.has(inv.id));
  if (!visible.length) {
    el.innerHTML = `<div class="empty-state">${t('allInvoicesArchived')}</div>`;
    return;
  }
  const placement = buildCollectionPlacementMap(state.collectionResult);
  el.innerHTML = visible.map(inv => {
    const dotKind = invoiceDotStatusFromPlacement(inv, placement);
    return `
    <div class="invoice-item" data-id="${inv.id}" title="${inv.filename}">
      <div class="invoice-thumb">${invoiceIcon(inv.invoice_type)}</div>
      <div class="invoice-meta">
        <div class="invoice-name">${escHtml(inv.filename)}</div>
        <div class="invoice-sub">
          ${formatInvoiceAmountDisplay(inv)}
          ${inv.issue_date ? ' · ' + inv.issue_date.slice(0, 10) : ''}
        </div>
      </div>
      <div class="invoice-status status-dot-${dotKind}" title="${escAttr(invoiceListDotTitle(dotKind))}"></div>
    </div>`;
  }).join('');
}

function statusLabel(s) {
  return { done: t('statusDone'), pending: t('statusPending'), error: t('statusError') }[s] || s;
}

function updateTotalBadge() {
  const archived = getArchivedInvoiceIdSet();
  const n = state.invoices.filter(inv => !archived.has(inv.id)).length;
  document.getElementById('totalBadge').textContent = t('totalBadge', { count: n });
}

/** 左侧列表与顶部数量与归档状态同步（归档/复原/看板重绘后调用） */
function syncUploadedInvoicePanel() {
  renderInvoiceList();
  updateTotalBadge();
}

// ─── 历史归档（localStorage + 归集排除）────────────────────────────────────────
function loadArchiveFromStorage() {
  try {
    const raw = localStorage.getItem(ARCHIVE_STORAGE_KEY);
    if (!raw) {
      state.archiveEntries = [];
      return;
    }
    const j = JSON.parse(raw);
    const arr = Array.isArray(j.entries) ? j.entries : [];
    state.archiveEntries = arr.filter(e => e && e.id && Array.isArray(e.invoiceIds) && e.invoiceIds.length);
  } catch {
    state.archiveEntries = [];
  }
}

function saveArchiveToStorage() {
  localStorage.setItem(ARCHIVE_STORAGE_KEY, JSON.stringify({ entries: state.archiveEntries }));
}

function pruneArchiveEntries() {
  const valid = new Set((state.invoices || []).map(i => i.id));
  let changed = false;
  const next = [];
  for (const ent of state.archiveEntries || []) {
    const ids = (ent.invoiceIds || []).filter(id => valid.has(id));
    if (!ids.length) {
      changed = true;
      continue;
    }
    if (ids.length !== (ent.invoiceIds || []).length) changed = true;
    next.push({ ...ent, invoiceIds: ids });
  }
  if (changed || next.length !== (state.archiveEntries || []).length) {
    state.archiveEntries = next;
    saveArchiveToStorage();
  }
}

function getArchivedInvoiceIdSet() {
  const s = new Set();
  for (const e of state.archiveEntries || []) {
    for (const id of e.invoiceIds || []) s.add(id);
  }
  return s;
}

function applyArchiveVisibility(result) {
  if (!result) return null;
  const archived = getArchivedInvoiceIdSet();
  const fi = list => (list || []).filter(inv => !archived.has(inv.id));
  const categories = (result.categories || []).map(cat => ({
    ...cat,
    groups: (cat.groups || [])
      .map(g => {
        const invs = fi(g.invoices);
        if (!invs.length) return null;
        return { ...g, invoices: invs };
      })
      .filter(Boolean),
    ungrouped_invoices: fi(cat.ungrouped_invoices),
  }));
  const unclassified_invoices = fi(result.unclassified_invoices);
  return { ...result, categories, unclassified_invoices };
}

function findInvoiceInResult(invoiceId) {
  const r = state.collectionResult;
  if (!r) return null;
  for (const c of r.categories || []) {
    for (const g of c.groups || []) {
      const inv = (g.invoices || []).find(i => i.id === invoiceId);
      if (inv) return inv;
    }
    for (const inv of c.ungrouped_invoices || []) {
      if (inv.id === invoiceId) return inv;
    }
  }
  for (const inv of r.unclassified_invoices || []) {
    if (inv.id === invoiceId) return inv;
  }
  return (state.invoices || []).find(i => i.id === invoiceId) || null;
}

function makeArchiveEntryObject(payload) {
  return {
    id: (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : `a${Date.now()}_${Math.random().toString(36).slice(2, 9)}`,
    categoryId: payload.categoryId,
    groupId: payload.groupId,
    groupName: payload.groupName || '',
    categoryName: payload.categoryName || '',
    invoiceIds: [...payload.invoiceIds],
    addedAt: Date.now(),
  };
}

/** 基于当前归集结果构建归档载荷（排除已在归档中的发票） */
function buildArchiveEntryPayload(categoryId, groupIdOpt) {
  const raw = state.collectionResult;
  if (!raw?.categories) return null;
  const cat = raw.categories.find(c => c.category_id === categoryId);
  if (!cat) return null;
  const archived = getArchivedInvoiceIdSet();
  let invoiceIds;
  let groupName;
  let gidStore = null;
  if (groupIdOpt != null && Number.isFinite(Number(groupIdOpt))) {
    const gid = Number(groupIdOpt);
    const g = (cat.groups || []).find(x => x.id === gid);
    if (!g?.invoices?.length) return null;
    invoiceIds = g.invoices.map(i => i.id).filter(id => !archived.has(id));
    groupName = g.name || '';
    gidStore = gid;
  } else {
    invoiceIds = (cat.ungrouped_invoices || []).map(i => i.id).filter(id => !archived.has(id));
    groupName = getUngroupedDisplayName(categoryId);
    gidStore = null;
  }
  if (!invoiceIds.length) return null;
  return {
    categoryId,
    groupId: gidStore,
    groupName,
    categoryName: cat.category_name || '',
    invoiceIds,
  };
}

function tryArchiveBoardGroup(categoryId, groupIdOpt) {
  const payload = buildArchiveEntryPayload(categoryId, groupIdOpt);
  if (!payload) {
    toast(t('noGroupInvoiceWarn'), 'warning');
    return;
  }
  const busy = getArchivedInvoiceIdSet();
  if (payload.invoiceIds.some(id => busy.has(id))) {
    toast(t('archiveOverlap'), 'warning');
    return;
  }
  state.archiveEntries.push(makeArchiveEntryObject(payload));
  saveArchiveToStorage();
  renderBoard();
  renderArchivePanel();
  toast(t('archiveAdded'), 'success');
}

function archiveBoardGroupFromBtn(ev, btn) {
  ev.stopPropagation();
  const cid = btn?.dataset?.archiveCat;
  if (!cid) return;
  const raw = btn.dataset.archiveGid;
  const gid = raw === '' || raw === undefined ? null : parseInt(raw, 10);
  tryArchiveBoardGroup(cid, Number.isFinite(gid) ? gid : null);
}

function batchArchiveSelectedBoardGroups() {
  const checked = Array.from(document.querySelectorAll('.board-archive-select:checked'));
  if (!checked.length) {
    toast(t('archiveBatchNoneArchive'), 'warning');
    return;
  }
  let ok = 0;
  for (const cb of checked) {
    const card = cb.closest('.group-card');
    if (!card?.dataset.boardCategory) continue;
    const cid = card.dataset.boardCategory;
    const raw = card.dataset.boardGroupId;
    const gidOpt = raw === '' || raw === undefined ? null : parseInt(raw, 10);
    const payload = buildArchiveEntryPayload(cid, Number.isFinite(gidOpt) ? gidOpt : null);
    if (!payload) continue;
    const busy = getArchivedInvoiceIdSet();
    if (payload.invoiceIds.some(id => busy.has(id))) {
      toast(t('archiveOverlap'), 'warning');
      continue;
    }
    state.archiveEntries.push(makeArchiveEntryObject(payload));
    ok += 1;
  }
  if (ok > 0) {
    saveArchiveToStorage();
    renderBoard();
    renderArchivePanel();
    toast(t('archiveBatchArchived', { count: ok }), 'success');
  } else {
    toast(t('archiveBatchNoneArchive'), 'warning');
  }
}

/** 按条目内保存的费用类型与分组 ID 复原（不依赖拖放落点） */
async function restoreArchiveEntry(entryId) {
  const idx = state.archiveEntries.findIndex(x => x.id === entryId);
  if (idx < 0) return;
  const entry = state.archiveEntries[idx];
  const targetCategory = entry.categoryId;
  const targetGroup = entry.groupId != null && entry.groupId !== '' && Number.isFinite(Number(entry.groupId))
    ? Number(entry.groupId)
    : null;
  if (!targetCategory || targetCategory === 'unclassified') {
    toast(t('archiveRestoreBadTarget'), 'warning');
    return;
  }
  const ids = [...entry.invoiceIds];
  state.archiveEntries.splice(idx, 1);
  saveArchiveToStorage();
  renderBoard();
  renderArchivePanel();
  try {
    if (ids.length === 1) {
      await api('PATCH', '/api/collections/move', {
        invoice_id: ids[0],
        target_category_id: targetCategory,
        target_group_id: targetGroup,
      });
    } else {
      await api('PATCH', '/api/collections/move/batch', {
        invoice_ids: ids,
        target_category_id: targetCategory,
        target_group_id: targetGroup,
      });
    }
    toast(t('archiveRestored'), 'success');
    await loadCollectionResult();
  } catch (e) {
    state.archiveEntries.splice(idx, 0, entry);
    saveArchiveToStorage();
    renderBoard();
    renderArchivePanel();
    toast(`${t('requestFailed')}: ${e.message || e}`, 'error');
  }
}

function restoreArchiveEntryFromBtn(ev, btn) {
  ev.stopPropagation();
  const id = btn?.getAttribute('data-archive-restore-id');
  if (id) void restoreArchiveEntry(id);
}

async function batchRestoreSelectedArchiveEntries() {
  const entryIds = Array.from(document.querySelectorAll('.archive-entry-select:checked'))
    .map(cb => cb.closest('.archive-group-card')?.dataset.archiveEntryId)
    .filter(Boolean);
  if (!entryIds.length) {
    toast(t('archiveBatchNoneRestore'), 'warning');
    return;
  }
  const idSet = new Set(entryIds);
  const removed = state.archiveEntries.filter(e => idSet.has(e.id));
  if (!removed.length) {
    toast(t('archiveBatchNoneRestore'), 'warning');
    return;
  }
  state.archiveEntries = state.archiveEntries.filter(e => !idSet.has(e.id));
  saveArchiveToStorage();
  renderBoard();
  renderArchivePanel();
  const rollback = () => {
    state.archiveEntries.push(...removed);
    saveArchiveToStorage();
    renderBoard();
    renderArchivePanel();
  };
  try {
    for (const ent of removed) {
      const ids = [...ent.invoiceIds];
      const tc = ent.categoryId;
      const tg = ent.groupId != null && ent.groupId !== '' && Number.isFinite(Number(ent.groupId))
        ? Number(ent.groupId)
        : null;
      if (!tc || tc === 'unclassified') throw new Error(t('archiveRestoreBadTarget'));
      if (ids.length === 1) {
        await api('PATCH', '/api/collections/move', {
          invoice_id: ids[0],
          target_category_id: tc,
          target_group_id: tg,
        });
      } else {
        await api('PATCH', '/api/collections/move/batch', {
          invoice_ids: ids,
          target_category_id: tc,
          target_group_id: tg,
        });
      }
    }
    toast(t('archiveBatchRestored', { count: removed.length }), 'success');
    await loadCollectionResult();
  } catch (e) {
    rollback();
    toast(`${t('requestFailed')}: ${e.message || e}`, 'error');
  }
}

function archiveEntryDateKey(ent) {
  const ts = ent?.addedAt;
  if (ts == null || !Number.isFinite(Number(ts))) return '__unknown__';
  const d = new Date(Number(ts));
  if (Number.isNaN(d.getTime())) return '__unknown__';
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function formatArchiveSectionDateLabel(dateKey) {
  if (dateKey === '__unknown__') return t('archiveDateUnknown');
  const parts = dateKey.split('-').map(Number);
  if (parts.length !== 3 || parts.some(n => !Number.isFinite(n))) return t('archiveDateUnknown');
  const [y, mo, da] = parts;
  const d = new Date(y, mo - 1, da);
  if (state.lang === 'en') {
    return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
  }
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });
}

function renderArchiveEntryCard(ent) {
  const catLabel = displayCategoryName(ent.categoryId, ent.categoryName || '');
  const n = (ent.invoiceIds || []).length;
  const rows = (ent.invoiceIds || []).map(id => {
    const inv = findInvoiceInResult(id);
    const title = inv?.invoice_type || inv?.filename || `#${id}`;
    const amt = formatInvoiceAmountDisplay(inv);
    const date = (inv?.issue_date || '').slice(0, 10);
    return `
        <div class="archive-inv-row">
          <span class="archive-inv-icon">${invoiceIcon(inv?.invoice_type)}</span>
          <div class="archive-inv-info">
            <div class="archive-inv-title">${escHtml(title)}</div>
            <div class="archive-inv-sub">${escHtml(date)}${amt ? ' · ' + amt : ''}</div>
          </div>
        </div>`;
  }).join('');
  const gname = ent.groupName || t('ungrouped');
  return `
      <div class="archive-group-card" data-archive-entry-id="${escAttr(ent.id)}">
        <div class="archive-group-header" onclick="toggleArchiveEntry(event, this)">
          <input type="checkbox" class="archive-entry-select" onclick="event.stopPropagation()"
                 title="" aria-label="select archive entry" />
          <span class="archive-cat-badge" title="${escAttr(catLabel)}">${escHtml(catLabel)}</span>
          <span class="archive-group-name" title="${escAttr(ent.groupName || '')}">${escHtml(gname)}</span>
          <span class="archive-group-meta">${t('invoiceCount', { count: n })}</span>
          <div class="archive-entry-actions">
            <button type="button" class="btn btn-outline btn-sm btn-restore-archive"
                    data-archive-restore-id="${escAttr(ent.id)}"
                    onclick="restoreArchiveEntryFromBtn(event, this)">${t('btnRestore')}</button>
          </div>
          <span class="archive-group-toggle group-toggle">▼</span>
        </div>
        <div class="archive-group-body collapsed">${rows}</div>
      </div>`;
}

function renderArchivePanel() {
  const mount = document.getElementById('archiveEntries');
  const hint = document.getElementById('archiveEmptyHint');
  if (!mount) return;
  if (!(state.archiveEntries || []).length) {
    mount.innerHTML = '';
    if (hint) hint.classList.remove('hidden');
    return;
  }
  if (hint) hint.classList.add('hidden');
  const byDate = new Map();
  for (const ent of state.archiveEntries) {
    const k = archiveEntryDateKey(ent);
    if (!byDate.has(k)) byDate.set(k, []);
    byDate.get(k).push(ent);
  }
  for (const arr of byDate.values()) {
    arr.sort((a, b) => (Number(b.addedAt) || 0) - (Number(a.addedAt) || 0));
  }
  const keys = [...byDate.keys()].sort((a, b) => {
    if (a === '__unknown__') return 1;
    if (b === '__unknown__') return -1;
    return b.localeCompare(a);
  });
  mount.innerHTML = keys.map(dateKey => {
    const entries = byDate.get(dateKey);
    const heading = formatArchiveSectionDateLabel(dateKey);
    const cards = entries.map(renderArchiveEntryCard).join('');
    return `<div class="archive-date-section"><div class="archive-date-heading">${escHtml(heading)}</div>${cards}</div>`;
  }).join('');
}

function toggleArchiveEntry(ev, header) {
  if (ev && (ev.target.closest('button') || ev.target.closest('input[type="checkbox"]'))) return;
  const body = header.nextElementSibling;
  const toggle = header.querySelector('.archive-group-toggle');
  if (body) body.classList.toggle('collapsed');
  if (toggle) toggle.classList.toggle('collapsed');
}

function syncArchiveToggleLabels() {
  const layout = document.getElementById('boardLayout');
  const collapsed = layout?.classList.contains('archive-collapsed');
  const btn = document.getElementById('btnArchiveToggle');
  if (btn) btn.textContent = collapsed ? t('archiveExpand') : t('archiveCollapse');
  document.getElementById('btnArchiveExpand')?.setAttribute('title', t('archiveExpandTitle'));
}

function applyArchivePanelCollapsedState() {
  const layout = document.getElementById('boardLayout');
  if (!layout) return;
  if (localStorage.getItem(ARCHIVE_PANEL_KEY) === '0') {
    layout.classList.add('archive-collapsed');
  } else {
    layout.classList.remove('archive-collapsed');
  }
  syncArchiveToggleLabels();
}

function setupArchivePanelOnce() {
  if (_archivePanelUiBound) return;
  _archivePanelUiBound = true;
  const layout = document.getElementById('boardLayout');
  document.getElementById('btnArchiveToggle')?.addEventListener('click', () => {
    layout?.classList.add('archive-collapsed');
    localStorage.setItem(ARCHIVE_PANEL_KEY, '0');
    syncArchiveToggleLabels();
  });
  document.getElementById('btnArchiveExpand')?.addEventListener('click', () => {
    layout?.classList.remove('archive-collapsed');
    localStorage.setItem(ARCHIVE_PANEL_KEY, '1');
    syncArchiveToggleLabels();
  });
  document.getElementById('btnBatchArchiveBoard')?.addEventListener('click', batchArchiveSelectedBoardGroups);
  document.getElementById('btnBatchRestoreArchive')?.addEventListener('click', () => {
    void batchRestoreSelectedArchiveEntries();
  });
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

  const excl = Array.from(getArchivedInvoiceIdSet());
  const body = {
    force_reclassify: document.getElementById('optForceReclassify').checked,
    use_subcategory:  document.getElementById('optSubcategory').checked,
    use_rules:        document.getElementById('optRules').checked,
    use_llm:          document.getElementById('optLLM').checked,
    exclude_invoice_ids: excl.length ? excl : undefined,
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
  pruneArchiveEntries();
  renderBoard();
  renderArchivePanel();
}

function renderBoard() {
  const board = document.getElementById('boardContainer');
  // 销毁旧的 Sortable 实例
  state.sortableInstances.forEach(s => s.destroy());
  state.sortableInstances = [];

  const raw = state.collectionResult;
  if (!raw) {
    board.innerHTML = '';
    syncUploadedInvoicePanel();
    return;
  }

  const result = applyArchiveVisibility(raw);
  if (!result) {
    board.innerHTML = '';
    syncUploadedInvoicePanel();
    return;
  }

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
    syncUploadedInvoicePanel();
    return;
  }

  board.innerHTML = cols.join('');
  refreshBoardColumnSummaries();

  // 初始化拖拽
  initDragDrop();
  // 重绘后同步多选样式
  refreshSelectionUI();
  syncUploadedInvoicePanel();
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
    const ungName = getUngroupedDisplayName(cat.category_id);
    bodyHtml += `
      <div class="group-card" data-board-category="${escAttr(cat.category_id)}" data-board-group-id="">
        <div class="group-header" onclick="toggleGroup(event, this)">
          <div class="group-header-row1">
          <input type="checkbox" class="board-archive-select" onclick="event.stopPropagation()"
                 title="${escAttr(t('batchArchive'))}" aria-label="${escAttr(t('batchArchive'))}" />
          <span class="group-icon">${groupIconForCategory(cat.category_id)}</span>
          <span class="group-name" title="${escAttr(ungName)}" ondblclick="renameUngroupedLabel(event, '${cat.category_id}', this)">${escHtml(ungName)}</span>
          <span class="group-count">${t('invoiceCount', { count: cat.ungrouped_invoices?.length || 0 })}</span>
          <button type="button" class="btn btn-outline btn-sm btn-archive-group"
                  data-archive-cat="${escAttr(cat.category_id)}" data-archive-gid=""
                  onclick="archiveBoardGroupFromBtn(event, this)"
                  title="${escAttr(t('btnArchive'))}">${t('btnArchive')}</button>
          <button class="group-select-btn"
                  data-category="${cat.category_id}"
                  data-group=""
                  onclick="toggleGroupSelect(event, '${cat.category_id}', '')"
                  title="${t('selectGroupTitle')}">${t('selectGroup', { count: cat.ungrouped_invoices?.length || 0 })}</button>
          <span class="group-toggle">▼</span>
          </div>
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
  const icon = groupIconForCategory(cat.category_id);
  const dateRange = group.start_date
    ? `${group.start_date}${group.end_date && group.end_date !== group.start_date ? ' ~ ' + group.end_date : ''}`
    : '';
  const gname = group.name || '';
  return `
    <div class="group-card" id="group-card-${group.id}"
         data-board-category="${cat.category_id}" data-board-group-id="${group.id}">
      <div class="group-header" onclick="toggleGroup(event, this)">
        <div class="group-header-row1">
        <input type="checkbox" class="board-archive-select" onclick="event.stopPropagation()"
               title="${escAttr(t('batchArchive'))}" aria-label="${escAttr(t('batchArchive'))}" />
        <span class="group-icon">${icon}</span>
        <span class="group-name" title="${escAttr(gname)}" ondblclick="renameGroup(event, ${group.id}, this)">${escHtml(gname)}</span>
        <span class="group-count">${t('invoiceCount', { count: group.invoices.length })}</span>
        <button type="button" class="btn btn-outline btn-sm btn-archive-group"
                data-archive-cat="${escAttr(cat.category_id)}" data-archive-gid="${group.id}"
                onclick="archiveBoardGroupFromBtn(event, this)"
                title="${escAttr(t('btnArchive'))}">${t('btnArchive')}</button>
        <button class="group-select-btn"
                data-category="${cat.category_id}"
                data-group="${group.id}"
                onclick="toggleGroupSelect(event, '${cat.category_id}', '${group.id}')"
                title="${t('selectGroupTitle')}">${t('selectGroup', { count: group.invoices.length })}</button>
        <span class="group-toggle">▼</span>
        </div>
        ${dateRange ? `<div class="group-header-row2"><span class="group-date-range" title="${escAttr(dateRange)}">${escHtml(dateRange)}</span></div>` : ''}
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
  const amt = formatInvoiceAmountDisplay(inv);
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
function toggleGroup(ev, header) {
  if (ev && (ev.target.closest('button') || ev.target.closest('input[type="checkbox"]'))) return;
  const body = header.nextElementSibling;
  const toggle = header.querySelector('.group-toggle');
  body.classList.toggle('collapsed');
  toggle.classList.toggle('collapsed');
}

async function renameGroup(ev, groupId, nameEl) {
  if (ev) {
    ev.stopPropagation();
    ev.preventDefault();
  }
  const current = nameEl.textContent.trim();
  const newName = prompt('修改分组名称：', current);
  if (!newName || !newName.trim() || newName.trim() === current) return;
  const trimmed = newName.trim();
  try {
    await api('PATCH', `/api/collections/groups/${groupId}`, { name: trimmed });
    nameEl.textContent = trimmed;
    toast('分组名称已更新', 'success');
  } catch (e) {
    toast(`更新失败：${e}`, 'error');
  }
}

function renameUngroupedLabel(ev, categoryId, nameEl) {
  if (ev) {
    ev.stopPropagation();
    ev.preventDefault();
  }
  const current = nameEl.textContent.trim();
  const newName = prompt('修改分组名称：', current);
  if (!newName || !newName.trim() || newName.trim() === current) return;
  const trimmed = newName.trim();
  try {
    const def = t('ungrouped');
    if (trimmed === def) {
      localStorage.removeItem(UNGROUPED_LABEL_STORAGE_PREFIX + categoryId);
      nameEl.textContent = def;
    } else {
      localStorage.setItem(UNGROUPED_LABEL_STORAGE_PREFIX + categoryId, trimmed);
      nameEl.textContent = trimmed;
    }
    toast('分组名称已更新', 'success');
  } catch (e) {
    toast(`更新失败：${e.message || e}`, 'error');
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
  try {
    const inv = await api('GET', `/api/invoices/${invoiceId}`);
    _currentDetailInvoice = inv;

    const fields = [
      ['发票类型', inv.invoice_type], ['发票号码', inv.invoice_number],
      ['开票日期', inv.issue_date], ['销售方', inv.seller_name],
      ['购买方', inv.buyer_name], ['金额', inv.amount != null ? `¥${inv.amount}` : null],
      ['税额', inv.tax_amount != null ? `¥${inv.tax_amount}` : null],
      ['价税合计', formatInvoiceAmountDisplay(inv) || null],
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
  } catch (e) {
    toast(`加载详情失败：${e.message || e}`, 'error');
  }
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
  const [cats, rules, travel, models, templates] = await Promise.all([
    api('GET', '/api/config/categories'),
    api('GET', '/api/config/rules'),
    api('GET', '/api/config/travel'),
    api('GET', '/api/config/models'),
    api('GET', '/api/config/nuextract-templates'),
  ]);

  state.categories = cats.categories;
  state.rules = rules.rules;
  state.travelConfig = travel;
  state.modelsConfig = models;
  const templateItems = Array.isArray(templates?.templates) ? templates.templates : [];
  state.nuExtractTemplates = templateItems.map(normalizeNuExtractTemplateItem);

  renderCategoryEditor(cats.categories);
  renderRuleEditor(rules.rules);
  renderTravelEditor(travel);
  renderModelsEditor(models);
  renderNuExtractTemplateEditor(state.nuExtractTemplates);
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

// ── Travel Editor ─────────────────────────────────────────────────────────────
function renderTravelEditor(travel) {
  document.getElementById('travelHomeCity').value = travel?.home_city || '上海';
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
  const llmFb = document.getElementById('optUseLlmOnFallback');
  if (llmFb) llmFb.checked = Boolean(models.extraction?.use_llm_on_fallback);
}

// ── NuExtract Templates Editor ────────────────────────────────────────────────
function createDefaultTemplateField() {
  return {
    key: '新字段',
    kind: 'primitive',
    primitiveType: 'string',
    children: [],
    enumValues: [],
  };
}

function isPlainObject(v) {
  return v && typeof v === 'object' && !Array.isArray(v);
}

function schemaToFieldTree(schema) {
  if (!isPlainObject(schema)) return [];
  return Object.entries(schema).map(([key, value]) => valueToFieldNode(key, value));
}

function valueToFieldNode(key, value) {
  if (isPlainObject(value)) {
    return {
      key,
      kind: 'object',
      primitiveType: 'string',
      children: schemaToFieldTree(value),
      enumValues: [],
    };
  }
  if (Array.isArray(value)) {
    if (value.length && isPlainObject(value[0])) {
      return {
        key,
        kind: 'array_object',
        primitiveType: 'string',
        children: schemaToFieldTree(value[0]),
        enumValues: [],
      };
    }
    return {
      key,
      kind: 'enum',
      primitiveType: 'string',
      children: [],
      enumValues: value.map(v => String(v)),
    };
  }
  return {
    key,
    kind: 'primitive',
    primitiveType: typeof value === 'string' ? value : 'string',
    children: [],
    enumValues: [],
  };
}

function fieldNodeToSchemaValue(node) {
  if (!node) return 'string';
  if (node.kind === 'object') return fieldTreeToSchema(node.children || []);
  if (node.kind === 'array_object') return [fieldTreeToSchema(node.children || [])];
  if (node.kind === 'enum') return (node.enumValues || []).map(v => String(v).trim()).filter(Boolean);
  return node.primitiveType || 'string';
}

function fieldTreeToSchema(nodes) {
  const out = {};
  (nodes || []).forEach(node => {
    const key = (node?.key || '').trim();
    if (!key) return;
    out[key] = fieldNodeToSchemaValue(node);
  });
  return out;
}

function validateFieldTree(nodes, labelPrefix = '字段') {
  const keySet = new Set();
  (nodes || []).forEach((node, idx) => {
    const key = (node?.key || '').trim();
    if (!key) throw new Error(`${labelPrefix}第 ${idx + 1} 项字段名不能为空`);
    if (keySet.has(key)) throw new Error(`${labelPrefix}中存在重复字段名「${key}」`);
    keySet.add(key);

    if (node.kind === 'object' || node.kind === 'array_object') {
      const children = node.children || [];
      if (!children.length) throw new Error(`字段「${key}」是嵌套类型，至少需要一个子字段`);
      validateFieldTree(children, `字段「${key}」的子字段`);
    }
    if (node.kind === 'enum') {
      const values = (node.enumValues || []).map(v => String(v || '').trim()).filter(Boolean);
      if (!values.length) throw new Error(`字段「${key}」的枚举值不能为空`);
      const unique = new Set(values);
      if (unique.size !== values.length) throw new Error(`字段「${key}」的枚举值存在重复`);
    }
    if (node.kind === 'primitive' && !(node.primitiveType || '').trim()) {
      throw new Error(`字段「${key}」缺少字段类型`);
    }
  });
}

function normalizeNuExtractTemplateItem(item) {
  const schema = item && typeof item === 'object' ? (item.schema ?? item.schema_definition ?? {}) : {};
  return {
    id: (item && item.id) ? item.id : `tpl_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    document_type: item?.document_type || item?.invoice_type || '',
    schema,
    schemaTree: schemaToFieldTree(schema),
  };
}

function getFieldNodesByPath(template, path, includeCurrent = false) {
  if (!template || !Array.isArray(template.schemaTree)) return null;
  if (!path) return template.schemaTree;
  const idxs = String(path).split('.').map(x => parseInt(x, 10)).filter(Number.isInteger);
  let nodes = template.schemaTree;
  for (let i = 0; i < idxs.length; i++) {
    const idx = idxs[i];
    if (!nodes[idx]) return null;
    if (i === idxs.length - 1) {
      return includeCurrent ? nodes[idx] : nodes[idx].children;
    }
    nodes = nodes[idx].children || [];
  }
  return null;
}

function renderFieldTypeOptions(current) {
  const options = [
    ['primitive:string', '文本(string)'],
    ['primitive:verbatim-string', '原文(verbatim-string)'],
    ['primitive:number', '数字(number)'],
    ['primitive:integer', '整数(integer)'],
    ['primitive:date-time', '日期时间(date-time)'],
    ['object', '对象(object)'],
    ['array_object', '对象数组(array<object>)'],
    ['enum', '枚举(enum)'],
  ];
  return options.map(([value, label]) => `<option value="${value}" ${current === value ? 'selected' : ''}>${escHtml(label)}</option>`).join('');
}

function nodeKindValue(node) {
  if (!node) return 'primitive:string';
  if (node.kind === 'primitive') return `primitive:${node.primitiveType || 'string'}`;
  return node.kind;
}

function renderFieldNodes(nodes, templateIdx, pathPrefix = '') {
  return (nodes || []).map((node, idx) => {
    const path = pathPrefix ? `${pathPrefix}.${idx}` : `${idx}`;
    const kindVal = nodeKindValue(node);
    const childHtml = (node.kind === 'object' || node.kind === 'array_object')
      ? `
      <div class="tpl-field-children">
        <div class="tpl-field-children-list">
          ${renderFieldNodes(node.children || [], templateIdx, path)}
        </div>
        <button type="button" class="btn btn-outline btn-sm tpl-btn"
                data-action="add-child-field" data-template-idx="${templateIdx}" data-path="${path}">
          + 添加子字段
        </button>
      </div>`
      : '';
    const enumHtml = node.kind === 'enum'
      ? `
      <div class="tpl-enum-editor">
        ${(node.enumValues || []).map((val, optIdx) => `
          <div class="tpl-enum-item">
            <input class="form-input tpl-enum-input" value="${escAttr(val)}"
                   data-action="edit-enum-value"
                   data-template-idx="${templateIdx}"
                   data-path="${path}"
                   data-option-idx="${optIdx}" />
            <button type="button" class="rule-delete-btn tpl-delete-btn"
                    data-action="delete-enum-value"
                    data-template-idx="${templateIdx}"
                    data-path="${path}"
                    data-option-idx="${optIdx}">×</button>
          </div>
        `).join('')}
        <button type="button" class="btn btn-outline btn-sm tpl-btn"
                data-action="add-enum-value" data-template-idx="${templateIdx}" data-path="${path}">
          + 添加枚举值
        </button>
      </div>`
      : '';
    return `
      <div class="tpl-field-node">
        <div class="tpl-field-row">
          <input class="form-input tpl-field-key" value="${escAttr(node.key || '')}"
                 placeholder="字段名称"
                 data-action="edit-field-key"
                 data-template-idx="${templateIdx}"
                 data-path="${path}" />
          <select class="form-select tpl-field-type"
                  data-action="edit-field-type"
                  data-template-idx="${templateIdx}"
                  data-path="${path}">
            ${renderFieldTypeOptions(kindVal)}
          </select>
          <button type="button" class="rule-delete-btn tpl-delete-btn"
                  data-action="delete-field"
                  data-template-idx="${templateIdx}"
                  data-path="${path}">×</button>
        </div>
        ${enumHtml}
        ${childHtml}
      </div>`;
  }).join('');
}

function renderNuExtractTemplateEditor(templates) {
  const container = document.getElementById('nuextractTemplateList');
  if (!container) return;
  const safeTemplates = Array.isArray(templates) ? templates : [];
  if (!safeTemplates.length) {
    container.innerHTML = `<div class="empty-state">暂无抽取模板，请先新增发票类型并配置对应的 Schema 模板。</div>`;
    return;
  }
  container.innerHTML = safeTemplates.map((t, i) => `
    <div class="rule-item" data-idx="${i}">
      <div class="rule-item-header">
        <span style="font-size:16px">📄</span>
        <input class="rule-name-input"
               value="${escAttr(t.document_type || '')}"
               data-action="edit-document-type"
               data-template-idx="${i}"
               placeholder="发票类型名称"
               style="flex:1" />
        <button class="rule-delete-btn" onclick="removeNuExtractTemplate(${i})">🗑️</button>
      </div>
      <div class="form-group" style="margin-top:8px">
        <label class="form-label">字段编辑器（支持嵌套）</label>
        <div class="tpl-editor-wrap">
          <div class="tpl-field-tree">
            ${renderFieldNodes(t.schemaTree || [], i)}
            <button type="button" class="btn btn-outline btn-sm tpl-btn"
                    data-action="add-root-field" data-template-idx="${i}">
              + 新增字段
            </button>
          </div>
        </div>
      </div>
    </div>`).join('');
}

function addNuExtractTemplate() {
  if (!Array.isArray(state.nuExtractTemplates)) state.nuExtractTemplates = [];
  state.nuExtractTemplates.push({
    id: `tpl_${Date.now()}`,
    document_type: '新文档类型',
    schema: {},
    schemaTree: [createDefaultTemplateField()],
  });
  renderNuExtractTemplateEditor(state.nuExtractTemplates);
}

function removeNuExtractTemplate(idx) {
  const tpl = state.nuExtractTemplates[idx];
  const tplName = tpl?.document_type || `第 ${idx + 1} 个模板`;
  if (!confirm(`确认删除发票类型「${tplName}」及其关联的抽取模板吗？此操作不可撤销。`)) return;
  state.nuExtractTemplates.splice(idx, 1);
  renderNuExtractTemplateEditor(state.nuExtractTemplates);
}

function collectNuExtractTemplates() {
  const typeNameSet = new Set();
  const templates = Array.isArray(state.nuExtractTemplates) ? state.nuExtractTemplates : [];
  if (!templates.length) {
    throw new Error('至少需要保留一个发票类型与对应抽取模板');
  }
  return templates.map((tpl, i) => {
    const document_type = (tpl.document_type || '').trim();
    if (!document_type) {
      throw new Error(`第 ${i + 1} 个模板的文档类型名称不能为空`);
    }
    if (typeNameSet.has(document_type)) {
      throw new Error(`文档类型「${document_type}」重复，请保持唯一`);
    }
    typeNameSet.add(document_type);
    const fieldTree = tpl.schemaTree || [];
    validateFieldTree(fieldTree, `模板「${document_type}」`);
    const schema = fieldTreeToSchema(fieldTree);
    if (!schema || typeof schema !== 'object' || Array.isArray(schema)) {
      throw new Error(`第 ${i + 1} 个模板 (${document_type}) 的 Schema 必须是 JSON 对象`);
    }
    if (!Object.keys(schema).length) {
      throw new Error(`第 ${i + 1} 个模板 (${document_type}) 至少需要一个字段`);
    }
    // 复用原有的 ID 或生成新 ID
    const id = (tpl && tpl.id) ? tpl.id : `tpl_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    return { id, document_type, schema };
  });
}

function handleTemplateEditorClick(event) {
  const el = event.target.closest('[data-action]');
  if (!el) return;
  const action = el.dataset.action;
  const templateIdx = parseInt(el.dataset.templateIdx, 10);
  if (!Number.isInteger(templateIdx) || !state.nuExtractTemplates[templateIdx]) return;
  const template = state.nuExtractTemplates[templateIdx];
  const path = el.dataset.path || '';

  if (action === 'add-root-field') {
    template.schemaTree.push(createDefaultTemplateField());
    renderNuExtractTemplateEditor(state.nuExtractTemplates);
    return;
  }
  if (action === 'add-child-field') {
    const parent = getFieldNodesByPath(template, path, true);
    if (!parent) return;
    if (!Array.isArray(parent.children)) parent.children = [];
    parent.children.push(createDefaultTemplateField());
    renderNuExtractTemplateEditor(state.nuExtractTemplates);
    return;
  }
  if (action === 'delete-field') {
    const idxs = String(path).split('.').map(x => parseInt(x, 10)).filter(Number.isInteger);
    if (!idxs.length) return;
    const last = idxs[idxs.length - 1];
    const parentPath = idxs.slice(0, -1).join('.');
    const siblings = parentPath ? getFieldNodesByPath(template, parentPath) : template.schemaTree;
    if (!Array.isArray(siblings) || !siblings[last]) return;
    siblings.splice(last, 1);
    renderNuExtractTemplateEditor(state.nuExtractTemplates);
    return;
  }
  if (action === 'add-enum-value') {
    const node = getFieldNodesByPath(template, path, true);
    if (!node) return;
    if (!Array.isArray(node.enumValues)) node.enumValues = [];
    node.enumValues.push('新枚举值');
    renderNuExtractTemplateEditor(state.nuExtractTemplates);
    return;
  }
  if (action === 'delete-enum-value') {
    const node = getFieldNodesByPath(template, path, true);
    const optIdx = parseInt(el.dataset.optionIdx, 10);
    if (!node || !Array.isArray(node.enumValues) || !Number.isInteger(optIdx) || !node.enumValues[optIdx]) return;
    node.enumValues.splice(optIdx, 1);
    renderNuExtractTemplateEditor(state.nuExtractTemplates);
  }
}

function handleTemplateEditorChange(event) {
  const el = event.target.closest('[data-action]');
  if (!el) return;
  const action = el.dataset.action;
  const templateIdx = parseInt(el.dataset.templateIdx, 10);
  if (!Number.isInteger(templateIdx) || !state.nuExtractTemplates[templateIdx]) return;
  const template = state.nuExtractTemplates[templateIdx];
  const path = el.dataset.path || '';

  if (action === 'edit-document-type') {
    template.document_type = String(el.value || '').trim();
    return;
  }

  const node = getFieldNodesByPath(template, path, true);
  if (!node) return;
  if (action === 'edit-field-key') {
    node.key = String(el.value || '').trim();
    renderNuExtractTemplateEditor(state.nuExtractTemplates);
    return;
  }
  if (action === 'edit-field-type') {
    const value = String(el.value || '');
    if (value.startsWith('primitive:')) {
      node.kind = 'primitive';
      node.primitiveType = value.split(':')[1] || 'string';
      node.children = [];
      node.enumValues = [];
    } else if (value === 'object') {
      node.kind = 'object';
      if (!Array.isArray(node.children)) node.children = [];
      node.enumValues = [];
    } else if (value === 'array_object') {
      node.kind = 'array_object';
      if (!Array.isArray(node.children)) node.children = [];
      node.enumValues = [];
    } else if (value === 'enum') {
      node.kind = 'enum';
      node.children = [];
      if (!Array.isArray(node.enumValues) || !node.enumValues.length) node.enumValues = ['枚举值1'];
    }
    renderNuExtractTemplateEditor(state.nuExtractTemplates);
    return;
  }
  if (action === 'edit-enum-value') {
    const optIdx = parseInt(el.dataset.optionIdx, 10);
    if (!Array.isArray(node.enumValues) || !Number.isInteger(optIdx)) return;
    node.enumValues[optIdx] = String(el.value || '').trim();
    renderNuExtractTemplateEditor(state.nuExtractTemplates);
  }
}

function normalizeExtractionConfig(extraction) {
  const src = extraction || {};
  const ocr = src.ocr || {};
  return {
    provider: src.provider || 'auto',
    use_llm_on_fallback: Boolean(src.use_llm_on_fallback),
    ocr: {
      engine: ocr.engine || 'rapidocr_onnx',
      pdf_max_pages: parseInt(ocr.pdf_max_pages) || 3,
      min_text_chars: parseInt(ocr.min_text_chars) || 80,
    },
  };
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
    } else if (activeTab === 'tabTravel') {
      const homeCity = document.getElementById('travelHomeCity').value.trim() || '上海';
      await api('PUT', '/api/config/travel', { home_city: homeCity });
      state.travelConfig = { home_city: homeCity };
      toast('差旅设置已保存', 'success');
    } else if (activeTab === 'tabModels') {
      const extPrev = { ...(state.modelsConfig?.extraction || {}) };
      extPrev.use_llm_on_fallback = Boolean(
        document.getElementById('optUseLlmOnFallback')?.checked,
      );
      const models = {
        llm: {
          base_url: document.getElementById('llmBaseUrl').value,
          api_key: document.getElementById('llmApiKey').value,
          model: document.getElementById('llmModel').value,
          timeout: parseInt(document.getElementById('llmTimeout').value),
        },
        nuextract: {
          host: document.getElementById('nuHost').value,
          port: document.getElementById('nuPort').value,
          timeout: parseInt(document.getElementById('nuTimeout').value),
        },
        extraction: normalizeExtractionConfig(extPrev),
      };
      await api('PUT', '/api/config/models', models);
      state.modelsConfig = models;
      toast('模型服务配置已保存并生效', 'success');
    } else if (activeTab === 'tabNuExtractTemplates') {
      try {
        const templates = collectNuExtractTemplates();
        await api('PUT', '/api/config/nuextract-templates', { templates });
        state.nuExtractTemplates = templates.map(normalizeNuExtractTemplateItem);
        renderNuExtractTemplateEditor(state.nuExtractTemplates);
        toast('抽取模板配置已保存并生效', 'success');
      } catch (err) {
        toast(err.message, 'warning');
      }
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
  document.getElementById('invoiceStatusLegend')?.setAttribute('aria-label', t('invoiceStatusLegend'));

  updateTotalBadge();
  refreshSelectionUI();
  renderInvoiceList();
  renderBoard();
  renderArchivePanel();
  syncArchiveToggleLabels();
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
    bindConfigModalEvents();
    loadArchiveFromStorage();
    applyLanguage();
    setupArchivePanelOnce();
    applyArchivePanelCollapsedState();
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
