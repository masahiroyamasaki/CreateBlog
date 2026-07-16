// ===== 会社コンテキストを読み取る =====
const COMPANY_ID       = document.getElementById('company-id')?.value || null;
const COMPANY_TONE     = document.getElementById('company-tone')?.value || '';
const COMPANY_WC       = document.getElementById('company-word-count')?.value || '';
const WP_CONFIGURED    = document.getElementById('wp-configured')?.value === 'true';
const WP_STATUS_LABEL  = document.getElementById('wp-status-label')?.value || 'draft';
const HAS_TEMPLATE     = document.getElementById('has-template')?.value === 'true';
const MAIL_CONFIGURED  = document.getElementById('mail-configured')?.value === 'true';

// 会社設定でフォームを上書き
if (COMPANY_TONE) document.getElementById('tone').value = COMPANY_TONE;
if (COMPANY_WC)   document.getElementById('word_count').value = COMPANY_WC;

const state = {
  draft: '',
  contentCheck: '',
  legalCheck: '',
  finalContent: '',
  savedPostId: null,
  templateHtml: null,
};

// ===== メインフォーム送信 → 全ステップ自動実行 =====
document.getElementById('form-main').addEventListener('submit', async (e) => {
  e.preventDefault();

  const data = {
    topic:      document.getElementById('topic').value,
    keywords:   document.getElementById('keywords').value,
    tone:       document.getElementById('tone').value,
    word_count: document.getElementById('word_count').value,
  };

  document.getElementById('input-card').classList.add('hidden');
  document.getElementById('progress-card').classList.remove('hidden');

  // STEP 1
  setStatus(1, 'running');
  await streamTo('/api/create-draft', data, 'stream-1', (t) => { state.draft += t; });
  setStatus(1, 'done');

  // STEP 2
  setStatus(2, 'running');
  await streamTo('/api/check-content', { draft: state.draft }, 'stream-2', (t) => { state.contentCheck += t; });
  setStatus(2, 'done');

  // STEP 3
  setStatus(3, 'running');
  await streamTo('/api/check-legal', { draft: state.draft }, 'stream-3', (t) => { state.legalCheck += t; });
  setStatus(3, 'done');

  // STEP 4
  setStatus(4, 'running');
  document.getElementById('final-card').classList.remove('hidden');
  document.getElementById('final-card').scrollIntoView({ behavior: 'smooth', block: 'start' });

  await streamTo(
    '/api/create-final',
    { draft: state.draft, content_check: state.contentCheck, legal_check: state.legalCheck },
    'final-stream',
    (t) => { state.finalContent += t; }
  );

  setStatus(4, 'done');
  document.getElementById('progress-title').textContent = '全エージェントの処理が完了しました ✓';

  // Markdown レンダリング
  const rendered = document.getElementById('final-rendered');
  rendered.innerHTML = marked.parse(state.finalContent);
  document.getElementById('final-stream').classList.add('hidden');
  rendered.classList.remove('hidden');

  // テンプレートが設定されていれば適用
  if (HAS_TEMPLATE) await applyTemplatePreview();

  // H1 からタイトルを自動抽出
  const titleMatch = state.finalContent.match(/^#\s+(.+)/m);
  if (titleMatch) document.getElementById('save-title').value = titleMatch[1].trim();

  // 会社 + WP 設定あり → 自動保存してメール送信
  if (COMPANY_ID && WP_CONFIGURED) {
    await autoSaveAndEmail();
  } else {
    document.getElementById('save-form').classList.remove('hidden');
    document.getElementById('save-form').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
});

// ===== 最終記事の編集トグル =====
async function toggleFinalEdit() {
  const rendered = document.getElementById('final-rendered');
  const editEl   = document.getElementById('final-edit');
  const btn      = event.currentTarget;
  const isEditing = !editEl.classList.contains('hidden');

  if (isEditing) {
    state.finalContent = editEl.value;
    rendered.innerHTML = marked.parse(state.finalContent);
    if (HAS_TEMPLATE) await applyTemplatePreview();
    rendered.classList.remove('hidden');
    editEl.classList.add('hidden');
    btn.textContent = '編集する';
  } else {
    editEl.value = state.finalContent;
    rendered.classList.add('hidden');
    editEl.classList.remove('hidden');
    btn.textContent = '編集を完了';
  }
}

// ===== テンプレートプレビュー適用 =====
async function applyTemplatePreview() {
  // テンプレート HTML をキャッシュから取得（なければフェッチ）
  if (state.templateHtml === null) {
    try {
      const res = await fetch(`/api/template-content?company_id=${COMPANY_ID}`);
      const data = await res.json();
      state.templateHtml = data.has_template ? data.template : false;
    } catch {
      state.templateHtml = false;
    }
  }
  if (!state.templateHtml) return;

  const rendered = document.getElementById('final-rendered');
  const articleHtml = rendered.innerHTML;

  // テンプレートのスタイルを抽出してヘッドに注入（重複しないよう管理）
  const styleMatch = state.templateHtml.match(/<style[^>]*>([\s\S]*?)<\/style>/i);
  if (styleMatch) {
    const styleId = 'company-template-style';
    let styleEl = document.getElementById(styleId);
    if (!styleEl) {
      styleEl = document.createElement('style');
      styleEl.id = styleId;
      document.head.appendChild(styleEl);
    }
    styleEl.textContent = styleMatch[1];
  }

  // {{content}} を記事 HTML に置換して表示
  const withoutStyle = state.templateHtml.replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '');
  rendered.innerHTML = withoutStyle.replace('{{content}}', articleHtml);

  // テンプレート適用バッジを表示
  let badge = document.getElementById('template-badge');
  if (!badge) {
    badge = document.createElement('div');
    badge.id = 'template-badge';
    badge.className = 'template-badge';
    badge.textContent = 'テンプレート適用中';
    rendered.parentElement.insertBefore(badge, rendered);
  }
}

// ===== 保存 =====
async function saveArticle() {
  const btn = event.currentTarget;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>保存中...';

  const payload = {
    title:         document.getElementById('save-title').value || '無題',
    content:       state.finalContent,
    content_check: state.contentCheck,
    legal_check:   state.legalCheck,
    tags:          document.getElementById('save-tags').value,
    company_id:    COMPANY_ID,
  };

  const res  = await fetch('/api/save-article', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (data.success) {
    state.savedPostId = data.post_id;
    if (WP_CONFIGURED) {
      document.getElementById('save-actions').classList.add('hidden');
      const section = document.getElementById('review-section');
      section.classList.remove('hidden');
      section.scrollIntoView({ behavior: 'smooth', block: 'start' });

      if (MAIL_CONFIGURED) {
        // メール確認フロー
        await sendReviewEmail(data.post_id);
      } else {
        // メール未設定 → 直接WP投稿ボタンを表示
        document.getElementById('btn-wp-direct').classList.remove('hidden');
        const errEl = document.getElementById('review-error');
        errEl.textContent = 'メール設定が未完了のため、直接投稿モードで動作します。';
        errEl.classList.remove('hidden');
      }
    } else {
      window.location.href = COMPANY_ID
        ? `/companies/${COMPANY_ID}`
        : `/view/${data.post_id}`;
    }
  } else {
    btn.disabled = false;
    btn.innerHTML = '保存して完了 ✓';
    alert('保存に失敗しました。再度お試しください。');
  }
}

// ===== 生成完了時の自動保存＋メール送信 =====
async function autoSaveAndEmail() {
  const notice = document.getElementById('auto-save-notice');
  notice.className = 'auto-save-notice saving';
  notice.innerHTML = '<span class="spinner"></span> 記事を保存しています...';
  notice.classList.remove('hidden');
  notice.scrollIntoView({ behavior: 'smooth', block: 'start' });

  // 保存
  const title = document.getElementById('save-title').value || '無題';
  const saveRes = await fetch('/api/save-article', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title,
      content:       state.finalContent,
      content_check: state.contentCheck,
      legal_check:   state.legalCheck,
      tags:          '',
      company_id:    COMPANY_ID,
    }),
  });
  const saveData = await saveRes.json();

  if (!saveData.success) {
    notice.className = 'auto-save-notice error';
    notice.textContent = '⚠ 自動保存に失敗しました。手動で保存してください。';
    document.getElementById('save-form').classList.remove('hidden');
    document.getElementById('save-form').scrollIntoView({ behavior: 'smooth', block: 'start' });
    return;
  }

  state.savedPostId = saveData.post_id;
  notice.innerHTML = '<span class="spinner"></span> 確認メールを送信しています...';

  // メール送信（review-section を先に表示してから呼ぶ）
  document.getElementById('save-form').classList.remove('hidden');
  document.getElementById('save-actions').classList.add('hidden');
  document.getElementById('review-section').classList.remove('hidden');

  await sendReviewEmail(saveData.post_id);

  notice.className = 'auto-save-notice done';
  notice.textContent = '✓ 記事を保存しました（タイトル: ' + title + '）';
}

// ===== 確認メール送信 =====
async function sendReviewEmail(postId) {
  const sendingEl = document.getElementById('review-sending');
  const sentEl    = document.getElementById('review-sent');
  const errorEl   = document.getElementById('review-error');

  sendingEl.classList.remove('hidden');

  const res = await fetch('/api/send-review-email', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ post_id: postId, company_id: COMPANY_ID }),
  });
  const data = await res.json();

  sendingEl.classList.add('hidden');

  if (data.success) {
    document.getElementById('review-sent-desc').textContent =
      `${data.to} に確認メールを送信しました。メール内のボタンをクリックして投稿を承認してください。`;
    sentEl.classList.remove('hidden');
  } else {
    errorEl.textContent = `メール送信エラー: ${data.reason}`;
    errorEl.classList.remove('hidden');
    document.getElementById('btn-wp-direct').classList.remove('hidden');
  }
}

// ===== WordPress 直接投稿（メール未設定時のフォールバック） =====
async function postToWordPress() {
  const btn   = document.getElementById('btn-wp-direct');
  const errEl = document.getElementById('review-error');

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>投稿中...';

  const res = await fetch('/api/wp-post', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      company_id: COMPANY_ID,
      title:      document.getElementById('save-title').value || '無題',
      content:    state.finalContent,
    }),
  });
  const data = await res.json();

  if (data.success) {
    const label = data.status === 'publish' ? '公開' : '下書き';
    btn.textContent = '投稿完了 ✓';
    errEl.className = 'review-success';
    errEl.innerHTML = `✓ WordPress に${label}しました。` +
      (data.link ? ` <a href="${data.link}" target="_blank">記事を確認する ↗</a>` : '');
    errEl.classList.remove('hidden');
    setTimeout(() => {
      window.location.href = COMPANY_ID ? `/companies/${COMPANY_ID}` : `/view/${state.savedPostId}`;
    }, 3000);
  } else {
    errEl.textContent = `投稿エラー: ${data.reason}`;
    errEl.classList.remove('hidden');
    btn.disabled = false;
    btn.textContent = '再試行する';
  }
}

function skipToList() {
  window.location.href = COMPANY_ID
    ? `/companies/${COMPANY_ID}`
    : `/view/${state.savedPostId}`;
}

// ===== ステップステータス更新 =====
function setStatus(n, st) {
  const icon   = document.getElementById(`icon-${n}`);
  const status = document.getElementById(`status-${n}`);
  icon.className = `pipe-icon ${st}`;
  if (st === 'running') {
    icon.innerHTML   = '<span class="spinner-sm"></span>';
    status.textContent = '処理中...';
  } else if (st === 'done') {
    icon.textContent   = '✓';
    status.textContent = '完了';
  }
}

// ===== ストリーミング共通処理 =====
async function streamTo(url, body, targetId, onChunk) {
  const el = document.getElementById(targetId);
  if (el) el.textContent = '';

  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) { if (el) el.textContent = `エラー: ${res.status}`; return; }

  const reader  = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    onChunk(chunk);
    if (el) { el.textContent += chunk; el.scrollTop = el.scrollHeight; }
  }
}
