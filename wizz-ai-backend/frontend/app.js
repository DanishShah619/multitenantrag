/**
 * Wizz AI Frontend SPA Controller
 * Multi-Tenant RAG Management, Streaming SSE Chat, and Knowledge Base Ingestion
 */

// Application State
const state = {
  apiBase: window.location.origin,
  tenantId: localStorage.getItem('wizz_tenant_id') || '',
  adminApiKey: localStorage.getItem('wizz_admin_key') || '',
  embedApiKey: localStorage.getItem('wizz_embed_key') || '',
  tenantName: localStorage.getItem('wizz_tenant_name') || '',
  isDemo: localStorage.getItem('wizz_is_demo') === 'true',
  activeChatId: null,
  isStreaming: false,
  documents: [],
  citations: [],
};

// DOM Elements
const elements = {
  // Navigation
  tabButtons: document.querySelectorAll('.nav-tab'),
  tabPanes: document.querySelectorAll('.tab-pane'),
  systemStatus: document.getElementById('system-status'),
  
  // Tenant Context
  tenantBanner: document.getElementById('tenant-banner'),
  tenantTypeBadge: document.getElementById('tenant-type-badge'),
  tenantNameDisplay: document.getElementById('tenant-name-display'),
  tenantIdPill: document.getElementById('tenant-id-pill'),
  btnQuickDemo: document.getElementById('btn-quick-demo'),
  
  // Chat Playground
  chatMessages: document.getElementById('chat-messages'),
  chatWelcome: document.getElementById('chat-welcome'),
  chatForm: document.getElementById('chat-form'),
  chatInput: document.getElementById('chat-input'),
  btnSend: document.getElementById('btn-send'),
  btnClearChat: document.getElementById('btn-clear-chat'),
  chatThreadId: document.getElementById('chat-thread-id'),
  citationsList: document.getElementById('citations-list'),
  sampleChips: document.querySelectorAll('.sample-chip'),
  
  // Documents Hub
  uploadZone: document.getElementById('upload-zone'),
  fileInput: document.getElementById('file-input'),
  uploadProgressCard: document.getElementById('upload-progress-card'),
  uploadingFileName: document.getElementById('uploading-file-name'),
  uploadingStatusLabel: document.getElementById('uploading-status-label'),
  docTableBody: document.getElementById('doc-table-body'),
  docCountBadge: document.getElementById('doc-count-badge'),
  docTableCount: document.getElementById('doc-table-count'),
  btnRefreshDocs: document.getElementById('btn-refresh-docs'),
  
  // Tenant & Keys Manager
  tenantIdInput: document.getElementById('tenant-id-input'),
  adminKeyInput: document.getElementById('admin-key-input'),
  embedKeyInput: document.getElementById('embed-key-input'),
  btnSaveKeys: document.getElementById('btn-save-keys'),
  btnResetTenant: document.getElementById('btn-reset-tenant'),
  btnCreateDemoTenant: document.getElementById('btn-create-demo-tenant'),
  demoLabelInput: document.getElementById('demo-label-input'),
  activeTenantStatus: document.getElementById('active-tenant-status'),
  
  // Widget Simulator
  simLauncherBtn: document.getElementById('sim-launcher-btn'),
  simWidgetWindow: document.getElementById('sim-widget-window'),
  simCloseBtn: document.getElementById('sim-close-btn'),
  simWidgetForm: document.getElementById('sim-widget-form'),
  simWidgetInput: document.getElementById('sim-widget-input'),
  simWidgetMessages: document.getElementById('sim-widget-messages'),
  
  // Toast
  toastContainer: document.getElementById('toast-container'),
};

// Initialize Application
document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initTenantState();
  initHealthCheck();
  initChat();
  initDocuments();
  initTenantManager();
  initWidgetSimulator();
  initCopyButtons();
});

// Toast Notifications
function showToast(message, type = 'info') {
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${message}</span>`;
  elements.toastContainer.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Tab Switching
function initTabs() {
  elements.tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetTab = btn.dataset.tab;
      elements.tabButtons.forEach(b => b.classList.remove('active'));
      elements.tabPanes.forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`pane-${targetTab}`).classList.add('active');

      if (targetTab === 'documents') {
        loadDocuments();
      }
    });
  });
}

// Health Check
async function initHealthCheck() {
  try {
    const res = await fetch(`${state.apiBase}/health`);
    if (res.ok) {
      elements.systemStatus.className = 'status-indicator online';
      elements.systemStatus.querySelector('.status-text').textContent = 'API Online';
    } else {
      throw new Error();
    }
  } catch (err) {
    elements.systemStatus.className = 'status-indicator error';
    elements.systemStatus.querySelector('.status-text').textContent = 'API Disconnected';
  }
}

// Tenant State Management
function initTenantState() {
  if (state.tenantId) {
    elements.tenantBanner.style.display = 'block';
    elements.tenantTypeBadge.textContent = state.isDemo ? 'Demo Tenant' : 'Custom Tenant';
    elements.tenantTypeBadge.className = `tenant-badge ${state.isDemo ? 'demo' : ''}`;
    elements.tenantNameDisplay.textContent = state.tenantName || 'Active Tenant';
    elements.tenantIdPill.style.display = 'inline-block';
    elements.tenantIdPill.textContent = state.tenantId.slice(0, 8) + '...';

    elements.tenantIdInput.value = state.tenantId;
    elements.adminKeyInput.value = state.adminApiKey;
    elements.embedKeyInput.value = state.embedApiKey;
    elements.activeTenantStatus.textContent = 'Active';
    elements.activeTenantStatus.className = 'status-pill active';

    loadDocuments();
  } else {
    elements.tenantTypeBadge.textContent = 'No Tenant Active';
    elements.tenantTypeBadge.className = 'tenant-badge';
    elements.tenantNameDisplay.textContent = 'Click "1-Click Demo Tenant" to start testing instantly';
    elements.tenantIdPill.style.display = 'none';
    elements.activeTenantStatus.textContent = 'Unconfigured';
    elements.activeTenantStatus.className = 'status-pill';
  }

  // Quick Demo Buttons
  elements.btnQuickDemo.addEventListener('click', createDemoTenant);
  elements.btnCreateDemoTenant.addEventListener('click', createDemoTenant);
}

// 1-Click Demo Tenant Creation
async function createDemoTenant() {
  const label = elements.demoLabelInput ? elements.demoLabelInput.value.trim() : '';
  showToast('Creating demo tenant...', 'info');

  try {
    const res = await fetch(`${state.apiBase}/demo/signup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: label || 'Demo Tenant' }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to create demo tenant' }));
      throw new Error(err.detail || 'Demo signup failed');
    }

    const data = await res.json();
    state.tenantId = data.tenant_id;
    state.adminApiKey = data.admin_api_key;
    state.embedApiKey = data.embed_api_key;
    state.tenantName = label || 'Demo Tenant';
    state.isDemo = true;

    localStorage.setItem('wizz_tenant_id', state.tenantId);
    localStorage.setItem('wizz_admin_key', state.adminApiKey);
    localStorage.setItem('wizz_embed_key', state.embedApiKey);
    localStorage.setItem('wizz_tenant_name', state.tenantName);
    localStorage.setItem('wizz_is_demo', 'true');

    initTenantState();
    showToast('🎉 Demo tenant provisioned successfully!', 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// Tenant Manager Forms
function initTenantManager() {
  elements.btnSaveKeys.addEventListener('click', () => {
    state.adminApiKey = elements.adminKeyInput.value.trim();
    state.embedApiKey = elements.embedKeyInput.value.trim();
    localStorage.setItem('wizz_admin_key', state.adminApiKey);
    localStorage.setItem('wizz_embed_key', state.embedApiKey);
    showToast('API Keys saved locally', 'success');
  });

  elements.btnResetTenant.addEventListener('click', () => {
    if (confirm('Clear stored tenant credentials?')) {
      localStorage.clear();
      state.tenantId = '';
      state.adminApiKey = '';
      state.embedApiKey = '';
      state.tenantName = '';
      state.isDemo = false;
      initTenantState();
      showToast('Tenant configuration reset', 'info');
    }
  });

  // Password visibility toggle
  document.querySelectorAll('.btn-toggle-vis').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = document.getElementById(btn.dataset.toggleTarget);
      if (target.type === 'password') {
        target.type = 'text';
        btn.textContent = '🔒';
      } else {
        target.type = 'password';
        btn.textContent = '👁';
      }
    });
  });
}

// ==========================================================================
// Document Management & Ingestion
// ==========================================================================
function initDocuments() {
  const zone = elements.uploadZone;

  zone.addEventListener('click', () => {
    if (!state.adminApiKey) {
      showToast('Please create or configure an Admin API key first', 'error');
      return;
    }
    elements.fileInput.click();
  });

  elements.fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
      uploadDocument(e.target.files[0]);
    }
  });

  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    zone.classList.add('dragover');
  });

  zone.addEventListener('dragleave', () => {
    zone.classList.remove('dragover');
  });

  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('dragover');
    if (!state.adminApiKey) {
      showToast('Please create or configure an Admin API key first', 'error');
      return;
    }
    if (e.dataTransfer.files.length > 0) {
      uploadDocument(e.dataTransfer.files[0]);
    }
  });

  elements.btnRefreshDocs.addEventListener('click', loadDocuments);
}

async function uploadDocument(file) {
  if (!state.adminApiKey) {
    showToast('Admin API key required for uploads', 'error');
    return;
  }

  elements.uploadProgressCard.style.display = 'block';
  elements.uploadingFileName.textContent = file.name;
  elements.uploadingStatusLabel.textContent = 'Uploading & Parsing...';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch(`${state.apiBase}/documents`, {
      method: 'POST',
      headers: {
        'X-API-Key': state.adminApiKey,
      },
      body: formData,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Upload failed' }));
      throw new Error(err.detail || 'Document processing failed');
    }

    const data = await res.json();
    showToast(`✓ Document "${file.name}" ingested successfully!`, 'success');
    loadDocuments();
  } catch (err) {
    showToast(`Error: ${err.message}`, 'error');
  } finally {
    setTimeout(() => {
      elements.uploadProgressCard.style.display = 'none';
      elements.fileInput.value = '';
    }, 1500);
  }
}

async function loadDocuments() {
  if (!state.adminApiKey) return;

  try {
    const res = await fetch(`${state.apiBase}/documents`, {
      headers: { 'X-API-Key': state.adminApiKey },
    });

    if (!res.ok) return;

    const docs = await res.json();
    state.documents = docs;
    renderDocuments(docs);
  } catch (err) {
    console.error('Failed to load documents', err);
  }
}

function renderDocuments(docs) {
  elements.docCountBadge.textContent = docs.length;
  elements.docTableCount.textContent = `${docs.length} items`;

  if (docs.length === 0) {
    elements.docTableBody.innerHTML = `
      <tr>
        <td colspan="6" class="text-center empty-state" style="text-align: center; padding: 30px; color: var(--text-muted);">
          No documents uploaded yet. Drop a PDF, DOCX, or Markdown file above to start indexing.
        </td>
      </tr>
    `;
    return;
  }

  elements.docTableBody.innerHTML = docs.map(doc => {
    const sizeKb = doc.size_bytes ? `${(doc.size_bytes / 1024).toFixed(1)} KB` : 'N/A';
    const dateStr = new Date(doc.created_at).toLocaleDateString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    });

    let statusClass = `badge status-${doc.status}`;
    let statusText = doc.status;

    return `
      <tr>
        <td><strong>${escapeHtml(doc.filename)}</strong></td>
        <td><span class="${statusClass}">${statusText}</span></td>
        <td>${doc.chunk_count || 0}</td>
        <td>${sizeKb}</td>
        <td>${dateStr}</td>
        <td>
          <button class="btn btn-ghost btn-xs" onclick="window.askAboutDoc('${escapeHtml(doc.filename)}')">
            Ask Questions
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

window.askAboutDoc = function(filename) {
  document.getElementById('tab-btn-chat').click();
  elements.chatInput.value = `Summarize key details from ${filename}`;
  elements.chatInput.focus();
};

// ==========================================================================
// Chat Playground & Real-Time SSE Streaming
// ==========================================================================
function initChat() {
  elements.chatForm.addEventListener('submit', (e) => {
    e.preventDefault();
    sendChatMessage();
  });

  // Shift+Enter for newline, Enter to send
  elements.chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage();
    }
  });

  elements.btnClearChat.addEventListener('click', () => {
    state.activeChatId = null;
    elements.chatThreadId.textContent = 'New Thread';
    elements.chatMessages.innerHTML = '';
    elements.chatMessages.appendChild(elements.chatWelcome);
    elements.citationsList.innerHTML = `
      <div class="empty-citations">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="empty-icon"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
        <p>No citations yet. Ask a question to see the retrieved source passages and confidence scores.</p>
      </div>
    `;
  });

  // Sample Query Chips
  elements.sampleChips.forEach(chip => {
    chip.addEventListener('click', () => {
      elements.chatInput.value = chip.dataset.query;
      sendChatMessage();
    });
  });
}

async function sendChatMessage() {
  const query = elements.chatInput.value.trim();
  if (!query || state.isStreaming) return;

  if (!state.embedApiKey) {
    showToast('Please create or set an Embed API Key first', 'error');
    document.getElementById('tab-btn-tenant').click();
    return;
  }

  // Remove welcome banner on first message
  if (elements.chatWelcome && elements.chatWelcome.parentNode === elements.chatMessages) {
    elements.chatWelcome.remove();
  }

  // Render User Message
  appendMessage('user', query);
  elements.chatInput.value = '';
  elements.btnSend.disabled = true;
  state.isStreaming = true;

  // Placeholder for streaming assistant response
  const assistantBubble = appendMessage('assistant', '', true);
  const msgContentEl = assistantBubble.querySelector('.msg-content');

  try {
    const res = await fetch(`${state.apiBase}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': state.embedApiKey,
      },
      body: JSON.stringify({
        query: query,
        chat_id: state.activeChatId,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Chat request failed' }));
      throw new Error(err.detail || `Server error (${res.status})`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let answerText = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // keep last incomplete chunk

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === 'token') {
              answerText += data.content;
              msgContentEl.innerHTML = formatMarkdown(answerText);
              elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
            } else if (data.type === 'citations') {
              state.citations = data.citations || [];
              renderCitations(state.citations);
            } else if (data.type === 'done') {
              if (data.chat_id) {
                state.activeChatId = data.chat_id;
                elements.chatThreadId.textContent = `Thread ${data.chat_id.slice(0, 8)}`;
              }
            }
          } catch (e) {
            console.error('SSE JSON parse error', e);
          }
        }
      }
    }
  } catch (err) {
    msgContentEl.innerHTML = `<span style="color: var(--danger);">Error: ${escapeHtml(err.message)}</span>`;
    showToast(err.message, 'error');
  } finally {
    msgContentEl.classList.remove('streaming-cursor');
    elements.btnSend.disabled = false;
    state.isStreaming = false;
  }
}

function appendMessage(role, text, isStreaming = false) {
  const msgEl = document.createElement('div');
  msgEl.className = `chat-msg ${role}`;
  msgEl.innerHTML = `
    <div class="msg-avatar">${role === 'user' ? 'U' : 'AI'}</div>
    <div class="msg-content ${isStreaming ? 'streaming-cursor' : ''}">${formatMarkdown(text)}</div>
  `;
  elements.chatMessages.appendChild(msgEl);
  elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
  return msgEl;
}

// Citations Rendering
function renderCitations(citations) {
  if (!citations || citations.length === 0) {
    elements.citationsList.innerHTML = `
      <div class="empty-citations">
        <p>No citations found for this query.</p>
      </div>
    `;
    return;
  }

  elements.citationsList.innerHTML = citations.map(c => `
    <div class="citation-card" id="citation-card-${c.ref}">
      <div class="citation-card-header">
        <span class="citation-ref-tag">[${c.ref}]</span>
        <span class="citation-score-tag">Score: ${(c.score || 0).toFixed(3)}</span>
      </div>
      <div class="citation-doc-name" title="${escapeHtml(c.filename)}">
        📄 ${escapeHtml(c.filename || 'Document')}
      </div>
      <span class="citation-content-type">${escapeHtml(c.content_type || 'text')}</span>
      <div class="citation-preview">${escapeHtml(c.text_preview)}...</div>
    </div>
  `).join('');
}

// Basic Markdown / Citation formatter
function formatMarkdown(text) {
  if (!text) return '';
  let formatted = escapeHtml(text);

  // Bold
  formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  // Italic
  formatted = formatted.replace(/\*(.*?)\*/g, '<em>$1</em>');
  // Inline Code
  formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Citations [1], [2]
  formatted = formatted.replace(/\[(\d+)\]/g, '<span class="inline-ref" onclick="window.highlightCitation($1)">[$1]</span>');
  // Line breaks
  formatted = formatted.replace(/\n\n/g, '</p><p>');
  formatted = formatted.replace(/\n/g, '<br>');

  return `<p>${formatted}</p>`;
}

window.highlightCitation = function(ref) {
  const card = document.getElementById(`citation-card-${ref}`);
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.style.borderColor = 'var(--accent-primary)';
    card.style.transform = 'scale(1.02)';
    setTimeout(() => {
      card.style.borderColor = 'var(--border-subtle)';
      card.style.transform = 'none';
    }, 1500);
  }
};

// ==========================================================================
// Widget Simulator
// ==========================================================================
function initWidgetSimulator() {
  elements.simLauncherBtn.addEventListener('click', () => {
    elements.simWidgetWindow.classList.toggle('open');
  });

  elements.simCloseBtn.addEventListener('click', () => {
    elements.simWidgetWindow.classList.remove('open');
  });

  elements.simWidgetForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const query = elements.simWidgetInput.value.trim();
    if (!query) return;

    if (!state.embedApiKey) {
      alert('Please provision a demo tenant first');
      return;
    }

    // Add user msg
    const userBubble = document.createElement('div');
    userBubble.className = 'widget-msg user';
    userBubble.textContent = query;
    elements.simWidgetMessages.appendChild(userBubble);
    elements.simWidgetInput.value = '';

    // Assistant placeholder
    const botBubble = document.createElement('div');
    botBubble.className = 'widget-msg bot';
    botBubble.textContent = '...';
    elements.simWidgetMessages.appendChild(botBubble);
    elements.simWidgetMessages.scrollTop = elements.simWidgetMessages.scrollHeight;

    try {
      const res = await fetch(`${state.apiBase}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': state.embedApiKey,
        },
        body: JSON.stringify({ query }),
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let answer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const textChunk = decoder.decode(value);
        const lines = textChunk.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.type === 'token') {
                answer += data.content;
                botBubble.textContent = answer;
                elements.simWidgetMessages.scrollTop = elements.simWidgetMessages.scrollHeight;
              }
            } catch (err) {}
          }
        }
      }
    } catch (err) {
      botBubble.textContent = 'Error connecting to assistant';
    }
  });
}

// Copy to Clipboard Helpers
function initCopyButtons() {
  document.querySelectorAll('.btn-copy').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = document.getElementById(btn.dataset.copyTarget);
      if (target) {
        navigator.clipboard.writeText(target.value);
        btn.textContent = 'Copied!';
        setTimeout(() => btn.textContent = 'Copy', 2000);
      }
    });
  });

  document.querySelectorAll('.btn-copy-code').forEach(btn => {
    btn.addEventListener('click', () => {
      const codeId = `code-${btn.dataset.code}`;
      const codeEl = document.getElementById(codeId);
      if (codeEl) {
        navigator.clipboard.writeText(codeEl.innerText);
        btn.textContent = 'Copied!';
        setTimeout(() => btn.textContent = 'Copy curl', 2000);
      }
    });
  });
}

function escapeHtml(str) {
  return (str || '').replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  })[m]);
}
