let currentReportData = null;
let currentFilter = 'all';
let acceptedBlockRevisions = new Set(); // Stores block_ids whose revisions are accepted
let chatHistory = [];
let activeContextBlock = null;

const SUPPORTED_EXTENSIONS = ['.pdf', '.docx', '.doc', '.txt', '.md', '.rtf', '.odt'];

document.addEventListener('DOMContentLoaded', () => {
  checkSystemStatus();
  setupDragAndDrop();
  loadGuidelines();
  loadHistory();

  // Close export dropdown when clicking outside
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.dropdown-export')) {
      const drop = document.getElementById('export-dropdown-content');
      if (drop) drop.classList.remove('show');
    }
  });
});

// Check API system status
async function checkSystemStatus() {
  const statusText = document.getElementById('status-text');
  if (!statusText) return; // Guard clause untuk mencegah error
  
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    if (data.openrouter_configured) {
      statusText.textContent = `AI Ready | ${data.indexed_chunks} Chunks`;
    } else {
      statusText.textContent = `API Key belum diisi`;
    }
  } catch (err) {
    statusText.textContent = 'Server Disconnected';
  }
}

// Navigation Tab Switcher
function switchTab(tabId) {
  document.querySelectorAll('.tab-panel').forEach(panel => panel.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));

  document.getElementById(tabId).classList.add('active');
  const btnIdx = tabId === 'tab-check' ? 0 : tabId === 'tab-pedoman' ? 1 : 2;
  document.querySelectorAll('.nav-btn')[btnIdx].classList.add('active');

  if (tabId === 'tab-pedoman') loadGuidelines();
  if (tabId === 'tab-history') loadHistory();
}

function isFileSupported(filename) {
  const lower = filename.toLowerCase();
  return SUPPORTED_EXTENSIONS.some(ext => lower.endsWith(ext));
}

function getFileIcon(filename) {
  const lower = filename.toLowerCase();
  if (lower.endsWith('.docx') || lower.endsWith('.doc')) {
    return '<i class="fa-solid fa-file-word" style="color: #2563eb;"></i>';
  } else if (lower.endsWith('.pdf')) {
    return '<i class="fa-solid fa-file-pdf" style="color: #dc2626;"></i>';
  } else if (lower.endsWith('.txt') || lower.endsWith('.md')) {
    return '<i class="fa-solid fa-file-lines" style="color: #475569;"></i>';
  } else if (lower.endsWith('.rtf') || lower.endsWith('.odt')) {
    return '<i class="fa-solid fa-file-signature" style="color: #d97706;"></i>';
  }
  return '<i class="fa-solid fa-file" style="color: #64748b;"></i>';
}

function setupDragAndDrop() {
  const dropZone = document.getElementById('drop-zone');
  if (!dropZone) return;

  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, preventDefaults, false);
  });

  function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
  }

  ['dragenter', 'dragover'].forEach(eventName => {
    dropZone.addEventListener(eventName, () => dropZone.classList.add('dragover'), false);
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, () => dropZone.classList.remove('dragover'), false);
  });

  dropZone.addEventListener('drop', e => {
    const dt = e.dataTransfer;
    const files = dt.files;
    if (files.length > 0) {
      processDocFile(files[0]);
    }
  });
}

function handleFileSelect(event) {
  const files = event.target.files;
  if (files.length > 0) {
    processDocFile(files[0]);
  }
}

// Process Document Upload (DOCX, PDF, TXT, etc.) & Run Check
async function processDocFile(file) {
  if (!isFileSupported(file.name)) {
    alert(`Format file "${file.name}" tidak didukung.\nGunakan format: DOCX, DOC, PDF, TXT, RTF, atau MD.`);
    return;
  }

  const dropZone = document.getElementById('drop-zone');
  const loadingContainer = document.getElementById('loading-container');
  const resultsWrapper = document.getElementById('results-wrapper');
  const statsSummary = document.getElementById('stats-summary');
  const scoreBanner = document.getElementById('score-banner');

  dropZone.style.display = 'none';
  loadingContainer.style.display = 'block';
  resultsWrapper.style.display = 'none';
  if (scoreBanner) scoreBanner.style.display = 'none';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const response = await fetch('/api/check', {
      method: 'POST',
      body: formData
    });

    if (!response.ok) {
      const errData = await response.json();
      throw new Error(errData.detail || 'Gagal memproses pemeriksaan dokumen.');
    }

    const report = await response.json();
    currentReportData = report;

    // Reset accepted set to include all revision blocks by default
    acceptedBlockRevisions.clear();
    report.blocks.forEach(b => {
      if (b.suggested_revision) acceptedBlockRevisions.add(b.block_id);
    });

    loadingContainer.style.display = 'none';
    dropZone.style.display = 'block';
    statsSummary.style.display = 'grid';
    if (scoreBanner) scoreBanner.style.display = 'flex';
    resultsWrapper.style.display = 'block';

    renderReport(report);
    checkSystemStatus();

  } catch (err) {
    loadingContainer.style.display = 'none';
    dropZone.style.display = 'block';
    alert(`Error: ${err.message}`);
  }
}

// Render Check Report
function renderReport(report) {
  // Update stats dengan guard clauses
  const statTotal = document.getElementById('stat-total');
  const statSesuai = document.getElementById('stat-sesuai');
  const statRevisi = document.getElementById('stat-revisi');
  const statEjaan = document.getElementById('stat-ejaan');
  const statTidak = document.getElementById('stat-tidak');
  const statSpanTotal = document.getElementById('stat-span-total');
  const statErrTandaBaca = document.getElementById('stat-err-tanda-baca');
  const statErrKosaKata = document.getElementById('stat-err-kosa-kata');
  const statLongSentences = document.getElementById('stat-long-sentences');
  
  if (statTotal) statTotal.textContent = report.summary.total_blocks || 0;
  if (statSesuai) statSesuai.textContent = report.summary.sesuai || 0;
  if (statRevisi) statRevisi.textContent = report.summary.perlu_revisi || 0;
  if (statEjaan) statEjaan.textContent = report.summary.ejaan_tanda_baca || 0;
  if (statTidak) statTidak.textContent = report.summary.tidak_ditemukan_rujukan || 0;

  const totalSpanErrors = report.summary.total_span_errors || 0;
  if (statSpanTotal) statSpanTotal.textContent = totalSpanErrors;
  if (statErrTandaBaca) statErrTandaBaca.textContent = report.summary.errors_tanda_baca || 0;
  if (statErrKosaKata) statErrKosaKata.textContent = report.summary.errors_kosa_kata || 0;
  if (statLongSentences && report.legal_metrics) {
    statLongSentences.textContent = report.legal_metrics.long_sentences_count || 0;
  }

  // Update Executive Score Banner
  if (report.compliance_score) {
    const score = report.compliance_score;
    const circleVal = document.getElementById('score-circle-value');
    const gradeBadge = document.getElementById('score-grade-badge');
    const predText = document.getElementById('score-predicate-text');

    if (circleVal) circleVal.textContent = Math.round(score.overall_score);
    if (gradeBadge) gradeBadge.textContent = score.grade;
    if (predText) predText.textContent = `Grade ${score.grade} - ${score.predicate}`;

    if (score.sub_scores) {
      if (document.getElementById('sub-score-pedoman')) {
        document.getElementById('sub-score-pedoman').textContent = `${Math.round(score.sub_scores.pedoman_score || 100)}%`;
      }
      if (document.getElementById('sub-score-ejaan')) {
        document.getElementById('sub-score-ejaan').textContent = `${Math.round(score.sub_scores.ejaan_score || 100)}%`;
      }
      if (document.getElementById('sub-score-punct')) {
        document.getElementById('sub-score-punct').textContent = `${Math.round(score.sub_scores.tanda_baca_kosa_kata_score || 100)}%`;
      }
      if (document.getElementById('sub-score-struct')) {
        document.getElementById('sub-score-struct').textContent = `${Math.round(score.sub_scores.struktur_hukum_score || 100)}%`;
      }
    }
  }

  if (document.getElementById('doc-title-badge')) {
    document.getElementById('doc-title-badge').textContent = report.document_checked || 'dokumen';
  }

  const issueCount = (report.summary.perlu_revisi || 0) + (report.summary.ejaan_tanda_baca || 0);
  if (document.getElementById('issue-count-badge')) {
    document.getElementById('issue-count-badge').textContent = `${issueCount} Blok Masalah | ${totalSpanErrors} Kesalahan`;
  }

  renderTurnitinDocument(report.blocks, currentFilter);
  renderBlocks(report.blocks, currentFilter);
}

/**
 * Apply inline highlight marks using exact string positions.
 */
function applySpanHighlights(originalText, spanErrors, blockId, issueCounter) {
  if (!spanErrors || spanErrors.length === 0) {
    return escapeHTML(originalText);
  }

  const matches = [];
  spanErrors.forEach((err, idx) => {
    const snippet = err.original_snippet;
    if (!snippet) return;

    let searchFrom = 0;
    while (searchFrom < originalText.length) {
      const pos = originalText.indexOf(snippet, searchFrom);
      if (pos === -1) break;
      matches.push({
        start: pos,
        end: pos + snippet.length,
        error: err,
        idx: idx,
      });
      break;
    }
  });

  if (matches.length === 0) {
    return escapeHTML(originalText);
  }

  matches.sort((a, b) => a.start - b.start);

  const nonOverlapping = [];
  let lastEnd = -1;
  for (const m of matches) {
    if (m.start >= lastEnd) {
      nonOverlapping.push(m);
      lastEnd = m.end;
    }
  }

  let result = '';
  let cursor = 0;

  for (const m of nonOverlapping) {
    if (m.start > cursor) {
      result += escapeHTML(originalText.substring(cursor, m.start));
    }

    let hlClass = 'hl-ejaan';
    if (m.error.error_type === 'pedoman') hlClass = 'hl-pedoman';
    else if (m.error.error_type === 'tanda_baca') hlClass = 'hl-tanda-baca';
    else if (m.error.error_type === 'kosa_kata') hlClass = 'hl-kosa-kata';

    const tooltipText = escapeHTML(m.error.explanation || `Kesalahan ${m.error.error_type}`);
    const snippetHTML = escapeHTML(originalText.substring(m.start, m.end));

    result += `<mark class="inline-hl ${hlClass}" id="hl-${blockId}-${m.idx}" onclick="scrollToCard('${blockId}')" title="${tooltipText}">${snippetHTML}<span class="hl-badge">${issueCounter}</span></mark>`;

    cursor = m.end;
  }

  if (cursor < originalText.length) {
    result += escapeHTML(originalText.substring(cursor));
  }

  return result;
}

// Render Turnitin Left Document Reader with Inline Highlights
function renderTurnitinDocument(blocks, filter) {
  const docView = document.getElementById('document-reader-view');
  if (!docView) return;
  docView.innerHTML = '';

  if (!blocks || blocks.length === 0) {
    docView.innerHTML = '<div class="empty-state"><i class="fa-solid fa-file-circle-question empty-state-icon"></i><p>Tidak ada isi dokumen.</p></div>';
    return;
  }

  const pagesMap = {};
  blocks.forEach(b => {
    const pageNum = b.page_number || 1;
    if (!pagesMap[pageNum]) pagesMap[pageNum] = [];
    pagesMap[pageNum].push(b);
  });

  let issueCounter = 0;

  Object.keys(pagesMap).sort((a, b) => a - b).forEach(pageNum => {
    const pageWrapper = document.createElement('div');
    pageWrapper.className = 'doc-page-wrapper';

    const pageMarker = document.createElement('div');
    pageMarker.className = 'page-marker';
    pageMarker.innerHTML = `<i class="fa-regular fa-file"></i> Halaman ${pageNum}`;
    pageWrapper.appendChild(pageMarker);

    pagesMap[pageNum].forEach(b => {
      const p = document.createElement('div');
      p.className = 'doc-paragraph';

      const isFilteredOut = (filter !== 'all' && b.status !== filter);
      let textContent;

      if (b.status !== 'sesuai' && b.status !== 'tidak_ditemukan_rujukan' && !isFilteredOut) {
        issueCounter++;

        if (b.span_errors && b.span_errors.length > 0) {
          textContent = applySpanHighlights(b.original_text, b.span_errors, b.block_id, issueCounter);
        } else {
          let hlClass = 'hl-pedoman';
          if (b.status === 'ejaan_tanda_baca') hlClass = 'hl-ejaan';
          textContent = `<mark class="inline-hl ${hlClass}" id="hl-${b.block_id}" onclick="scrollToCard('${b.block_id}')">${escapeHTML(b.original_text)}<span class="hl-badge">${issueCounter}</span></mark>`;
        }
      } else {
        textContent = escapeHTML(b.original_text);
      }

      p.innerHTML = textContent;
      pageWrapper.appendChild(p);
    });

    docView.appendChild(pageWrapper);
  });
}

function getErrorTypeLabel(errorType) {
  const labels = {
    'pedoman': 'Pedoman/UU',
    'ejaan': 'Ejaan',
    'tanda_baca': 'Tanda Baca',
    'kosa_kata': 'Kosa Kata',
  };
  return labels[errorType] || errorType;
}

function getSeverityLabel(severity) {
  const labels = {
    'high': 'Tinggi',
    'medium': 'Sedang',
    'low': 'Rendah',
  };
  return labels[severity] || 'Sedang';
}

// Render Block Cards on Right Sidebar with Interactive Accept/Reject
function renderBlocks(blocks, filter) {
  const container = document.getElementById('blocks-container');
  if (!container) return;
  container.innerHTML = '';

  const filteredBlocks = blocks.filter(b => filter === 'all' || b.status === filter);

  if (filteredBlocks.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <i class="fa-solid fa-magnifying-glass empty-state-icon"></i>
        <p>Tidak ada umpan balik untuk filter ini.</p>
      </div>
    `;
    return;
  }

  filteredBlocks.forEach(b => {
    const card = document.createElement('div');
    const isAccepted = acceptedBlockRevisions.has(b.block_id);
    card.className = `block-card ${isAccepted ? 'revision-accepted' : 'revision-rejected'}`;
    card.id = `card-${b.block_id}`;
    card.onclick = () => scrollToHighlight(b.block_id);

    let statusLabel = '<i class="fa-solid fa-circle-check"></i> Sesuai';
    if (b.status === 'perlu_revisi') statusLabel = '<i class="fa-solid fa-triangle-exclamation"></i> Perlu Revisi';
    if (b.status === 'ejaan_tanda_baca') statusLabel = '<i class="fa-solid fa-spell-check"></i> Ejaan & Tanda Baca';
    if (b.status === 'tidak_ditemukan_rujukan') statusLabel = '<i class="fa-solid fa-circle-question"></i> Tanpa Rujukan';

    let issueHTML = '';
    if (b.issue) {
      issueHTML = `<div class="issue-box"><i class="fa-solid fa-circle-exclamation"></i> <strong>Detail Masalah:</strong> ${escapeHTML(b.issue)}</div>`;
    }

    let ruleHTML = '';
    if (b.rule_reference) {
      const secText = b.rule_reference.section ? ` | ${b.rule_reference.section}` : '';
      ruleHTML = `
        <div class="rule-reference-box">
          <i class="fa-solid fa-book-open"></i> <strong>Rujukan Pedoman:</strong> ${escapeHTML(b.rule_reference.document)} (Halaman ${b.rule_reference.page}${escapeHTML(secText)})
        </div>
      `;
    }

    let spanErrorsHTML = '';
    if (b.span_errors && b.span_errors.length > 0) {
      const grouped = {};
      b.span_errors.forEach(err => {
        const type = err.error_type || 'ejaan';
        if (!grouped[type]) grouped[type] = [];
        grouped[type].push(err);
      });

      spanErrorsHTML = '<div class="span-errors-container">';

      for (const [errorType, errors] of Object.entries(grouped)) {
        const typeLabel = getErrorTypeLabel(errorType);
        spanErrorsHTML += `<div class="span-error-group-header">${typeLabel} (${errors.length})</div>`;

        errors.forEach(err => {
          const tagClass = `tag-${err.error_type || 'ejaan'}`;
          const sevClass = err.severity ? `sev-tag-${err.severity}` : '';
          const sevLabel = getSeverityLabel(err.severity);

          spanErrorsHTML += `
            <div class="span-error-box">
              <div class="span-error-tags">
                <span class="span-error-tag ${tagClass}">${escapeHTML(getErrorTypeLabel(err.error_type))}</span>
                <span class="span-error-severity ${sevClass}">${sevLabel}</span>
              </div>
              <div class="span-error-diff">
                <s class="span-error-original">"${escapeHTML(err.original_snippet)}"</s>
                <span class="span-error-arrow"><i class="fa-solid fa-arrow-right-long"></i></span>
                <strong class="span-error-suggested">"${escapeHTML(err.suggested_snippet)}"</strong>
              </div>
              <div class="span-error-explanation">${escapeHTML(err.explanation)}</div>
            </div>
          `;
        });
      }

      spanErrorsHTML += '</div>';
    }

    let revisionHTML = '';
    if (b.suggested_revision) {
      revisionHTML = `
        <div class="suggested-box">
          <i class="fa-solid fa-lightbulb"></i> <strong>Saran Perbaikan Usulan:</strong> ${escapeHTML(b.suggested_revision)}
        </div>
      `;
    }

    // Action bar with Accept / Reject buttons & Ask AI button
    let actionBarHTML = '';
    if (b.suggested_revision || (b.span_errors && b.span_errors.length > 0)) {
      actionBarHTML = `
        <div class="card-action-bar">
          <div class="action-btn-group">
            <button class="btn-action-accept ${isAccepted ? 'active' : ''}" onclick="toggleAcceptBlock('${b.block_id}', true, event)">
              <i class="fa-solid fa-check"></i> Terima Revisi
            </button>
            <button class="btn-action-reject ${!isAccepted ? 'active' : ''}" onclick="toggleAcceptBlock('${b.block_id}', false, event)">
              <i class="fa-solid fa-xmark"></i> Tolak
            </button>
          </div>
          <button class="btn-ask-ai" onclick="openAssistantForBlock('${b.block_id}', event)">
            <i class="fa-solid fa-comment-dots"></i> Tanya AI
          </button>
        </div>
      `;
    }

    const errorCount = (b.span_errors && b.span_errors.length) || 0;
    const errorCountBadge = errorCount > 0
      ? `<span class="block-error-count"><i class="fa-solid fa-circle-dot"></i> ${errorCount} kesalahan</span>`
      : '';

    card.innerHTML = `
      <div class="block-header">
        <span class="block-id">${escapeHTML(b.block_id)}</span>
        <div class="block-header-right">
          ${errorCountBadge}
          <span class="status-badge ${b.status}">${statusLabel}</span>
        </div>
      </div>
      <div class="original-text-box">${escapeHTML(b.original_text)}</div>
      ${issueHTML}
      ${spanErrorsHTML}
      ${ruleHTML}
      ${revisionHTML}
      ${actionBarHTML}
    `;

    container.appendChild(card);
  });
}

function toggleAcceptBlock(blockId, accept, event) {
  if (event) event.stopPropagation();
  if (accept) {
    acceptedBlockRevisions.add(blockId);
  } else {
    acceptedBlockRevisions.delete(blockId);
  }
  if (currentReportData) {
    renderBlocks(currentReportData.blocks, currentFilter);
  }
}

function acceptAllRevisions() {
  if (!currentReportData) return;
  currentReportData.blocks.forEach(b => {
    if (b.suggested_revision) acceptedBlockRevisions.add(b.block_id);
  });
  renderBlocks(currentReportData.blocks, currentFilter);
}

function rejectAllRevisions() {
  acceptedBlockRevisions.clear();
  if (currentReportData) {
    renderBlocks(currentReportData.blocks, currentFilter);
  }
}

// Scroll & Sync Functions between Document & Sidebar
function scrollToCard(blockId) {
  const card = document.getElementById(`card-${blockId}`);
  if (card) {
    document.querySelectorAll('.block-card').forEach(c => c.classList.remove('active-card'));
    card.classList.add('active-card');
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

function scrollToHighlight(blockId) {
  const hl = document.getElementById(`hl-${blockId}`) || document.getElementById(`hl-${blockId}-0`);
  if (hl) {
    document.querySelectorAll('.inline-hl').forEach(h => h.classList.remove('active-highlight'));
    hl.classList.add('active-highlight');
    hl.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  scrollToCard(blockId);
}

// Filter Blocks & Turnitin View
function filterResults(filterType) {
  currentFilter = filterType;

  document.querySelectorAll('.pill-btn').forEach(btn => btn.classList.remove('active'));
  const activePill =
    filterType === 'all' ? 'filter-all' :
    filterType === 'perlu_revisi' ? 'filter-revisi' :
    filterType === 'ejaan_tanda_baca' ? 'filter-ejaan' :
    filterType === 'sesuai' ? 'filter-sesuai' : 'filter-tidak';

  if (document.getElementById(activePill)) {
    document.getElementById(activePill).classList.add('active');
  }

  if (currentReportData) {
    renderTurnitinDocument(currentReportData.blocks, currentFilter);
    renderBlocks(currentReportData.blocks, currentFilter);
  }
}

// Toggle Export Dropdown
function toggleExportMenu() {
  const menu = document.getElementById('export-dropdown-content');
  if (menu) menu.classList.toggle('show');
}

// Download Export in various formats
async function downloadExport(mode) {
  if (!currentReportData) return;
  toggleExportMenu();

  try {
    const payload = {
      report: currentReportData,
      mode: mode,
      accepted_block_ids: Array.from(acceptedBlockRevisions),
    };

    const res = await fetch('/api/export-docx', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!res.ok) throw new Error('Gagal mengekspor file DOCX.');

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const baseName = (currentReportData.document_checked || 'dokumen').replace(/\.[^/.]+$/, '');
    a.download = mode === 'track_changes'
      ? `${baseName}_track_changes.docx`
      : mode === 'audit_report'
      ? `${baseName}_laporan_audit.docx`
      : `${baseName}_naskah_bersih.docx`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(`Export Error: ${err.message}`);
  }
}

function exportJSONReport() {
  if (!currentReportData) return;
  toggleExportMenu();
  const jsonStr = JSON.stringify(currentReportData, null, 2);
  const blob = new Blob([jsonStr], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `laporan_${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

// ============================================
// Assistant Drawer Functions & Rich Markdown
// ============================================

function toggleAssistantDrawer() {
  const drawer = document.getElementById('assistant-drawer');
  if (drawer) drawer.classList.toggle('open');
}

function toggleDrawerWide() {
  const drawer = document.getElementById('assistant-drawer');
  const btn = document.getElementById('btn-toggle-wide');
  if (!drawer) return;

  drawer.classList.toggle('wide');
  if (btn) {
    if (drawer.classList.contains('wide')) {
      btn.innerHTML = '<i class="fa-solid fa-down-left-and-up-right-to-center"></i>';
      btn.title = 'Perkecil Panel Chat';
    } else {
      btn.innerHTML = '<i class="fa-solid fa-up-right-and-down-left-from-center"></i>';
      btn.title = 'Perlebar Panel Chat';
    }
  }
}

function clearChatHistory() {
  if (!confirm('Bersihkan seluruh percakapan dengan asisten AI?')) return;
  chatHistory = [];
  activeContextBlock = null;
  const messagesContainer = document.getElementById('chat-messages');
  if (messagesContainer) {
    messagesContainer.innerHTML = `
      <div class="chat-bubble ai">
        <div class="chat-welcome-card">
          <div class="welcome-header">
            <i class="fa-solid fa-scale-balanced"></i>
            <strong>Selamat datang di Asisten Legal Drafter &amp; Kebahasaan</strong>
          </div>
          <p>Saya siap membantu meninjau naskah, memformulasikan pasal hukum, menguji kepatuhan UU 12/2011 &amp; UU 13/2022, serta menyempurnakan kaidah PUEBI/EYD &amp; KBBI.</p>
        </div>
      </div>
    `;
  }
}

function applyQuickPrompt(promptText) {
  const input = document.getElementById('chat-input');
  if (!input) return;
  input.value = promptText;
  sendChatMessage();
}

function openAssistantForBlock(blockId, event) {
  if (event) event.stopPropagation();
  const block = currentReportData?.blocks.find(b => b.block_id === blockId);
  activeContextBlock = block;
  
  const drawer = document.getElementById('assistant-drawer');
  if (drawer && !drawer.classList.contains('open')) {
    drawer.classList.add('open');
  }

  const chatMessages = document.getElementById('chat-messages');
  if (chatMessages && block) {
    const promptNotice = document.createElement('div');
    promptNotice.className = 'chat-bubble ai';
    promptNotice.innerHTML = `
      <div style="background: var(--yellow-soft); border: 1px solid var(--yellow-border); padding: 0.65rem 0.85rem; border-radius: var(--radius-sm); margin-bottom: 0.5rem;">
        <strong style="color: #92400e;"><i class="fa-solid fa-bullseye"></i> Fokus Konteks: Blok ${escapeHTML(blockId)} (Halaman ${block.page_number})</strong>
        <div style="font-size: 0.8rem; color: #78350f; margin-top: 0.3rem; font-style: italic;">
          "${escapeHTML(block.original_text.substring(0, 180))}${block.original_text.length > 180 ? '...' : ''}"
        </div>
      </div>
      <p style="margin: 0; font-size: 0.835rem;">Silakan tanyakan perbaikan formulasi pasal, uji kepatuhan, atau koreksi EYD/KBBI untuk blok ini.</p>
    `;
    chatMessages.appendChild(promptNotice);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }
}

function handleChatKey(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendChatMessage();
  }
}

/**
 * Format markdown text into beautiful HTML with tables, headings, code blocks, lists, and callouts.
 */
function renderMarkdownToHTML(markdownText) {
  if (!markdownText) return '';

  // 1. Try marked.js if available
  if (typeof marked !== 'undefined' && typeof marked.parse === 'function') {
    try {
      marked.setOptions({
        gfm: true,
        breaks: true,
        headerIds: false,
        mangle: false,
      });
      let html = marked.parse(markdownText);

      // Wrap tables in responsive container
      html = html.replace(/<table>([\s\S]*?)<\/table>/gi, '<div class="chat-table-wrapper"><table>$1</table></div>');

      // Sanitize if DOMPurify is available
      if (typeof DOMPurify !== 'undefined' && typeof DOMPurify.sanitize === 'function') {
        html = DOMPurify.sanitize(html, {
          ADD_ATTR: ['target', 'onclick', 'class'],
          ADD_TAGS: ['mark']
        });
      }
      return html;
    } catch (e) {
      console.warn('Marked parsing error, falling back to regex parser:', e);
    }
  }

  // 2. Fallback built-in regex markdown parser
  let out = escapeHTML(markdownText);

  // Headers
  out = out.replace(/^### (.*$)/gim, '<h3>$1</h3>');
  out = out.replace(/^## (.*$)/gim, '<h2>$1</h2>');
  out = out.replace(/^# (.*$)/gim, '<h1>$1</h1>');

  // Bold & Italic
  out = out.replace(/\*\*\*(.*?)\*\*\*/g, '<strong><em>$1</em></strong>');
  out = out.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/\*(.*?)\*/g, '<em>$1</em>');

  // Horizontal rules
  out = out.replace(/^---$/gim, '<hr>');

  // Blockquotes
  out = out.replace(/^> (.*$)/gim, '<blockquote>$1</blockquote>');

  // Tables fallback: convert simple markdown tables
  const lines = out.split('\n');
  let inTable = false;
  let tableHTML = '';
  let processedLines = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (line.startsWith('|') && line.endsWith('|')) {
      // Check if it's separator row
      if (/^\|[-:\s|]+\|$/.test(line)) {
        continue;
      }
      const cells = line.split('|').slice(1, -1).map(c => c.trim());
      if (!inTable) {
        inTable = true;
        tableHTML = '<div class="chat-table-wrapper"><table><thead><tr>';
        cells.forEach(c => { tableHTML += `<th>${c}</th>`; });
        tableHTML += '</tr></thead><tbody>';
      } else {
        tableHTML += '<tr>';
        cells.forEach(c => { tableHTML += `<td>${c}</td>`; });
        tableHTML += '</tr>';
      }
    } else {
      if (inTable) {
        tableHTML += '</tbody></table></div>';
        processedLines.push(tableHTML);
        tableHTML = '';
        inTable = false;
      }
      processedLines.push(lines[i]);
    }
  }
  if (inTable) {
    tableHTML += '</tbody></table></div>';
    processedLines.push(tableHTML);
  }

  out = processedLines.join('<br>');
  out = out.replace(/<br><br>/g, '<br>');
  return out;
}

async function sendChatMessage() {
  const input = document.getElementById('chat-input');
  const messagesContainer = document.getElementById('chat-messages');
  const message = input.value.trim();
  if (!message) return;

  // Append User message
  const userBubble = document.createElement('div');
  userBubble.className = 'chat-bubble user';
  userBubble.textContent = message;
  messagesContainer.appendChild(userBubble);
  input.value = '';
  messagesContainer.scrollTop = messagesContainer.scrollHeight;

  // Append Loading bubble
  const loadingBubble = document.createElement('div');
  loadingBubble.className = 'chat-bubble ai';
  loadingBubble.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin" style="color: var(--primary-blue); margin-right: 0.4rem;"></i> Sedang menyusun analisis hukum & kepatuhan...';
  messagesContainer.appendChild(loadingBubble);
  messagesContainer.scrollTop = messagesContainer.scrollHeight;

  const startTime = Date.now();

  try {
    const payload = {
      message: message,
      chat_history: chatHistory,
      document_context: currentReportData?.document_checked || '',
      block_context: activeContextBlock ? activeContextBlock.original_text : null
    };

    const res = await fetch('/api/assistant/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    const rawReply = data.reply || 'Tidak ada respons dari AI.';
    const formattedHTML = renderMarkdownToHTML(rawReply);

    const elapsedSec = ((Date.now() - startTime) / 1000).toFixed(1);

    // Save reply for copy action
    loadingBubble.setAttribute('data-raw-content', rawReply);
    loadingBubble.innerHTML = `
      <div class="ai-content-body">${formattedHTML}</div>
      <div class="chat-bubble-footer">
        <span><i class="fa-solid fa-scale-balanced" style="color: var(--primary-blue);"></i> Legal Drafter AI &bull; ${elapsedSec}s</span>
        <button class="chat-copy-btn" onclick="copyChatResponse(this)" title="Salin seluruh jawaban">
          <i class="fa-regular fa-copy"></i> Salin Jawaban
        </button>
      </div>
    `;

    // Record in history
    chatHistory.push({ role: 'user', content: message });
    chatHistory.push({ role: 'assistant', content: rawReply });

  } catch (err) {
    loadingBubble.innerHTML = `
      <div style="color: var(--danger); font-weight: 600;">
        <i class="fa-solid fa-triangle-exclamation"></i> Gagal memproses: ${escapeHTML(err.message)}
      </div>
    `;
  }

  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function copyChatResponse(btn) {
  const bubble = btn.closest('.chat-bubble.ai');
  if (!bubble) return;

  const rawContent = bubble.getAttribute('data-raw-content') || bubble.querySelector('.ai-content-body')?.innerText || '';
  if (!rawContent) return;

  navigator.clipboard.writeText(rawContent).then(() => {
    const originalHTML = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-check" style="color: #16a34a;"></i> Tersalin!';
    btn.style.borderColor = '#16a34a';
    btn.style.color = '#16a34a';
    setTimeout(() => {
      btn.innerHTML = originalHTML;
      btn.style.borderColor = '';
      btn.style.color = '';
    }, 2000);
  }).catch(err => {
    alert('Gagal menyalin teks: ' + err);
  });
}

// Load Guideline Documents
async function loadGuidelines() {
  const tbody = document.getElementById('guidelines-table-body');
  try {
    const res = await fetch('/api/guidelines');
    const data = await res.json();
    tbody.innerHTML = '';

    if (!data.guidelines || data.guidelines.length === 0) {
      tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color: var(--text-muted); padding: 2rem;">Belum ada file pedoman di folder ./rules/</td></tr>`;
      return;
    }

    data.guidelines.forEach(g => {
      const tr = document.createElement('tr');
      const icon = getFileIcon(g.name);
      const extName = (g.extension || '').replace('.', '').toUpperCase();

      tr.innerHTML = `
        <td><span class="format-tag">${extName}</span></td>
        <td>${icon} <strong style="margin-left: 0.35rem;">${escapeHTML(g.name)}</strong></td>
        <td>${(g.size / 1024 / 1024).toFixed(2)} MB</td>
        <td><span class="status-badge sesuai"><i class="fa-solid fa-circle-check"></i> Terindeks</span></td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="4" style="color: var(--danger);">Gagal memuat daftar pedoman.</td></tr>`;
  }
}

async function handleRuleUpload(event) {
  const files = event.target.files;
  if (!files || files.length === 0) return;

  const formData = new FormData();
  for (let i = 0; i < files.length; i++) {
    formData.append('files', files[i]);
  }

  try {
    const res = await fetch('/api/upload-guidelines', {
      method: 'POST',
      body: formData
    });

    if (res.ok) {
      alert('File pedoman berhasil diunggah! Jangan lupa klik "Re-index Database Vektor".');
      loadGuidelines();
    }
  } catch (err) {
    alert(`Gagal mengunggah file pedoman: ${err.message}`);
  }
}

async function triggerReindex() {
  if (!confirm('Apakah Anda yakin ingin memproses ulang (re-index) seluruh file pedoman ke ChromaDB?')) return;

  try {
    const res = await fetch('/api/index-guidelines', { method: 'POST' });
    const data = await res.json();

    if (res.ok) {
      alert(data.message);
      checkSystemStatus();
    } else {
      throw new Error(data.detail || 'Indexing gagal.');
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

async function loadHistory() {
  const tbody = document.getElementById('reports-table-body');
  try {
    const res = await fetch('/api/reports');
    const data = await res.json();
    tbody.innerHTML = '';

    if (!data.reports || data.reports.length === 0) {
      tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color: var(--text-muted); padding: 2rem;">Belum ada riwayat laporan tersimpan.</td></tr>`;
      return;
    }

    data.reports.forEach(r => {
      const dateStr = new Date(r.created * 1000).toLocaleString('id-ID');
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><i class="fa-solid fa-file-code" style="color: var(--primary-blue); margin-right: 0.5rem;"></i> <strong>${escapeHTML(r.filename)}</strong></td>
        <td>${(r.size / 1024).toFixed(1)} KB</td>
        <td>${dateStr}</td>
        <td>
          <a href="/api/reports/${encodeURIComponent(r.filename)}" target="_blank" class="btn btn-secondary btn-sm">
            <i class="fa-solid fa-eye"></i> Lihat JSON
          </a>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="4" style="color: var(--danger);">Gagal memuat riwayat laporan.</td></tr>`;
  }
}

function escapeHTML(str) {
  if (!str) return '';
  return str.replace(/[&<>'"]/g,
    tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
  );
}
