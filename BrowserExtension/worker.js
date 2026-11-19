const activityUrl = 'https://www.gy2025.com/#/home/proofing';
const officialOrigins = new Set(['https://www.gy2025.com', 'https://gy2025.com']);
const bridgePath = /^\/qz-fusion\/[A-Za-z0-9_-]{32}$/;
const maxBytes = 7 * 1024 * 1024;
const activeClaims = new Set();

function bridgeRequest(bridge, endpoint) {
  return fetch(`${bridge}/${endpoint}`, {
    headers: { 'X-Fusion-Bridge': '1' },
    redirect: 'error',
    credentials: 'omit',
    cache: 'no-store',
    signal: AbortSignal.timeout(8000),
  });
}

function encodeFile(bytes) {
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 8192) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  }
  return btoa(binary);
}

async function start(url, tabId, key) {
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port ||
      !bridgePath.test(url.pathname) || url.search || url.hash) return {};
  const bridge = `${url.origin}${url.pathname}`;
  const response = await bridgeRequest(bridge, 'info');
  if (!response.ok) return { error: 'Fusion 的文件交接已过期，请重新发起。' };

  const info = await response.json();
  if (info.activity !== activityUrl || typeof info.filename !== 'string' ||
      !/\.step$/i.test(info.filename) || !Number.isInteger(info.size) ||
      info.size <= 0 || !Number.isFinite(info.expires) || info.expires <= Date.now()) {
    return { error: '无法识别这次 STEP 导出，请手动上传。' };
  }
  if (info.claimed) return { error: '这个 STEP 已交接过一次，可以在活动页检查结果。' };
  if (info.size > maxBytes) return { error: '文件超出了活动页的 7 MiB STEP 上限。' };

  const job = {
    bridge, filename: info.filename, size: info.size, expires: info.expires,
    claimed: false, autoConfirm: info.autoConfirm === true,
  };
  await chrome.storage.session.set({ [key]: job });
  try {
    await chrome.tabs.update(tabId, { url: activityUrl });
  } catch (error) {
    await chrome.storage.session.remove(key);
    throw error;
  }
  return {};
}

async function claim(job, key) {
  if (job.claimed || activeClaims.has(key)) return { error: '这个 STEP 已开始交接，请检查官网或手动上传。' };
  if (Date.now() >= job.expires) return { error: '自动交接已过期。请从 Fusion 重新发起。' };

  activeClaims.add(key);
  try {
    const response = await bridgeRequest(job.bridge, 'step');
    if (!response.ok) return { error: '无法从 Fusion 读取此 STEP，可以改用手动上传。' };
    if (Number(response.headers.get('Content-Length')) !== job.size) {
      return { error: 'STEP 文件长度不匹配，可以改用手动上传。' };
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.length !== job.size) return { error: 'STEP 文件接收不完整，可以改用手动上传。' };
    await chrome.storage.session.set({ [key]: { ...job, claimed: true } });
    return { filename: job.filename, base64: encodeFile(bytes) };
  } finally {
    activeClaims.delete(key);
  }
}

async function handle(message, sender) {
  if (sender.frameId !== 0 || !Number.isInteger(sender.tab?.id) || !sender.url) return {};
  const tabId = sender.tab.id;
  const key = `quote-${tabId}`;
  const url = new URL(sender.url);
  if (message.type === 'start') return start(url, tabId, key);
  if (!officialOrigins.has(url.origin)) return {};

  const job = (await chrome.storage.session.get(key))[key];
  if (!job) return {};
  if (message.type === 'read') {
    return { job: {
      filename: job.filename, expires: job.expires, claimed: job.claimed,
      manual: job.manual, autoConfirm: job.autoConfirm,
    } };
  }
  if (message.type === 'fallback') {
    await chrome.storage.session.set({ [key]: { ...job, manual: true } });
    await chrome.tabs.create({ url: `${job.bridge}#manual` });
    return {};
  }
  if (message.type === 'claim' && !job.manual && url.hash.split('?')[0] === '#/home/proofing') return claim(job, key);
  return {};
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handle(message, sender).then(sendResponse).catch(() => {
    sendResponse({ error: 'Fusion 文件交接失败，可以改用手动上传。' });
  });
  return true;
});

chrome.tabs.onRemoved.addListener(tabId => {
  chrome.storage.session.remove(`quote-${tabId}`).catch(() => {});
});
