(() => {
  if (location.hash || !document.querySelector('meta[name="qz-fusion-bridge"][content="1"]')) return;
  const status = document.getElementById('status');
  chrome.runtime.sendMessage({ type: 'start' }).then(response => {
    if (response?.error) status.textContent = response.error;
  }).catch(() => {
    status.textContent = '自动跳转未完成，可以打开活动页并手动上传。';
  });
})();
