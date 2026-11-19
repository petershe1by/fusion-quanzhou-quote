(() => {
  if (globalThis.fusionQuanzhouBatchLoaded) return;
  globalThis.fusionQuanzhouBatchLoaded = true;
  const activityHash = '#/home/proofing';
  let activeJob;
  let previousJobId;
  let previousPanel;

  function onActivityPage() {
    return location.hash.split('?')[0] === activityHash;
  }

  function findUpload() {
    if (!onActivityPage()) return null;
    const inputs = Array.from(document.querySelectorAll('.ant-upload-btn input[type="file"][multiple]'));
    const matches = inputs.filter(input => {
      const accepted = input.accept.toLowerCase().split(',').map(value => value.trim());
      return accepted.includes('.step') && accepted.includes('.stp') && !input.closest('.ant-modal');
    });
    return matches.length === 1 ? matches[0] : null;
  }

  function isReady(input) {
    return input && !input.disabled && !input.closest('.ant-upload-disabled, [aria-disabled="true"]');
  }

  function confirmationDialog() {
    if (!onActivityPage()) return null;
    const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
    const matching = dialogs.filter(dialog => dialog.getClientRects().length &&
      (dialog.closest('.upload-confirm') || dialog.querySelector('.upload-confirm')) &&
      dialog.querySelector('.ant-modal-title')?.textContent.trim() === '确认上传文件');
    return matching.length === 1 ? matching[0] : null;
  }

  function confirmationButton(job) {
    const modal = confirmationDialog();
    if (!modal) return null;
    const expected = new Set(job.files.map(file => file.name));
    const rows = modal.querySelectorAll('.file-item-row');
    if (rows.length !== expected.size) return null;
    for (const row of rows) {
      const name = row.querySelector('.file-name')?.textContent.trim();
      const material = row.querySelector('.ant-select-selection-item')?.textContent.trim();
      if (!expected.delete(name) || material !== '铝合金6061') return null;
    }
    const buttons = Array.from(modal.querySelectorAll('.ant-modal-footer button'));
    return buttons.find(button => !button.disabled && button.getClientRects().length &&
      button.textContent.replace(/\s/g, '') === '确认上传');
  }

  function showStatus(count) {
    previousPanel?.remove();
    const host = document.createElement('aside');
    previousPanel = host;
    host.style.cssText = 'position:fixed;bottom:18px;left:18px;z-index:9999;width:min(330px,calc(100vw - 36px));';
    const root = host.attachShadow({ mode: 'closed' });
    root.innerHTML = `
      <style>
        section { font:14px/1.5 system-ui,sans-serif;color:#162d50;background:#fff;border:1px solid #becde3;
          padding:13px;border-radius:10px;box-shadow:0 5px 22px #18395a26; }
        p { margin:6px 0;overflow-wrap:anywhere; }
        button { font:inherit;color:#194b97;background:#f1f6ff;border:1px solid #bdd0eb;
          border-radius:5px;cursor:pointer;padding:2px 8px; }
      </style>
      <section aria-label="Fusion 文件导入"><strong></strong><p role="status"></p><button type="button">关闭</button></section>`;
    root.querySelector('strong').textContent = `Fusion 报价 · ${count} 件`;
    const status = root.querySelector('p');
    root.querySelector('button').addEventListener('click', () => host.remove());
    document.body.append(host);
    return message => {
      if (status.textContent !== message) status.textContent = message;
    };
  }

  function waitFor(find, timeout) {
    return new Promise((resolve, reject) => {
      const observer = new MutationObserver(check);
      const timer = setTimeout(() => finish(null), Math.max(0, timeout));
      window.addEventListener('hashchange', check);
      observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true });
      check();

      function cleanUp() {
        clearTimeout(timer);
        observer.disconnect();
        window.removeEventListener('hashchange', check);
      }
      function finish(value) {
        cleanUp();
        resolve(value);
      }
      function check() {
        try {
          const result = find();
          if (result) finish(result);
        } catch (error) {
          cleanUp();
          reject(error);
        }
      }
    });
  }

  function request(type, job, extra = {}) {
    return chrome.runtime.sendMessage({ type, id: job.id, ...extra });
  }

  async function deliver(job) {
    const setStatus = showStatus(job.files.length);
    const waitForUpload = () => waitFor(() => {
      const upload = findUpload();
      if (confirmationDialog()) {
        setStatus('正在等待网页中已有的上传确认结束。');
        return null;
      }
      if (isReady(upload)) return upload;
      setStatus(upload ? '等待登录或上传控件就绪。' : '正在等待 6.8 起打样页面。');
      return null;
    }, job.expires - Date.now());

    try {
      if (!await waitForUpload()) throw new Error('本批交接已超时。请登录后从 Fusion 重新发起。');
      const transfer = new DataTransfer();
      for (const [index, file] of job.files.entries()) {
        setStatus(`正在读取 STEP ${index + 1}/${job.files.length}…`);
        const response = await request('file', job, { index });
        if (!response?.base64) throw new Error(response?.error || `未读取到 ${file.name}。`);
        const bytes = Uint8Array.from(atob(response.base64), character => character.charCodeAt(0));
        if (bytes.length !== file.size) throw new Error(`文件 ${file.name} 传输不完整。`);
        transfer.items.add(new File([bytes], file.name, { type: 'application/octet-stream' }));
      }

      const upload = await waitForUpload();
      if (!upload) throw new Error('活动页的上传控件已失效。请重新发起报价。');
      const armed = await request('arm', job);
      if (!armed?.ok || upload !== findUpload() || !isReady(upload) || confirmationDialog()) {
        throw new Error('活动页在导入前发生变化。请重新发起报价。');
      }
      upload.files = transfer.files;
      upload.dispatchEvent(new Event('change', { bubbles: true }));

      if (!job.autoConfirm) {
        setStatus('这批 STEP 已导入。请在官网选择材质并确认。');
        return;
      }
      setStatus('正在核对本批文件及铝合金 6061。');
      const button = await waitFor(() => confirmationButton(job), 15000);
      if (!button) {
        setStatus('这批 STEP 已交给页面。请查看官网提示及确认框中的文件和材质。');
        return;
      }
      button.click();
      setStatus('已确认上传，等待官网解析和报价。');
    } catch (error) {
      setStatus(error.message || '自动导入失败，请从 Fusion 重新发起。');
    } finally {
      activeJob = undefined;
      await request('finish', job).catch(() => {});
    }
  }

  function start(job) {
    if (!job || activeJob || previousJobId === job.id || job.expires <= Date.now()) return;
    previousJobId = job.id;
    activeJob = job.id;
    deliver(job).catch(() => { activeJob = undefined; });
  }

  chrome.runtime.onMessage.addListener((message, sender, reply) => {
    if (sender.id === chrome.runtime.id && message.type === 'job') {
      start(message.job);
      reply({ ok: true });
    }
  });
  chrome.runtime.sendMessage({ type: 'ready' }).then(response => start(response?.job)).catch(() => {});
})();
