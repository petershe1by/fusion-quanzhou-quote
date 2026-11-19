const activityUrl = 'https://www.gy2025.com/#/home/proofing';
const activityHash = '#/home/proofing';
const officialOrigins = new Set(['https://www.gy2025.com', 'https://gy2025.com']);
const tabPatterns = ['https://www.gy2025.com/*', 'https://gy2025.com/*'];
const bridgeUrl = 'http://127.0.0.1:49167/qz-batch';
const alarmName = 'quanzhou-check-bridge';
const maxBytes = 7 * 1024 * 1024;
let clientPromise;
let polling = false;
let wakeRequested = false;
let finishing = false;

function siteUrl(url) {
  try {
    const parsed = new URL(url);
    return officialOrigins.has(parsed.origin) ? parsed : null;
  } catch {
    return null;
  }
}

function isActivity(url) {
  return siteUrl(url)?.hash.split('?')[0] === activityHash;
}

function getClientId() {
  if (!clientPromise) {
    clientPromise = (async () => {
      const saved = (await chrome.storage.session.get('clientId')).clientId;
      if (saved) return saved;
      const clientId = crypto.randomUUID();
      await chrome.storage.session.set({ clientId });
      return clientId;
    })();
    clientPromise.catch(() => { clientPromise = undefined; });
  }
  return clientPromise;
}

async function bridgeRequest(endpoint, data = {}) {
  return fetch(`${bridgeUrl}/${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Fusion-Bridge': '2' },
    body: JSON.stringify({ client: await getClientId(), ...data }),
    redirect: 'error', credentials: 'omit', cache: 'no-store',
    signal: AbortSignal.timeout(22000),
  });
}

function validJob(job) {
  if (!/^[A-Za-z0-9_-]{32}$/.test(job?.id || '') || !Number.isFinite(job.expires) ||
      job.expires <= Date.now() || !Array.isArray(job.files) || job.files.length < 1 || job.files.length > 100) return false;
  const names = new Set();
  for (const file of job.files) {
    if (typeof file?.name !== 'string' || !/^[^/\\]+\.step$/i.test(file.name) ||
        !Number.isInteger(file.size) || file.size < 1 || file.size > maxBytes || names.has(file.name.toLowerCase())) return false;
    names.add(file.name.toLowerCase());
  }
  return true;
}

function encodeFile(bytes) {
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 8192) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  }
  return btoa(binary);
}

async function pushJob(tabId, job) {
  const message = { type: 'job', job };
  try {
    await chrome.tabs.sendMessage(tabId, message);
    return;
  } catch {
    // An already-open page can predate the extension reload.
  }
  try {
    const tab = await chrome.tabs.get(tabId);
    if (!isActivity(tab.pendingUrl || tab.url) || tab.status === 'loading') return;
    await chrome.scripting.executeScript({ target: { tabId }, files: ['activity.js'] });
    await chrome.tabs.sendMessage(tabId, message);
  } catch {
    // A navigation can race with injection. The installed content script announces readiness.
  }
}

async function openActivity(job) {
  const tabs = await chrome.tabs.query({ url: tabPatterns });
  tabs.sort((a, b) => Number(b.active) - Number(a.active) || (b.lastAccessed || 0) - (a.lastAccessed || 0));
  let tab = tabs.find(candidate => isActivity(candidate.pendingUrl || candidate.url)) || tabs[0];
  if (tab) {
    await chrome.storage.session.set({ current: { job, tabId: tab.id } });
    const url = tab.pendingUrl || tab.url;
    tab = await chrome.tabs.update(tab.id, isActivity(url) ? { active: true } : { active: true, url: activityUrl });
  } else {
    tab = await chrome.tabs.create({ url: activityUrl, active: true });
    await chrome.storage.session.set({ current: { job, tabId: tab.id } });
  }
  await chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
  await pushJob(tab.id, job);
}

async function finishCurrent(current) {
  if (finishing) return;
  finishing = true;
  try {
    // Remember the terminal state until the bridge has acknowledged it.
    await chrome.storage.session.set({ current: { ...current, finished: true } });
    const response = await bridgeRequest('finish', { id: current.job.id });
    if (response.ok || response.status === 404) await chrome.storage.session.remove('current');
  } catch {
    // Retry from the persisted terminal state when the worker next wakes.
  } finally {
    finishing = false;
  }
}

async function poll() {
  let { current } = await chrome.storage.session.get('current');
  if (current) {
    if (current.finished || current.job.expires <= Date.now() ||
        (current.armed && current.armed < Date.now() - 45000)) {
      await finishCurrent(current);
    } else if (!current.armed) {
      const tab = await chrome.tabs.get(current.tabId).catch(() => null);
      if (!tab) await finishCurrent(current);
      else if (isActivity(tab.pendingUrl || tab.url)) await pushJob(current.tabId, current.job);
    }
    ({ current } = await chrome.storage.session.get('current'));
    if (current) return;
  }

  while (true) {
    const response = await bridgeRequest('next');
    if (response.status === 204) {
      // The local wait is shorter than the MV3 idle interval. An extension API call
      // between waits resets that interval while Fusion is connected.
      await chrome.storage.session.get('clientId');
      continue;
    }
    if (!response.ok) return;
    const job = await response.json();
    if (!validJob(job)) return;
    await openActivity(job);
    return;
  }
}

function wake() {
  if (polling) {
    wakeRequested = true;
    return;
  }
  polling = true;
  poll().catch(() => {}).finally(() => {
    polling = false;
    if (!wakeRequested) return;
    wakeRequested = false;
    wake();
  });
}

async function handle(message, sender) {
  const tabId = sender.tab?.id;
  if (!Number.isInteger(tabId) || sender.frameId !== 0 || !siteUrl(sender.url)) return {};
  const { current } = await chrome.storage.session.get('current');
  if (message.type === 'ready') {
    wake();
    if (current?.tabId === tabId && !current.armed && !current.finished) return { job: current.job };
    return {};
  }
  if (current?.tabId !== tabId || current.job.id !== message.id || current.finished) return {};
  if (message.type === 'finish') {
    await finishCurrent(current);
    wake();
    return {};
  }
  if (current.armed || !isActivity((await chrome.tabs.get(tabId)).url) || current.job.expires <= Date.now()) return {};
  if (message.type === 'arm') {
    await chrome.storage.session.set({ current: { ...current, armed: Date.now() } });
    return { ok: true };
  }
  if (message.type !== 'file' || !Number.isInteger(message.index)) return {};
  const file = current.job.files[message.index];
  if (!file) return {};
  const response = await bridgeRequest('file', { id: current.job.id, index: message.index });
  if (!response.ok || Number(response.headers.get('Content-Length')) !== file.size) {
    return { error: `无法读取 ${file.name}，请重新发起这批报价。` };
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.length !== file.size) return { error: `文件 ${file.name} 传输不完整。` };
  return { base64: encodeFile(bytes) };
}

async function installAlarm() {
  if (!await chrome.alarms.get(alarmName)) await chrome.alarms.create(alarmName, { periodInMinutes: 0.5 });
  wake();
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handle(message, sender).then(sendResponse).catch(() => sendResponse({ error: 'Fusion 文件交接失败。' }));
  return true;
});
chrome.runtime.onInstalled.addListener(() => { installAlarm().catch(() => {}); });
chrome.runtime.onStartup.addListener(() => { installAlarm().catch(() => {}); });
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === alarmName) wake(); });
chrome.tabs.onRemoved.addListener(tabId => {
  chrome.storage.session.get('current').then(async ({ current }) => {
    if (current?.tabId === tabId) await finishCurrent(current);
    wake();
  }).catch(() => {});
});
