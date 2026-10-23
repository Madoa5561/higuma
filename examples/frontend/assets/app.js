const title = document.querySelector('#page-title');
const description = document.querySelector('#description');
const panel = document.querySelector('#status-panel');
const status = document.querySelector('#request-status');
const output = document.querySelector('#api-response');
const refresh = document.querySelector('#refresh');
let pending;

const pages = {
  '/app': ['サーバーと、ブラウザー。それぞれの仕事を。',
    'HigumaはHTMLとassetsを配信し、ブラウザーが画面を描画します。ナビゲーションはページ全体を読み直さずに切り替わります。'],
  '/app/status': ['APIから届いた、実際の応答。',
    'この画面はGET /api/statusのJSONを取得して表示します。通信に失敗した場合も、成功したデータを装って表示しません。'],
  '/app/about': ['ひとつのアプリで、CSRとAPIを。',
    'History APIによる画面遷移と、mount_spaによる直接アクセス対応を組み合わせています。React・Vue等のビルド成果物も同じ方法で配信できます。'],
};

async function loadStatus() {
  pending?.abort();
  const controller = new AbortController();
  pending = controller;
  refresh.disabled = true;
  status.textContent = 'APIに接続中…';
  output.textContent = '';
  try {
    const response = await fetch('/api/status', { signal: controller.signal, headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (pending !== controller) return;
    output.textContent = JSON.stringify(data, null, 2);
    status.textContent = '取得成功 · Higumaの実API応答';
  } catch (error) {
    if (error.name === 'AbortError' || pending !== controller) return;
    status.textContent = `取得できませんでした: ${error.message}`;
  } finally {
    if (pending === controller) refresh.disabled = false;
  }
}

function render(focus = false) {
  pending?.abort();
  const path = location.pathname.replace(/\/$/, '');
  const page = pages[path] ?? ['画面が見つかりません', 'ナビゲーションからサンプルの画面を選んでください。'];
  title.textContent = page[0];
  description.textContent = page[1];
  document.title = `${page[0]} · Higuma CSR`;
  panel.hidden = path !== '/app/status';
  document.querySelectorAll('nav a').forEach((link) => {
    if (new URL(link.href).pathname.replace(/\/$/, '') === path) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  if (!panel.hidden) loadStatus();
  if (focus) title.focus();
}

document.addEventListener('click', (event) => {
  const link = event.target.closest('a');
  if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
      || link.target || link.hasAttribute('download')) return;
  const url = new URL(link.href);
  if (url.origin !== location.origin || !url.pathname.startsWith('/app/')) return;
  event.preventDefault();
  history.pushState(null, '', url);
  render(true);
});
window.addEventListener('popstate', () => render(true));
refresh.addEventListener('click', loadStatus);
render();
