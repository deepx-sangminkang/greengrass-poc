const API_BASE = '/api';
const POLL_INTERVAL_MS = 5000;

// ───────────────────────── shared helpers ─────────────────────────
// ponytail: no CSRF — this is a localhost dev console (matches backend contract).

async function requestJson(path, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const headers = { ...(options.headers || {}) };
    if (method !== 'GET' && options.body) headers['Content-Type'] = 'application/json';
    const resp = await fetch(`${API_BASE}${path}`, { ...options, headers });
    const data = await resp.json();
    if (!resp.ok) {
        const err = new Error(formatRequestError(data));
        err.detail = data.detail;
        throw err;
    }
    return data;
}

function formatRequestError(data) {
    if (!data) return 'Request failed';
    if (typeof data.detail === 'string') return data.detail;
    if (data.detail !== undefined) return JSON.stringify(data.detail, null, 2);
    if (typeof data.error === 'string') return data.error;
    if (typeof data.message === 'string') return data.message;
    return Object.keys(data).length ? JSON.stringify(data, null, 2) : 'Request failed';
}

function showJson(id, data) {
    const el = document.getElementById(id);
    if (el) el.textContent = JSON.stringify(data, null, 2);
}

function showRequestError(id, error) {
    showJson(id, error.detail || { error: error.message });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ───────────────────────── tab / gating ─────────────────────────

const VIEWS = { deploy: 'view-deploy', compile: 'view-compile', edge: 'view-edge' };
let stackReady = false;

function switchTab(name) {
    const btn = document.getElementById(`tab-${name}`);
    if (btn && btn.disabled) return;
    Object.entries(VIEWS).forEach(([key, viewId]) => {
        document.getElementById(viewId).hidden = key !== name;
        document.getElementById(`tab-${key}`).classList.toggle('active', key === name);
    });
    if (name === 'edge') enterEdge();
    else stopDeviceListPolling();
}

async function refreshSetupStatus() {
    try {
        const data = await requestJson('/setup/status');

        document.getElementById('ctx-region').textContent = data.region || '-';
        document.getElementById('ctx-stack').textContent =
            (data.stack && data.stack.name) || data.activeStackName || '(none)';

        // Marketplace chip + deploy-tab box
        const mk = data.marketplace || {};
        const subChip = document.getElementById('ctx-sub');
        const subText = document.getElementById('ctx-sub-text');
        const mkStatus = document.getElementById('marketplace-status');
        const mkLink = document.getElementById('marketplace-link');
        if (mkLink) mkLink.href = mk.subscribe_url || '#';
        if (mk.subscribed) {
            subText.textContent = `subscribed (${mk.ami_id || 'ok'})`;
            subChip.className = 'ctx-chip sub-ok';
            if (mkStatus) mkStatus.textContent = `✅ Subscribed. AMI: ${mk.ami_id || ''}`;
            if (mkLink) mkLink.classList.add('hidden');
        } else {
            subText.textContent = 'not subscribed';
            subChip.className = 'ctx-chip sub-no';
            if (mkStatus) mkStatus.textContent = '⚠️ Not subscribed (Ubuntu AMI works without subscription).';
            if (mkLink) mkLink.classList.remove('hidden');
        }

        // AMI options for deploy form
        if (data.ami_options) {
            const amiSel = document.getElementById('f-ami');
            if (amiSel) amiSel.innerHTML = data.ami_options.map(o =>
                `<option value="${o.key}">${o.label}${o.requires_subscription ? ' (subscription required)' : ''}</option>`).join('');
        }

        // Gate compile/edge tabs
        stackReady = !!(data.stack && data.stack.ready);
        document.getElementById('tab-compile').disabled = !stackReady;
        document.getElementById('tab-edge').disabled = !stackReady;

        if (stackReady && data.stack.outputs) renderOutputs(data.stack.outputs);
        return data;
    } catch (err) {
        console.error('setup status error:', err);
        return null;
    }
}

// ───────────────────────── DEPLOY tab ─────────────────────────

async function loadNetwork() {
    try {
        const data = await requestJson('/setup/network/vpcs');
        const vpcSel = document.getElementById('f-vpc');
        vpcSel.innerHTML = (data.vpcs || []).map(v =>
            `<option value="${v.id}">${v.name || v.id}${v.isDefault ? ' (default)' : ''} — ${v.cidr}</option>`).join('');
        const defaultVpc = (data.default && data.default.VpcId)
            || (data.vpcs.find(v => v.isDefault) || {}).id
            || (data.vpcs[0] || {}).id;
        if (defaultVpc) {
            vpcSel.value = defaultVpc;
            await loadSubnets(defaultVpc, data.default && data.default.SubnetId);
        }
    } catch (err) {
        console.error('loadNetwork error:', err);
    }
}

async function loadSubnets(vpcId, preselect) {
    const subSel = document.getElementById('f-subnet');
    if (!vpcId) { subSel.innerHTML = '<option value="">Select a VPC first</option>'; return; }
    subSel.innerHTML = '<option value="">Loading…</option>';
    try {
        const data = await requestJson(`/setup/network/subnets?vpc_id=${encodeURIComponent(vpcId)}`);
        subSel.innerHTML = (data.subnets || []).map(s =>
            `<option value="${s.id}">${s.name || s.id} — ${s.az} — ${s.cidr}</option>`).join('')
            || '<option value="">(no subnets)</option>';
        if (preselect) subSel.value = preselect;
    } catch (err) {
        subSel.innerHTML = '<option value="">(failed to load)</option>';
    }
}

let stackRefreshTimer = null;

async function loadExistingStacks() {
    const container = document.getElementById('existing-stacks');
    const list = document.getElementById('stacks-list');
    const empty = document.getElementById('no-stacks');
    try {
        const data = await requestJson('/setup/stacks');
        if (!data.stacks || data.stacks.length === 0) {
            container.classList.add('hidden');
            empty.classList.remove('hidden');
            stopStackRefresh();
            return;
        }
        empty.classList.add('hidden');
        container.classList.remove('hidden');
        let hasInProgress = false;
        list.innerHTML = data.stacks.map(s => {
            const isActive = s.name === data.active;
            const statusIcon = s.status.includes('COMPLETE') ? '✅'
                : s.status.includes('PROGRESS') ? '🔄'
                : s.status.includes('DELETE') ? '🗑️' : '⚠️';
            const bucket = (s.outputs && s.outputs.ModelBucketName) || '-';
            if (s.status.includes('PROGRESS')) hasInProgress = true;
            const delDisabled = s.status.includes('PROGRESS') || s.status.includes('DELETE') ? 'disabled' : '';
            return `
                <div class="stack-item ${isActive ? 'active' : ''}">
                    <div class="stack-item-header" onclick="selectStack('${s.name}')">
                        <strong>${s.name}</strong>
                        <span class="stack-item-status">${statusIcon} ${s.status}</span>
                    </div>
                    <div class="stack-item-detail">
                        <span>📦 ${bucket}</span>
                        <span>📅 ${s.created ? new Date(s.created).toLocaleDateString() : '-'}</span>
                        <button class="btn-delete" ${delDisabled} onclick="event.stopPropagation(); deleteStack('${s.name}')">🗑️ Delete</button>
                    </div>
                    ${isActive ? '<span class="stack-active-badge">active</span>' : ''}
                </div>`;
        }).join('');
        if (hasInProgress) startStackRefresh(); else stopStackRefresh();
    } catch (err) {
        console.error('loadExistingStacks error:', err);
    }
}

function startStackRefresh() { if (!stackRefreshTimer) stackRefreshTimer = setInterval(loadExistingStacks, 5000); }
function stopStackRefresh() { if (stackRefreshTimer) { clearInterval(stackRefreshTimer); stackRefreshTimer = null; } }

async function selectStack(stackName) {
    try {
        await requestJson('/setup/stacks/select', { method: 'POST', body: JSON.stringify({ stack_name: stackName }) });
        await refreshSetupStatus();
        await loadExistingStacks();
    } catch (err) {
        alert(`Select failed: ${err.message}`);
    }
}

async function deleteStack(stackName) {
    if (!confirm(`Delete stack '${stackName}'? All stack resources will be removed.`)) return;
    try {
        const resp = await fetch(`${API_BASE}/setup/stacks/${encodeURIComponent(stackName)}`, { method: 'DELETE' });
        if (!resp.ok) throw new Error(formatRequestError(await resp.json().catch(() => ({}))));
        startStackRefresh();
        loadExistingStacks();
    } catch (err) {
        alert(`Delete failed: ${err.message}`);
    }
}

async function validateTemplate() {
    const box = document.getElementById('validate-result');
    box.className = 'validate-result';
    box.classList.remove('hidden');
    box.textContent = 'Validating…';
    try {
        const amiType = document.getElementById('f-ami').value || 'marketplace';
        const data = await requestJson('/setup/validate', { method: 'POST', body: JSON.stringify({ ami_type: amiType }) });
        if (data.valid) {
            box.className = 'validate-result ok';
            const params = (data.parameters || []).map(p =>
                `${p.key}${p.default ? ` = ${p.default}` : ''}${p.noEcho ? ' (noEcho)' : ''}`).join('\n');
            box.textContent = `✅ Template valid.\nCapabilities: ${(data.capabilities || []).join(', ') || 'none'}\n\nParameters:\n${params}`;
        } else {
            box.className = 'validate-result err';
            box.textContent = `❌ Invalid: ${data.error || 'unknown error'}`;
        }
    } catch (err) {
        box.className = 'validate-result err';
        box.textContent = `❌ ${err.message}`;
    }
}

async function toggleTemplate() {
    const viewer = document.getElementById('cf-template-viewer');
    const content = document.getElementById('cf-template-content');
    if (!viewer.classList.contains('hidden')) { viewer.classList.add('hidden'); return; }
    content.textContent = 'Loading…';
    viewer.classList.remove('hidden');
    try {
        const resp = await fetch(`${API_BASE}/setup/template`);
        content.textContent = await resp.text();
    } catch (err) {
        content.textContent = 'Failed to load template: ' + err.message;
    }
}

let deployPollTimer = null;

async function deployStack() {
    const bucket = document.getElementById('f-bucket').value.trim();
    const vpc = document.getElementById('f-vpc').value;
    const subnet = document.getElementById('f-subnet').value;
    if (!bucket) { alert('ModelBucketName is required.'); return; }
    if (!vpc) { alert('VPC is required.'); return; }
    if (!subnet) { alert('Subnet is required.'); return; }

    const body = {
        vpc_id: vpc,
        subnet_id: subnet,
        model_bucket_name: bucket,
        instance_type: document.getElementById('f-instance').value,
        ami_type: document.getElementById('f-ami').value || 'marketplace',
    };
    const stackName = document.getElementById('f-stack-name').value.trim();
    if (stackName) body.stack_name = stackName;
    const thingGroup = document.getElementById('f-thing-group').value.trim();
    if (thingGroup) body.thing_group_name = thingGroup;

    const deployBtn = document.getElementById('deploy-btn');
    const statusEl = document.getElementById('deploy-status');
    deployBtn.disabled = true;
    deployBtn.textContent = '🔄 Deploying…';
    try {
        const data = await requestJson('/setup/deploy', { method: 'POST', body: JSON.stringify(body) });
        statusEl.textContent = `🔄 Deploying ${data.stack_name || ''} …`;
        document.getElementById('deploy-events').classList.remove('hidden');
        startDeployPolling();
        loadExistingStacks();
    } catch (err) {
        deployBtn.disabled = false;
        deployBtn.textContent = '🚀 Deploy';
        alert(`Deploy failed: ${err.message}`);
    }
}

function startDeployPolling() {
    if (deployPollTimer) clearInterval(deployPollTimer);
    deployPollTimer = setInterval(pollDeployStatus, POLL_INTERVAL_MS);
    pollDeployStatus();
}

async function pollDeployStatus() {
    try {
        const data = await requestJson('/setup/deploy/status');
        const statusEl = document.getElementById('deploy-status');
        const events = document.getElementById('deploy-events');
        events.classList.remove('hidden');
        if (data.events && data.events.length) {
            events.innerHTML = data.events.map(e => {
                const icon = e.status.includes('COMPLETE') ? '✅'
                    : e.status.includes('PROGRESS') ? '🔄'
                    : e.status.includes('FAILED') ? '❌' : '▶️';
                return `<span class="log-entry">${icon} ${e.resource}: ${e.status}${e.reason ? ' - ' + e.reason : ''}</span>`;
            }).join('');
            events.scrollTop = events.scrollHeight;
        }
        statusEl.textContent = `Status: ${data.stack_status}`;

        const deployBtn = document.getElementById('deploy-btn');
        if (data.ready) {
            clearInterval(deployPollTimer); deployPollTimer = null;
            deployBtn.disabled = false;
            deployBtn.textContent = '🚀 Deploy';
            statusEl.textContent = '✅ Stack ready.';
            if (data.outputs) renderOutputs(data.outputs);
            await refreshSetupStatus();
            loadExistingStacks();
        } else if (data.stack_status && (data.stack_status.includes('FAILED') || data.stack_status.includes('ROLLBACK'))) {
            clearInterval(deployPollTimer); deployPollTimer = null;
            deployBtn.disabled = false;
            deployBtn.textContent = '🚀 Retry deploy';
            statusEl.textContent = '❌ Deploy failed.';
        }
    } catch (err) {
        console.error('deploy poll error:', err);
    }
}

function renderOutputs(outputs) {
    const box = document.getElementById('deploy-outputs');
    const body = document.getElementById('outputs-body');
    const entries = Object.entries(outputs || {});
    if (!entries.length) { box.classList.add('hidden'); return; }
    body.innerHTML = entries.map(([k, v]) =>
        `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(String(v))}</td></tr>`).join('');
    box.classList.remove('hidden');
}

// ───────────────────────── COMPILE tab (verbatim from compiler-app) ─────────────────────────

let currentJobId = null;
let pollTimer = null;

const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const fileBtn = document.getElementById('file-btn');
const uploadSection = document.getElementById('upload-section');
const progressSection = document.getElementById('progress-section');
const errorSection = document.getElementById('error-section');
const jobFilename = document.getElementById('job-filename');
const jobStatusBadge = document.getElementById('job-status-badge');
const progressMessage = document.getElementById('progress-message');
const downloadBtn = document.getElementById('download-btn');
const errorMessage = document.getElementById('error-message');
const linksSection = document.getElementById('links-section');
const linksList = document.getElementById('links-list');
const logsSection = document.getElementById('logs-section');
const logsList = document.getElementById('logs-list');

let selectedOnnxFile = null;
let selectedJsonFile = null;
let elapsedTimer = null;
let elapsedStartTime = null;

fileBtn.addEventListener('click', (e) => { e.stopPropagation(); fileInput.click(); });
dropZone.addEventListener('click', (e) => {
    if (e.target.id === 'upload-btn' || e.target.id === 'file-btn') return;
    fileInput.click();
});
fileInput.addEventListener('change', (e) => { if (e.target.files.length) handleFiles(Array.from(e.target.files)); });
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => {
    e.preventDefault(); dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length) handleFiles(Array.from(e.dataTransfer.files));
});

function handleFiles(files) {
    for (const file of files) {
        const name = file.name.toLowerCase();
        if (name.endsWith('.onnx')) selectedOnnxFile = file;
        else if (name.endsWith('.json')) selectedJsonFile = file;
    }
    updateSelectedFilesUI();
}

function updateSelectedFilesUI() {
    const container = document.getElementById('selected-files');
    const onnxEl = document.getElementById('selected-onnx');
    const jsonEl = document.getElementById('selected-json');
    const uploadBtn = document.getElementById('upload-btn');
    container.classList.remove('hidden');
    onnxEl.textContent = selectedOnnxFile ? `✅ ONNX: ${selectedOnnxFile.name}` : '⬜ Select an ONNX file';
    onnxEl.className = selectedOnnxFile ? 'selected-file ready' : 'selected-file';
    jsonEl.textContent = selectedJsonFile ? `✅ JSON: ${selectedJsonFile.name}` : '⬜ Select a JSON config file';
    jsonEl.className = selectedJsonFile ? 'selected-file ready' : 'selected-file';
    if (selectedOnnxFile && selectedJsonFile) {
        uploadBtn.classList.remove('hidden');
        uploadBtn.onclick = () => startCompile();
    } else {
        uploadBtn.classList.add('hidden');
    }
}

async function startCompile() {
    if (selectedOnnxFile.size > 500 * 1024 * 1024) { showError('ONNX file exceeds 500MB.'); return; }
    if (selectedJsonFile.size > 10 * 1024 * 1024) { showError('JSON file exceeds 10MB.'); return; }
    showProgress(selectedOnnxFile.name);
    updateStep('upload');
    progressMessage.textContent = 'Uploading files…';
    startElapsedTimer();
    try {
        const formData = new FormData();
        formData.append('onnx_file', selectedOnnxFile);
        formData.append('config_file', selectedJsonFile);
        const resp = await fetch(`${API_BASE}/compile`, { method: 'POST', body: formData });
        if (!resp.ok) throw new Error((await resp.json()).detail || 'Upload failed');
        const data = await resp.json();
        currentJobId = data.job_id;
        updateStep('pending');
        progressMessage.textContent = 'Upload complete. Waiting to compile…';
        startPolling();
    } catch (err) {
        showError(err.message);
    }
}

function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(pollStatus, POLL_INTERVAL_MS);
    pollStatus();
}

async function pollStatus() {
    if (!currentJobId) return;
    try {
        const resp = await fetch(`${API_BASE}/jobs/${currentJobId}/status`);
        if (!resp.ok) throw new Error('Status query failed');
        const data = await resp.json();
        updateJobUI(data);
        if (data.status === 'succeeded' || data.status === 'failed') { clearInterval(pollTimer); pollTimer = null; }
    } catch (err) {
        console.error('Poll error:', err);
    }
}

function updateJobUI(data) {
    jobStatusBadge.textContent = getStatusLabel(data.status);
    jobStatusBadge.className = `badge ${data.status}`;
    switch (data.status) {
        case 'uploading': updateStep('upload'); progressMessage.textContent = 'Uploading files…'; break;
        case 'pending': updateStep('pending'); progressMessage.textContent = 'Creating EC2 instance… (~6 min)'; break;
        case 'running': updateStep('running'); progressMessage.textContent = 'Compiling model…'; break;
        case 'succeeded':
            updateStep('done'); stopElapsedTimer();
            progressMessage.textContent = `Compile complete! (${getElapsedString()})`;
            downloadBtn.classList.remove('hidden');
            break;
        case 'failed':
            stopElapsedTimer();
            progressMessage.textContent = `Compile failed: ${data.error || 'unknown error'}`;
            progressMessage.style.color = 'var(--danger)';
            break;
    }
    if (data.links && Object.keys(data.links).length) {
        linksSection.classList.remove('hidden');
        const linkLabels = { s3_input: '📦 S3 input', s3_output: '📦 S3 output', ec2_instance: '🖥️ EC2 instance', step_functions: '⚡ Step Functions' };
        linksList.innerHTML = Object.entries(data.links)
            .map(([k, url]) => `<a href="${url}" target="_blank">${linkLabels[k] || k}</a>`).join('');
    }
    if (data.logs && data.logs.length) {
        logsSection.classList.remove('hidden');
        logsList.innerHTML = data.logs.map(log => {
            const time = new Date(log.time * 1000).toLocaleTimeString();
            return `<span class="log-entry"><span class="log-time">[${time}]</span>${log.message}</span>`;
        }).join('');
        logsList.scrollTop = logsList.scrollHeight;
    }
    const compilerLogsSection = document.getElementById('compiler-logs-section');
    const compilerLogsList = document.getElementById('compiler-logs-list');
    if (data.compiler_logs && data.compiler_logs.length) {
        compilerLogsSection.classList.remove('hidden');
        compilerLogsList.innerHTML = data.compiler_logs.map(log => {
            const time = new Date(log.timestamp).toLocaleTimeString();
            const icon = log.stream === 'stderr' ? '⚠️' : '';
            const cls = log.stream === 'stderr' ? 'log-entry stderr' : 'log-entry';
            return `<span class="${cls}"><span class="log-time">[${time}]</span>${icon} ${escapeHtml(log.message)}</span>`;
        }).join('');
        compilerLogsList.scrollTop = compilerLogsList.scrollHeight;
    }
}

function startElapsedTimer() {
    elapsedStartTime = Date.now();
    const el = document.getElementById('elapsed-time');
    if (el) el.classList.remove('hidden');
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = setInterval(updateElapsedDisplay, 1000);
    updateElapsedDisplay();
}

function stopElapsedTimer() { if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; } updateElapsedDisplay(); }

function getElapsedString() {
    if (!elapsedStartTime) return '0s';
    const elapsed = Math.floor((Date.now() - elapsedStartTime) / 1000);
    const min = Math.floor(elapsed / 60), sec = elapsed % 60;
    return min > 0 ? `${min}m ${sec}s` : `${sec}s`;
}

function updateElapsedDisplay() {
    const el = document.getElementById('elapsed-time');
    if (el) el.textContent = `⏱️ Elapsed: ${getElapsedString()}`;
}

function getStatusLabel(status) {
    return { uploading: 'Uploading', pending: 'Pending', running: 'Compiling', succeeded: 'Done', failed: 'Failed' }[status] || status;
}

const STEP_ORDER = ['upload', 'pending', 'running', 'done'];

function updateStep(currentStep) {
    const currentIndex = STEP_ORDER.indexOf(currentStep);
    STEP_ORDER.forEach((step, index) => {
        const el = document.getElementById(`step-${step}`);
        el.classList.remove('active', 'current');
        if (index < currentIndex) el.classList.add('active');
        else if (index === currentIndex) el.classList.add('active', 'current');
    });
    document.querySelectorAll('.step-line').forEach((line, index) => line.classList.toggle('active', index < currentIndex));
}

downloadBtn.addEventListener('click', async () => {
    if (!currentJobId) return;
    downloadBtn.disabled = true;
    downloadBtn.textContent = '⬇️ Downloading…';
    try {
        const resp = await fetch(`${API_BASE}/jobs/${currentJobId}/download`);
        if (!resp.ok) throw new Error('Download failed');
        const disposition = resp.headers.get('Content-Disposition');
        let filename = 'compiled.dxnn';
        if (disposition) { const m = disposition.match(/filename="?(.+?)"?$/); if (m) filename = m[1]; }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename;
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        URL.revokeObjectURL(url);
    } catch (err) {
        alert(`Download failed: ${err.message}`);
    } finally {
        downloadBtn.disabled = false;
        downloadBtn.textContent = '⬇️ Download DXNN';
    }
});

function showProgress(filename) {
    uploadSection.classList.add('hidden');
    progressSection.classList.remove('hidden');
    errorSection.classList.add('hidden');
    jobFilename.textContent = filename;
    downloadBtn.classList.add('hidden');
    progressMessage.style.color = '';
}

function showError(message) {
    uploadSection.classList.add('hidden');
    progressSection.classList.add('hidden');
    errorSection.classList.remove('hidden');
    errorMessage.textContent = message;
}

function resetUI() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    currentJobId = null; selectedOnnxFile = null; selectedJsonFile = null;
    fileInput.value = '';
    uploadSection.classList.remove('hidden');
    progressSection.classList.add('hidden');
    errorSection.classList.add('hidden');
    downloadBtn.classList.add('hidden');
    linksSection.classList.add('hidden');
    logsSection.classList.add('hidden');
    document.getElementById('compiler-logs-section').classList.add('hidden');
    document.getElementById('selected-files').classList.add('hidden');
    document.getElementById('upload-btn').classList.add('hidden');
    linksList.innerHTML = ''; logsList.innerHTML = '';
    document.getElementById('compiler-logs-list').innerHTML = '';
    progressMessage.style.color = '';
}

// ───────────────────────── EDGE tab (from greengrass-app) ─────────────────────────

let selectedCoreThingName = null;
const edgeState = { activeStackName: null, thingGroupName: null, thingGroupArn: null, deviceTabs: [], activeDevice: null };
const DEVICE_LIST_POLL_MS = 10000;
let deviceListTimer = null;

function startDeviceListPolling() {
    stopDeviceListPolling();
    deviceListTimer = setInterval(() => refreshDeviceList(true), DEVICE_LIST_POLL_MS);
}
function stopDeviceListPolling() { if (deviceListTimer !== null) { clearInterval(deviceListTimer); deviceListTimer = null; } }

async function enterEdge() {
    try {
        const status = await requestJson('/setup/status');
        edgeState.activeStackName = (status.stack && status.stack.name) || status.activeStackName;
        const outputs = (status.stack && status.stack.outputs) || {};
        edgeState.thingGroupName = outputs.ThingGroupName || null;
        const groups = await loadThingGroups();
        const match = groups.find(g => g.groupName === edgeState.thingGroupName);
        edgeState.thingGroupArn = match ? match.groupArn : '';
        const sel = document.getElementById('component-thing-group');
        if (sel) sel.value = edgeState.thingGroupArn || '';
        document.getElementById('devices-thing-group-name').textContent =
            edgeState.thingGroupName || '(ThingGroupName not found in stack outputs)';
        await refreshDeviceList();
        startDeviceListPolling();
    } catch (err) {
        showRequestError('component-output', err);
    }
}

async function loadThingGroups() {
    try {
        const data = await requestJson('/thing-groups');
        const groups = data.thingGroups || [];
        const sel = document.getElementById('component-thing-group');
        if (sel) sel.innerHTML = ['<option value="">(All)</option>']
            .concat(groups.map(g => `<option value="${g.groupArn}">${g.groupName}</option>`)).join('');
        return groups;
    } catch (err) {
        showJson('component-output', { error: err.message });
        return [];
    }
}

async function refreshDeviceList(silent = false) {
    const list = document.getElementById('device-list');
    if (list && !silent) list.innerHTML = '<p>Loading…</p>';
    try {
        const groupArn = document.getElementById('component-thing-group').value;
        const query = groupArn ? `?thing_group_arn=${encodeURIComponent(groupArn)}` : '';
        const data = await requestJson(`/devices/cores${query}`);
        renderDeviceList(data.devices || []);
    } catch (err) {
        if (list && !silent) list.innerHTML = '';
        if (!silent) showRequestError('component-output', err);
    }
}

function renderDeviceList(devices) {
    const list = document.getElementById('device-list');
    if (!list) return;
    if (!devices.length) {
        list.innerHTML = '<p>No core devices registered in this Thing Group. Register one below. (Auto-refreshes every 10s.)</p>';
        return;
    }
    list.innerHTML = devices.map(d => {
        const name = d.coreDeviceThingName || '';
        return `<div class="core-device-item"><span>${name} (${d.status || '-'})</span>
            <button onclick="openDeviceTab('${name}')">Open</button></div>`;
    }).join('');
}

async function loadDeviceComponents() {
    const container = document.getElementById('device-components');
    if (container) container.innerHTML = '<p>Loading…</p>';
    try {
        const groupArn = document.getElementById('component-thing-group').value;
        const query = groupArn ? `?thing_group_arn=${encodeURIComponent(groupArn)}` : '';
        const data = await requestJson(`/devices/cores${query}`);
        const devices = data.devices || [];
        const cards = await Promise.all(devices.map(async (d) => {
            const name = d.coreDeviceThingName || '';
            let components = [];
            try { components = (await requestJson(`/devices/${encodeURIComponent(name)}/components`)).components || []; }
            catch (e) { components = []; }
            const rows = components.map(c => `<li>${c.componentName} ${c.componentVersion || ''} - ${c.lifecycleState || '-'}</li>`).join('');
            return `<div class="core-device-item"><strong>${name} (${d.status || '-'})</strong>
                <ul>${rows || '<li>No components installed.</li>'}</ul></div>`;
        }));
        if (container) container.innerHTML = cards.join('') || '<p>No core devices found.</p>';
    } catch (err) {
        if (container) container.innerHTML = '';
        showJson('component-output', { error: err.message });
    }
}

function openDeviceTab(name) {
    if (!edgeState.deviceTabs.includes(name)) edgeState.deviceTabs.push(name);
    activateDevice(name);
}

function closeDeviceTab(name) {
    edgeState.deviceTabs = edgeState.deviceTabs.filter(t => t !== name);
    if (edgeState.activeDevice === name) edgeState.activeDevice = edgeState.deviceTabs[edgeState.deviceTabs.length - 1] || null;
    renderTabBar();
    if (edgeState.activeDevice) activateDevice(edgeState.activeDevice);
    else document.getElementById('device-panel').hidden = true;
}

function renderTabBar() {
    const bar = document.getElementById('device-tabbar');
    if (!bar) return;
    bar.innerHTML = edgeState.deviceTabs.map(name => {
        const active = name === edgeState.activeDevice ? ' active' : '';
        return `<div class="tab${active}"><span class="tab-label" onclick="activateDevice('${name}')">${name}</span>
            <button class="tab-close" onclick="closeDeviceTab('${name}')">×</button></div>`;
    }).join('');
}

function activateDevice(name) {
    edgeState.activeDevice = name;
    renderTabBar();
    document.getElementById('device-panel').hidden = false;
    document.getElementById('device-panel-title').textContent = `Core Device: ${name}`;
    document.getElementById('active-device-components').innerHTML = '';
    loadActiveDeviceComponents();
}

async function loadActiveDeviceComponents() {
    const container = document.getElementById('active-device-components');
    if (!edgeState.activeDevice) return;
    if (container) container.innerHTML = '<p>Loading…</p>';
    try {
        const result = await requestJson(`/devices/${encodeURIComponent(edgeState.activeDevice)}/components`);
        const rows = (result.components || []).map(c =>
            `<li>${c.componentName} ${c.componentVersion || ''} - ${c.lifecycleState || '-'}</li>`).join('');
        if (container) container.innerHTML = `<ul>${rows || '<li>No components installed.</li>'}</ul>`;
    } catch (err) {
        if (container) container.innerHTML = `<p class="warn">${err.message}</p>`;
    }
}

async function generateInstallScript() {
    const body = { thing_name: document.getElementById('thing-name').value };
    if (edgeState.thingGroupName) body.thing_group_name = edgeState.thingGroupName;
    try {
        showJson('device-output', await requestJson('/devices/install-script', { method: 'POST', body: JSON.stringify(body) }));
    } catch (err) {
        showJson('device-output', { error: err.message });
    }
}

async function loadCoreDevices() {
    try {
        const data = await requestJson('/devices/cores');
        renderCoreDevices(data.devices || []);
        showJson('device-output', data);
    } catch (err) {
        showJson('device-output', { error: err.message });
    }
}

function renderCoreDevices(devices) {
    const list = document.getElementById('core-device-list');
    if (!list) return;
    if (!devices.length) { list.innerHTML = '<p>No core devices found.</p>'; return; }
    list.innerHTML = devices.map(d => {
        const name = d.coreDeviceThingName || '';
        return `<div class="core-device-item"><span>${name} (${d.status || '-'})</span>
            <button onclick="selectCoreDevice('${name}')">Select</button></div>`;
    }).join('');
}

function selectCoreDevice(name) {
    selectedCoreThingName = name;
    document.getElementById('selected-core-output').textContent = `Selected core device: ${name}`;
}

async function copyManualCommands() {
    try {
        await navigator.clipboard.writeText(document.getElementById('manual-install-commands').textContent);
    } catch (err) {
        showJson('device-output', { error: `Copy failed: ${err.message}` });
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
    if (edgeState.thingGroupName) body.thing_group_name = edgeState.thingGroupName;
    const output = document.getElementById('device-output');
    output.textContent = 'Starting SSH installation…\n';
    try {
        const response = await fetch(`${API_BASE}/devices/ssh-install-stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!response.ok || !response.body) {
            output.textContent = formatRequestError(await response.json().catch(() => ({})));
            return;
        }
        output.textContent = '';
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            output.textContent += decoder.decode(value, { stream: true });
            output.scrollTop = output.scrollHeight;
        }
        output.textContent += decoder.decode();
    } catch (err) {
        output.textContent += `\n[Error] ${err.message}`;
    }
}

// ───────────────────────── init ─────────────────────────

(async function init() {
    await refreshSetupStatus();
    loadNetwork();
    loadExistingStacks();
    setInterval(refreshSetupStatus, POLL_INTERVAL_MS);
})();
