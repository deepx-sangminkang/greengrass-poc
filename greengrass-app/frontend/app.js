const API_BASE = '/api';
let csrfToken = null;
let selectedCoreThingName = null;

const state = {
  activeStackName: null,
  thingGroupName: null,
  thingGroupArn: null,
  deviceTabs: [],
  activeDevice: null,
};

const DEVICE_LIST_POLL_MS = 10000;
let deviceListTimer = null;

function showJson(id, data) {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = JSON.stringify(data, null, 2);
  }
}

async function copyManualCommands() {
  const text = document.getElementById('manual-install-commands').textContent;
  try {
    await navigator.clipboard.writeText(text);
  } catch (error) {
    showJson('device-output', { error: `Copy failed: ${error.message}` });
  }
}

function showRequestError(id, error) {
  showJson(id, error.detail || { error: error.message });
}

function formatRequestError(data) {
  if (typeof data.detail === 'string') {
    return data.detail;
  }
  if (data.detail !== undefined) {
    return JSON.stringify(data.detail, null, 2);
  }
  if (typeof data.error === 'string') {
    return data.error;
  }
  if (typeof data.message === 'string') {
    return data.message;
  }
  if (data && Object.keys(data).length > 0) {
    return JSON.stringify(data, null, 2);
  }
  return 'Request failed';
}

async function requestJson(path, options = {}, retryOnCsrf = true) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };

  if (method !== 'GET') {
    headers['X-CSRF-Token'] = await getSessionToken();
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });
  const data = await response.json();
  if (!response.ok) {
    if (method !== 'GET' && retryOnCsrf && response.status === 403) {
      csrfToken = null;
      return requestJson(path, options, false);
    }
    const error = new Error(formatRequestError(data));
    error.detail = data.detail;
    throw error;
  }
  return data;
}

async function getSessionToken() {
  if (csrfToken) {
    return csrfToken;
  }

  const response = await fetch(`${API_BASE}/session`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || 'Session token request failed');
  }
  csrfToken = data.csrfToken;
  return csrfToken;
}

/* ===================== View navigation ===================== */

function showView(id) {
  document.querySelectorAll('.view').forEach((view) => {
    view.hidden = view.id !== id;
  });
  window.scrollTo(0, 0);
}

function goHome() {
  stopDeviceListPolling();
  showView('view-home');
}

function goToRegister() {
  stopDeviceListPolling();
  showView('view-register');
}

async function goToDevices() {
  showView('view-devices');
  await enterDevices();
  startDeviceListPolling();
}

function startDeviceListPolling() {
  stopDeviceListPolling();
  deviceListTimer = setInterval(() => {
    refreshDeviceList(true);
  }, DEVICE_LIST_POLL_MS);
}

function stopDeviceListPolling() {
  if (deviceListTimer !== null) {
    clearInterval(deviceListTimer);
    deviceListTimer = null;
  }
}

function enableNext() {
  const btn = document.getElementById('home-next-btn');
  if (btn) {
    btn.disabled = false;
  }
}

async function enterDevices() {
  try {
    const status = await requestJson('/setup/status');
    state.activeStackName = status.activeStackName || state.activeStackName;
    const outputs = (status.stack && status.stack.outputs) || {};
    state.thingGroupName = outputs.ThingGroupName || null;

    const groups = await loadThingGroups();
    const match = groups.find((group) => group.groupName === state.thingGroupName);
    state.thingGroupArn = match ? match.groupArn : '';

    const groupSelect = document.getElementById('component-thing-group');
    if (groupSelect) {
      groupSelect.value = state.thingGroupArn || '';
    }

    const ctxStack = document.getElementById('ctx-stack');
    if (ctxStack) {
      ctxStack.textContent = `Stack: ${state.activeStackName || '-'}`;
    }
    const ctxGroup = document.getElementById('ctx-thing-group');
    if (ctxGroup) {
      ctxGroup.textContent = `Thing Group: ${state.thingGroupName || '(not set)'}`;
    }
    const groupName = document.getElementById('devices-thing-group-name');
    if (groupName) {
      groupName.textContent = state.thingGroupName || '(ThingGroupName not found in stack outputs)';
    }
    const contextBar = document.getElementById('context-bar');
    if (contextBar) {
      contextBar.hidden = false;
    }

    await refreshDeviceList();
  } catch (error) {
    showRequestError('component-output', error);
  }
}

/* ===================== Step 1: CloudFormation ===================== */

async function loadSetupStatus() {
  try {
    const data = await requestJson('/setup/status');
    showJson('setup-output', data);
    if (data.activeStackName) {
      document.getElementById('stack-name').value = data.activeStackName;
    }
    if (data.stack && data.stack.status && data.stack.status !== 'NOT_FOUND') {
      state.activeStackName = data.activeStackName;
      enableNext();
    }
  } catch (error) {
    showRequestError('setup-output', error);
  }
}

async function loadStacks() {
  try {
    const data = await requestJson('/setup/stacks');
    const select = document.getElementById('stack-list');
    select.innerHTML = '<option value="">Select existing stack...</option>';
    (data.stacks || []).forEach((stack) => {
      const option = document.createElement('option');
      option.value = stack.name;
      const tag = stack.managed ? '★ ' : '';
      option.textContent = `${tag}${stack.name} (${stack.status})`;
      select.appendChild(option);
    });
    showJson('setup-output', data);
  } catch (error) {
    showRequestError('setup-output', error);
  }
}

async function selectStack() {
  const stackName = document.getElementById('stack-list').value || document.getElementById('stack-name').value;
  if (!stackName) {
    showJson('setup-output', { error: 'Please select or enter a stack name.' });
    return;
  }
  try {
    const data = await requestJson('/setup/select', {
      method: 'POST',
      body: JSON.stringify({ stack_name: stackName }),
    });
    document.getElementById('stack-name').value = data.activeStackName || stackName;
    state.activeStackName = data.activeStackName || stackName;
    enableNext();
    showJson('setup-output', data);
  } catch (error) {
    showRequestError('setup-output', error);
  }
}

async function deploySetupStack() {
  const stackName = document.getElementById('stack-name').value;
  try {
    showJson('setup-output', { status: 'running', message: 'Creating/updating CloudFormation stack.' });
    const data = await requestJson('/setup/deploy', {
      method: 'POST',
      body: JSON.stringify({ stack_name: stackName }),
    });
    state.activeStackName = data.stackName || stackName;
    enableNext();
    showJson('setup-output', data);
  } catch (error) {
    showRequestError('setup-output', error);
  }
}

/* ===================== Core device registration ===================== */

async function generateInstallScript() {
  const body = {
    thing_name: document.getElementById('thing-name').value,
  };
  try {
    showJson('device-output', await requestJson('/devices/install-script', {
      method: 'POST',
      body: JSON.stringify(body),
    }));
  } catch (error) {
    showJson('device-output', { error: error.message });
  }
}

async function runSshInstall() {
  const sshPort = document.getElementById('ssh-port').value;
  const body = {
    thing_name: document.getElementById('thing-name').value,
    host: document.getElementById('ssh-host').value,
    username: document.getElementById('ssh-user').value,
    port: sshPort ? Number(sshPort) : 22,
    password: document.getElementById('ssh-password').value || null,
    private_key_path: document.getElementById('ssh-key').value || null,
  };
  const output = document.getElementById('device-output');
  output.textContent = 'Starting SSH installation...\n';
  try {
    const response = await fetch(`${API_BASE}/devices/ssh-install-stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': await getSessionToken(),
      },
      body: JSON.stringify(body),
    });
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => ({}));
      output.textContent = formatRequestError(data);
      return;
    }
    output.textContent = '';
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      output.textContent += decoder.decode(value, { stream: true });
      output.scrollTop = output.scrollHeight;
    }
    output.textContent += decoder.decode();
  } catch (error) {
    output.textContent += `\n[Error] ${error.message}`;
  }
}

async function loadCoreDevices() {
  try {
    const data = await requestJson('/devices/cores');
    renderCoreDevices(data.devices || []);
    showJson('device-output', data);
  } catch (error) {
    showJson('device-output', { error: error.message });
  }
}

function renderCoreDevices(devices) {
  const list = document.getElementById('core-device-list');
  if (!list) {
    return;
  }
  if (devices.length === 0) {
    list.innerHTML = '<p>No core devices found.</p>';
    return;
  }

  list.innerHTML = devices.map((device) => {
    const name = device.coreDeviceThingName || '';
    const status = device.status || '-';
    return `
      <div class="core-device-item">
        <span>${name} (${status})</span>
        <button onclick="selectCoreDevice('${name}')">Select</button>
      </div>
    `;
  }).join('');
}

function selectCoreDevice(coreThingName) {
  selectedCoreThingName = coreThingName;
  const output = document.getElementById('selected-core-output');
  if (output) {
    output.textContent = `Selected core device: ${coreThingName}`;
  }
}

/* ===================== Devices view: list + thing group ===================== */

async function loadThingGroups() {
  try {
    const data = await requestJson('/thing-groups');
    const groups = data.thingGroups || [];
    const options = ['<option value="">(All)</option>']
      .concat(groups.map((group) =>
        `<option value="${group.groupArn}">${group.groupName}</option>`))
      .join('');
    const select = document.getElementById('component-thing-group');
    if (select) {
      select.innerHTML = options;
    }
    return groups;
  } catch (error) {
    showJson('component-output', { error: error.message });
    return [];
  }
}

async function refreshDeviceList(silent = false) {
  const list = document.getElementById('device-list');
  if (list && !silent) {
    list.innerHTML = '<p>Loading...</p>';
  }
  try {
    const groupArn = document.getElementById('component-thing-group').value;
    const query = groupArn ? `?thing_group_arn=${encodeURIComponent(groupArn)}` : '';
    const data = await requestJson(`/devices/cores${query}`);
    renderDeviceList(data.devices || []);
  } catch (error) {
    if (list && !silent) {
      list.innerHTML = '';
    }
    if (!silent) {
      showRequestError('component-output', error);
    }
  }
}

function renderDeviceList(devices) {
  const list = document.getElementById('device-list');
  if (!list) {
    return;
  }
  if (devices.length === 0) {
    list.innerHTML = '<p>No core devices registered in this Thing Group. Add one via "Register Core device". (Auto-refreshes every 10 seconds — devices appear once they connect to IoT Core.)</p>';
    return;
  }
  list.innerHTML = devices.map((device) => {
    const name = device.coreDeviceThingName || '';
    const status = device.status || '-';
    return `
      <div class="core-device-item">
        <span>${name} (${status})</span>
        <button onclick="openDeviceTab('${name}')">Open</button>
      </div>
    `;
  }).join('');
}

// All devices installation status (group-wide summary)
async function loadDeviceComponents() {
  const container = document.getElementById('device-components');
  if (container) {
    container.innerHTML = '<p>Loading...</p>';
  }
  try {
    const groupArn = document.getElementById('component-thing-group').value;
    const query = groupArn ? `?thing_group_arn=${encodeURIComponent(groupArn)}` : '';
    const data = await requestJson(`/devices/cores${query}`);
    const devices = data.devices || [];
    const cards = await Promise.all(devices.map(async (device) => {
      const name = device.coreDeviceThingName || '';
      let components = [];
      try {
        const result = await requestJson(`/devices/${encodeURIComponent(name)}/components`);
        components = result.components || [];
      } catch (error) {
        components = [];
      }
      const rows = components.map((component) =>
        `<li>${component.componentName} ${component.componentVersion || ''} - ${component.lifecycleState || '-'}</li>`).join('');
      return `
        <div class="core-device-item">
          <strong>${name} (${device.status || '-'})</strong>
          <ul>${rows || '<li>No components installed.</li>'}</ul>
        </div>
      `;
    }));
    if (container) {
      container.innerHTML = cards.join('') || '<p>No core devices found.</p>';
    }
  } catch (error) {
    if (container) {
      container.innerHTML = '';
    }
    showJson('component-output', { error: error.message });
  }
}

/* ===================== Per-device tabs ===================== */

function openDeviceTab(name) {
  if (!state.deviceTabs.includes(name)) {
    state.deviceTabs.push(name);
  }
  activateDevice(name);
}

function closeDeviceTab(name) {
  state.deviceTabs = state.deviceTabs.filter((tab) => tab !== name);
  if (state.activeDevice === name) {
    state.activeDevice = state.deviceTabs[state.deviceTabs.length - 1] || null;
  }
  renderTabBar();
  if (state.activeDevice) {
    activateDevice(state.activeDevice);
  } else {
    const panel = document.getElementById('device-panel');
    if (panel) {
      panel.hidden = true;
    }
  }
}

function renderTabBar() {
  const bar = document.getElementById('device-tabbar');
  if (!bar) {
    return;
  }
  bar.innerHTML = state.deviceTabs.map((name) => {
    const active = name === state.activeDevice ? ' active' : '';
    return `
      <div class="tab${active}">
        <span class="tab-label" onclick="activateDevice('${name}')">${name}</span>
        <button class="tab-close" onclick="closeDeviceTab('${name}')">×</button>
      </div>
    `;
  }).join('');
}

function activateDevice(name) {
  state.activeDevice = name;
  renderTabBar();

  const panel = document.getElementById('device-panel');
  if (panel) {
    panel.hidden = false;
  }
  const title = document.getElementById('device-panel-title');
  if (title) {
    title.textContent = `Core Device: ${name}`;
  }

  const components = document.getElementById('active-device-components');
  if (components) {
    components.innerHTML = '';
  }

  loadActiveDeviceComponents();
}

async function loadActiveDeviceComponents() {
  const container = document.getElementById('active-device-components');
  if (!state.activeDevice) {
    return;
  }
  if (container) {
    container.innerHTML = '<p>Loading...</p>';
  }
  try {
    const result = await requestJson(`/devices/${encodeURIComponent(state.activeDevice)}/components`);
    const components = result.components || [];
    const rows = components.map((component) =>
      `<li>${component.componentName} ${component.componentVersion || ''} - ${component.lifecycleState || '-'}</li>`).join('');
    if (container) {
      container.innerHTML = `<ul>${rows || '<li>No components installed.</li>'}</ul>`;
    }
  } catch (error) {
    if (container) {
      container.innerHTML = `<p class="warn">${error.message}</p>`;
    }
  }
}

loadSetupStatus();
