(() => {
  const activityHash = '#/home/proofing';

  function onActivityPage() {
    return location.hash.split('?')[0] === activityHash;
  }

  function findUpload() {
    if (!onActivityPage() || !document.title.includes('6.8起打样')) return null;
    const inputs = Array.from(document.querySelectorAll('.ant-upload-btn input[type="file"]'));
    const stepInputs = inputs.filter(input => {
      const accepted = input.accept.toLowerCase().split(',').map(value => value.trim());
      return accepted.includes('.step') && accepted.includes('.stp') && !input.closest('.ant-modal');
    });
    return stepInputs.length === 1 ? stepInputs[0] : null;
  }

  function isReady(input) {
    return input && !input.disabled && !input.closest('.ant-upload-disabled, [aria-disabled="true"]');
  }

  function confirmationButton(filename) {
    if (!onActivityPage()) return null;
    const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
    const matching = dialogs.filter(dialog => dialog.getClientRects().length &&
      (dialog.closest('.upload-confirm') || dialog.querySelector('.upload-confirm')) &&
      dialog.querySelector('.ant-modal-title')?.textContent.trim() === '确认上传文件');
    if (matching.length !== 1) return null;
    const rows = matching[0].querySelectorAll('.file-item-row');
    if (rows.length !== 1 || rows[0].querySelector('.file-name')?.textContent.trim() !== filename ||
        rows[0].querySelector('.ant-select-selection-item')?.textContent.trim() !== '铝合金6061') return null;
    const buttons = Array.from(matching[0].querySelectorAll('.ant-modal-footer button'));
    return buttons.find(button => !button.disabled && button.getClientRects().length &&
      button.textContent.replace(/\s/g, '') === '确认上传');
  }

  function confirmQuote(filename, setStatus) {
    setStatus('STEP 已交给活动页，正在检查 6061 报价确认。');
    const check = () => {
      const button = confirmationButton(filename);
      if (stopped || !button) return;
      observer.disconnect();
      clearTimeout(expiryTimer);
      button.click();
      setStatus('已确认上传，等待官网解析和报价。');
    };
    observer = new MutationObserver(check);
    observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true });
    expiryTimer = setTimeout(() => {
      observer.disconnect();
      setStatus('STEP 已交给活动页。请在官网检查文件，选择材质并确认上传。');
    }, 10000);
    check();
  }

  function showStatus(filename) {
    const host = document.createElement('aside');
    host.style.cssText = 'position:fixed;bottom:18px;left:18px;z-index:9999;width:min(350px,calc(100vw - 36px));';
    const root = host.attachShadow({ mode: 'closed' });
    root.innerHTML = `
      <style>
        section { font: 14px/1.5 system-ui,sans-serif; color:#172d50; background:#fff;
          border:1px solid #becde3; border-radius:10px; padding:14px; box-shadow:0 5px 22px #18395a26; }
        p { margin:5px 0 10px; overflow-wrap:anywhere; }
        button { cursor:pointer; font:inherit; margin-right:8px; padding:3px 8px;
          color:#194b97; background:#f1f6ff; border:1px solid #bdd0eb; border-radius:5px; }
      </style>
      <section aria-label="Fusion 文件导入"><strong></strong><p role="status"></p>
      <button type="button" id="manual">手动上传</button><button type="button" id="close">关闭提示</button></section>`;
    root.querySelector('strong').textContent = filename;
    const status = root.querySelector('p');
    root.querySelector('#manual').addEventListener('click', async () => {
      // Choosing manual upload cancels any pending automatic delivery in this page.
      stopped = true;
      observer?.disconnect();
      clearTimeout(expiryTimer);
      status.textContent = '从本地 STEP 页面复制文件路径后，在官网选择上传。';
      try {
        const result = await chrome.runtime.sendMessage({ type: 'fallback' });
        if (result?.error) status.textContent = result.error;
      } catch {
        status.textContent = '请在 Fusion 的本地导出目录找到该 STEP。';
      }
    });
    root.querySelector('#close').addEventListener('click', () => host.remove());
    document.body.append(host);
    return (message) => {
      if (status.textContent !== message) status.textContent = message;
    };
  }

  let stopped = false;
  let observer;
  let expiryTimer;

  async function watch() {
    // The extension does nothing on tabs that Fusion did not open.
    if (!onActivityPage()) return;
    const response = await chrome.runtime.sendMessage({ type: 'read' });
    if (!response?.job) return;
    const job = response.job;
    const setStatus = showStatus(job.filename);

    if (job.manual) {
      setStatus('已切换到手动上传。');
      return;
    }
    if (job.claimed) {
      setStatus('这个 STEP 已开始交接过一次。请检查活动页中的模型和报价。');
      return;
    }

    const checkUpload = async () => {
      if (stopped) return;
      const upload = findUpload();
      if (!isReady(upload)) {
        setStatus(upload ? '请先在官网登录。登录后会自动上传这个 STEP。' : '等待 6.8 起打样页面的上传控件。');
        return;
      }

      observer.disconnect();
      clearTimeout(expiryTimer);
      setStatus('正在从 Fusion 读取 STEP…');
      try {
        const fileData = await chrome.runtime.sendMessage({ type: 'claim' });
        if (stopped) return;
        if (!fileData?.base64) throw new Error(fileData?.error || '文件未传到浏览器，请手动上传。');
        const currentUpload = findUpload();
        if (!isReady(currentUpload)) throw new Error('活动页的上传控件已变化，请手动上传。');
        const bytes = Uint8Array.from(atob(fileData.base64), character => character.charCodeAt(0));
        const transfer = new DataTransfer();
        transfer.items.add(new File([bytes], fileData.filename, { type: 'application/octet-stream' }));
        currentUpload.files = transfer.files;
        currentUpload.dispatchEvent(new Event('change', { bubbles: true }));
        if (job.autoConfirm) {
          confirmQuote(fileData.filename, setStatus);
        } else {
          setStatus('STEP 已交给活动页。请在官网选择材质并确认上传。');
        }
      } catch (error) {
        setStatus(error.message || '自动导入失败，可以手动上传。');
      }
    };

    if (job.expires <= Date.now()) {
      setStatus('自动交接已过期。请从 Fusion 重新发起。');
      return;
    }
    expiryTimer = setTimeout(() => {
      stopped = true;
      observer.disconnect();
      setStatus('自动交接已过期。请从 Fusion 重新发起。');
    }, job.expires - Date.now());
    observer = new MutationObserver(checkUpload);
    observer.observe(document.documentElement, {
      subtree: true, childList: true, attributes: true,
      attributeFilter: ['class', 'disabled', 'aria-disabled'],
    });
    await checkUpload();
  }

  watch().catch(() => {});
})();
