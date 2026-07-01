const API_BASE = '/api';
const POLL_INTERVAL_MS = 5000;

let currentJobId = null;
let pollTimer = null;
let deployPollTimer = null;

// ═══ VIEW MANAGEMENT ═══

const setupView = document.getElementById('setup-view');
const compilerView = document.getElementById('compiler-view');
const statusBanner = document.getElementById('status-banner');
const statusText = document.getElementById('status-text');

function showSetup() {
    setupView.classList.remove('hidden');
    compilerView.classList.add('hidden');
    history.pushState(null, '', '/');
}

function showCompiler() {
    setupView.classList.add('hidden');
    compilerView.classList.remove('hidden');
    history.pushState(null, '', '/compiler');
}

// ═══ SETUP LOGIC ═══

async function checkSetup() {
    try {
        const resp = await fetch(`${API_BASE}/setup/status`);
        const data = await resp.json();

        // Region
        document.getElementById('setup-region').textContent = data.region;

        // Marketplace link
        document.getElementById('marketplace-link').href = data.marketplace.subscribe_url;

        // Populate AMI selector from server options
        if (data.ami_options) {
            const amiSelect = document.getElementById('ami-select');
            if (amiSelect && amiSelect.options.length <= 2) {
                amiSelect.innerHTML = data.ami_options.map(opt =>
                    `<option value="${opt.key}">${opt.label}${opt.requires_subscription ? ' (구독 필요)' : ''}</option>`
                ).join('');
            }
        }

        const subStatus = document.getElementById('sub-status');
        const step1 = document.getElementById('setup-step-1');
        const step2 = document.getElementById('setup-step-2');
        const step3 = document.getElementById('setup-step-3');
        const subBody = document.getElementById('sub-body');

        if (data.marketplace.subscribed) {
            subStatus.textContent = `✅ 구독됨 (${data.marketplace.ami_id})`;
            subStatus.className = 'setup-step-status ok';
            step1.classList.add('completed');
            const existingMsg = subBody.querySelector('.sub-confirmed');
            if (!existingMsg) {
                const msg = document.createElement('p');
                msg.className = 'sub-confirmed';
                msg.innerHTML = `✅ 구독 확인됨. AMI: <code>${data.marketplace.ami_id}</code>`;
                subBody.insertBefore(msg, subBody.firstChild);
            }
        } else {
            subStatus.textContent = '⚠️ 미구독 (Ubuntu AMI는 구독 없이 사용 가능)';
            subStatus.className = 'setup-step-status';
        }

        // Step 2 is always accessible (Ubuntu doesn't need subscription)
        step2.classList.remove('disabled');
        loadExistingStacks();

        // Stack status
        const stackStatus = document.getElementById('stack-deploy-status');
        if (data.stack.ready) {
            stackStatus.textContent = `✅ 배포 완료 (${data.stack.name})`;
            stackStatus.className = 'setup-step-status ok';
            step2.classList.add('completed');
            step3.classList.remove('disabled');
            step3.classList.add('completed');
            document.getElementById('setup-complete-status').textContent = '✅ 준비 완료';
            document.getElementById('setup-complete-status').className = 'setup-step-status ok';
            // 스택 목록과 버튼은 유지 (다른 스택 선택 가능)
            loadExistingStacks();

            showSetup();
            return;
        }

        if (data.stack.status === 'CREATE_IN_PROGRESS') {
            stackStatus.textContent = '🔄 배포 중...';
            stackStatus.className = 'setup-step-status progress';
            startDeployPolling();
        }

        showSetup();
    } catch (err) {
        statusBanner.classList.remove('hidden');
        statusText.textContent = '⚠️ 서버에 연결할 수 없습니다';
        setTimeout(checkSetup, 5000);
    }
}

async function toggleTemplate() {
    const viewer = document.getElementById('cf-template-viewer');
    const content = document.getElementById('cf-template-content');
    const amiSelect = document.getElementById('ami-select');
    const amiType = amiSelect ? amiSelect.value : 'marketplace';

    if (!viewer.classList.contains('hidden')) {
        viewer.classList.add('hidden');
        return;
    }

    content.textContent = '로딩 중...';
    try {
        const resp = await fetch(`${API_BASE}/setup/template?ami_type=${amiType}`);
        content.textContent = await resp.text();
    } catch (e) {
        content.textContent = '템플릿 로딩 실패: ' + e.message;
    }
    viewer.classList.remove('hidden');
}

async function deployStack() {
    const deployBtn = document.getElementById('deploy-btn');
    const amiSelect = document.getElementById('ami-select');
    const amiType = amiSelect.value;

    deployBtn.disabled = true;
    deployBtn.textContent = '🔄 배포 시작 중...';

    try {
        const resp = await fetch(`${API_BASE}/setup/deploy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ami_type: amiType }),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '배포 실패');
        }

        const data = await resp.json();
        const stackStatus = document.getElementById('stack-deploy-status');

        stackStatus.textContent = `🔄 배포 중... (${data.stack_name || ''})`;
        stackStatus.className = 'setup-step-status progress';
        deployBtn.textContent = '🔄 배포 진행 중...';

        const deployEvents = document.getElementById('deploy-events');
        deployEvents.classList.remove('hidden');
        startDeployPolling();
    } catch (err) {
        deployBtn.disabled = false;
        deployBtn.textContent = '🚀 새 스택 배포하기';
        alert(`배포 실패: ${err.message}`);
    }
}

let stackRefreshTimer = null;

async function loadExistingStacks() {
    const container = document.getElementById('existing-stacks');
    const list = document.getElementById('stacks-list');

    try {
        const resp = await fetch(`${API_BASE}/setup/stacks`);
        if (!resp.ok) return;
        const data = await resp.json();

        if (data.stacks.length === 0) {
            container.classList.add('hidden');
            stopStackRefresh();
            return;
        }

        container.classList.remove('hidden');
        let hasInProgress = false;

        list.innerHTML = data.stacks.map(s => {
            const isActive = s.name === data.active;
            const statusIcon = s.status.includes('COMPLETE') ? '✅' :
                               s.status.includes('PROGRESS') ? '🔄' :
                               s.status.includes('DELETE') ? '🗑️' : '⚠️';
            const bucket = s.outputs.ModelBucketName || '-';
            if (s.status.includes('PROGRESS')) hasInProgress = true;
            const deleteDisabled = s.status.includes('PROGRESS') || s.status.includes('DELETE') ? 'disabled' : '';
            return `
                <div class="stack-item ${isActive ? 'active' : ''}">
                    <div class="stack-item-header" onclick="selectStack('${s.name}')">
                        <strong>${s.name}</strong>
                        <span class="stack-item-status">${statusIcon} ${s.status}</span>
                    </div>
                    <div class="stack-item-detail">
                        <span>📦 버킷: ${bucket}</span>
                        <span>📅 ${new Date(s.created).toLocaleDateString('ko-KR')}</span>
                        <button class="btn-delete" ${deleteDisabled} onclick="event.stopPropagation(); deleteStack('${s.name}')">🗑️ 삭제</button>
                    </div>
                    ${isActive ? '<span class="stack-active-badge">현재 선택됨</span>' : ''}
                </div>`;
        }).join('');

        if (hasInProgress) {
            startStackRefresh();
        } else {
            stopStackRefresh();
        }
    } catch (err) {
        console.error('Failed to load stacks:', err);
    }
}

function startStackRefresh() {
    if (stackRefreshTimer) return;
    stackRefreshTimer = setInterval(loadExistingStacks, 5000);
}

function stopStackRefresh() {
    if (stackRefreshTimer) {
        clearInterval(stackRefreshTimer);
        stackRefreshTimer = null;
    }
}

async function selectStack(stackName) {
    try {
        const resp = await fetch(`${API_BASE}/setup/stacks/select`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stack_name: stackName }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '스택 선택 실패');
        }

        checkSetup();
    } catch (err) {
        alert(`스택 선택 실패: ${err.message}`);
    }
}

async function deleteStack(stackName) {
    if (!confirm(`정말 '${stackName}' 스택을 삭제하시겠습니까?\nS3 버킷은 유지되지만 Lambda, Step Functions 등 모든 리소스가 삭제됩니다.`)) {
        return;
    }

    try {
        const resp = await fetch(`${API_BASE}/setup/stacks/${encodeURIComponent(stackName)}`, {
            method: 'DELETE',
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '스택 삭제 실패');
        }

        startStackRefresh();
        loadExistingStacks();
    } catch (err) {
        alert(`스택 삭제 실패: ${err.message}`);
    }
}

function startDeployPolling() {
    if (deployPollTimer) clearInterval(deployPollTimer);
    deployPollTimer = setInterval(pollDeployStatus, 5000);
    pollDeployStatus();
}

async function pollDeployStatus() {
    try {
        const resp = await fetch(`${API_BASE}/setup/deploy/status`);
        const data = await resp.json();

        // Update events
        const deployEvents = document.getElementById('deploy-events');
        deployEvents.classList.remove('hidden');
        if (data.events && data.events.length > 0) {
            deployEvents.innerHTML = data.events
                .map(e => {
                    const icon = e.status.includes('COMPLETE') ? '✅' :
                                 e.status.includes('PROGRESS') ? '🔄' :
                                 e.status.includes('FAILED') ? '❌' : '▶️';
                    return `<span class="log-entry">${icon} ${e.resource}: ${e.status}${e.reason ? ' - ' + e.reason : ''}</span>`;
                })
                .join('');
            deployEvents.scrollTop = deployEvents.scrollHeight;
        }

        if (data.ready) {
            clearInterval(deployPollTimer);
            deployPollTimer = null;
            const deployBtn = document.getElementById('deploy-btn');
            deployBtn.disabled = false;
            deployBtn.textContent = '🚀 새 스택 배포하기';
            checkSetup();
        } else if (data.stack_status.includes('FAILED') || data.stack_status.includes('ROLLBACK')) {
            clearInterval(deployPollTimer);
            deployPollTimer = null;
            const stackStatus = document.getElementById('stack-deploy-status');
            stackStatus.textContent = '❌ 배포 실패';
            stackStatus.className = 'setup-step-status error';
            const deployBtn = document.getElementById('deploy-btn');
            deployBtn.disabled = false;
            deployBtn.textContent = '🚀 다시 배포하기';
        }
    } catch (err) {
        console.error('Deploy poll error:', err);
    }
}

// ═══ COMPILER LOGIC ═══

// DOM Elements (compiler)
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

// File handling
fileBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    fileInput.click();
});

dropZone.addEventListener('click', (e) => {
    if (e.target.id === 'upload-btn' || e.target.id === 'file-btn') return;
    fileInput.click();
});

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFiles(Array.from(e.target.files));
    }
});

dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
});

dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('dragover');
});

dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
        handleFiles(Array.from(e.dataTransfer.files));
    }
});

function handleFiles(files) {
    for (const file of files) {
        const name = file.name.toLowerCase();
        if (name.endsWith('.onnx')) {
            selectedOnnxFile = file;
        } else if (name.endsWith('.json')) {
            selectedJsonFile = file;
        }
    }
    updateSelectedFilesUI();
}

function updateSelectedFilesUI() {
    const container = document.getElementById('selected-files');
    const onnxEl = document.getElementById('selected-onnx');
    const jsonEl = document.getElementById('selected-json');
    const uploadBtn = document.getElementById('upload-btn');

    container.classList.remove('hidden');

    onnxEl.textContent = selectedOnnxFile
        ? `✅ ONNX: ${selectedOnnxFile.name}`
        : '⬜ ONNX 파일을 선택하세요';
    onnxEl.className = selectedOnnxFile ? 'selected-file ready' : 'selected-file';

    jsonEl.textContent = selectedJsonFile
        ? `✅ JSON: ${selectedJsonFile.name}`
        : '⬜ JSON 설정 파일을 선택하세요';
    jsonEl.className = selectedJsonFile ? 'selected-file ready' : 'selected-file';

    if (selectedOnnxFile && selectedJsonFile) {
        uploadBtn.classList.remove('hidden');
        uploadBtn.onclick = () => startCompile();
    } else {
        uploadBtn.classList.add('hidden');
    }
}

async function startCompile() {
    const MAX_SIZE = 500 * 1024 * 1024;
    if (selectedOnnxFile.size > MAX_SIZE) {
        showError('ONNX 파일 크기가 500MB를 초과합니다.');
        return;
    }
    if (selectedJsonFile.size > 10 * 1024 * 1024) {
        showError('JSON 파일 크기가 10MB를 초과합니다.');
        return;
    }

    showProgress(selectedOnnxFile.name);
    updateStep('upload');
    progressMessage.textContent = '파일 업로드 중...';
    startElapsedTimer();

    try {
        const formData = new FormData();
        formData.append('onnx_file', selectedOnnxFile);
        formData.append('config_file', selectedJsonFile);

        const resp = await fetch(`${API_BASE}/compile`, {
            method: 'POST',
            body: formData,
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '업로드 실패');
        }

        const data = await resp.json();
        currentJobId = data.job_id;
        updateStep('pending');
        progressMessage.textContent = '업로드 완료! 컴파일 대기 중...';
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
        if (!resp.ok) throw new Error('상태 조회 실패');

        const data = await resp.json();
        updateJobUI(data);

        if (data.status === 'succeeded' || data.status === 'failed') {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    } catch (err) {
        console.error('Poll error:', err);
    }
}

function updateJobUI(data) {
    jobStatusBadge.textContent = getStatusLabel(data.status);
    jobStatusBadge.className = `badge ${data.status}`;

    switch (data.status) {
        case 'uploading':
            updateStep('upload');
            progressMessage.textContent = '파일 업로드 중...';
            break;
        case 'pending':
            updateStep('pending');
            progressMessage.textContent = 'EC2 인스턴스 생성 중... (약 6분 대기)';
            break;
        case 'running':
            updateStep('running');
            progressMessage.textContent = '모델 컴파일 중... 잠시만 기다려주세요.';
            break;
        case 'succeeded':
            updateStep('done');
            stopElapsedTimer();
            progressMessage.textContent = `컴파일 완료! (소요시간: ${getElapsedString()})`;
            downloadBtn.classList.remove('hidden');
            break;
        case 'failed':
            stopElapsedTimer();
            progressMessage.textContent = `컴파일 실패: ${data.error || '알 수 없는 오류'}`;
            progressMessage.style.color = 'var(--danger)';
            break;
    }

    // Render links
    if (data.links && Object.keys(data.links).length > 0) {
        linksSection.classList.remove('hidden');
        const linkLabels = {
            s3_input: '📦 S3 입력 파일',
            s3_output: '📦 S3 출력 파일',
            ec2_instance: '🖥️ EC2 인스턴스',
            step_functions: '⚡ Step Functions',
        };
        linksList.innerHTML = Object.entries(data.links)
            .map(([key, url]) => `<a href="${url}" target="_blank">${linkLabels[key] || key}</a>`)
            .join('');
    }

    // Render logs
    if (data.logs && data.logs.length > 0) {
        logsSection.classList.remove('hidden');
        logsList.innerHTML = data.logs
            .map(log => {
                const time = new Date(log.time * 1000).toLocaleTimeString('ko-KR');
                return `<span class="log-entry"><span class="log-time">[${time}]</span>${log.message}</span>`;
            })
            .join('');
        logsList.scrollTop = logsList.scrollHeight;
    }

    // Render compiler logs (dx_com output)
    const compilerLogsSection = document.getElementById('compiler-logs-section');
    const compilerLogsList = document.getElementById('compiler-logs-list');
    if (data.compiler_logs && data.compiler_logs.length > 0) {
        compilerLogsSection.classList.remove('hidden');
        compilerLogsList.innerHTML = data.compiler_logs
            .map(log => {
                const time = new Date(log.timestamp).toLocaleTimeString('ko-KR');
                const icon = log.stream === 'stderr' ? '⚠️' : '';
                const cls = log.stream === 'stderr' ? 'log-entry stderr' : 'log-entry';
                return `<span class="${cls}"><span class="log-time">[${time}]</span>${icon} ${escapeHtml(log.message)}</span>`;
            })
            .join('');
        compilerLogsList.scrollTop = compilerLogsList.scrollHeight;
    }
}

function startElapsedTimer() {
    elapsedStartTime = Date.now();
    const elapsedEl = document.getElementById('elapsed-time');
    if (elapsedEl) elapsedEl.classList.remove('hidden');
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = setInterval(updateElapsedDisplay, 1000);
    updateElapsedDisplay();
}

function stopElapsedTimer() {
    if (elapsedTimer) {
        clearInterval(elapsedTimer);
        elapsedTimer = null;
    }
    updateElapsedDisplay();
}

function getElapsedString() {
    if (!elapsedStartTime) return '0초';
    const elapsed = Math.floor((Date.now() - elapsedStartTime) / 1000);
    const min = Math.floor(elapsed / 60);
    const sec = elapsed % 60;
    return min > 0 ? `${min}분 ${sec}초` : `${sec}초`;
}

function updateElapsedDisplay() {
    const elapsedEl = document.getElementById('elapsed-time');
    if (elapsedEl) {
        elapsedEl.textContent = `⏱️ 소요시간: ${getElapsedString()}`;
    }
}

function getStatusLabel(status) {
    const labels = {
        uploading: '업로드 중',
        pending: '대기 중',
        running: '컴파일 중',
        succeeded: '완료',
        failed: '실패',
    };
    return labels[status] || status;
}

const STEP_ORDER = ['upload', 'pending', 'running', 'done'];

function updateStep(currentStep) {
    const currentIndex = STEP_ORDER.indexOf(currentStep);

    STEP_ORDER.forEach((step, index) => {
        const el = document.getElementById(`step-${step}`);
        el.classList.remove('active', 'current');

        if (index < currentIndex) {
            el.classList.add('active');
        } else if (index === currentIndex) {
            el.classList.add('active', 'current');
        }
    });

    document.querySelectorAll('.step-line').forEach((line, index) => {
        line.classList.toggle('active', index < currentIndex);
    });
}

downloadBtn.addEventListener('click', async () => {
    if (!currentJobId) return;
    downloadBtn.disabled = true;
    downloadBtn.textContent = '⬇️ 다운로드 중...';

    try {
        const resp = await fetch(`${API_BASE}/jobs/${currentJobId}/download`);
        if (!resp.ok) throw new Error('다운로드 실패');

        const disposition = resp.headers.get('Content-Disposition');
        let filename = 'compiled.dxnn';
        if (disposition) {
            const match = disposition.match(/filename="?(.+?)"?$/);
            if (match) filename = match[1];
        }

        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    } catch (err) {
        alert(`다운로드 실패: ${err.message}`);
    } finally {
        downloadBtn.disabled = false;
        downloadBtn.textContent = '⬇️ DXNN 다운로드';
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
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    currentJobId = null;
    selectedOnnxFile = null;
    selectedJsonFile = null;
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
    linksList.innerHTML = '';
    logsList.innerHTML = '';
    document.getElementById('compiler-logs-list').innerHTML = '';
    progressMessage.style.color = '';
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ═══ INITIALIZE ═══

// 뒤로가기/앞으로가기 처리
window.addEventListener('popstate', () => {
    if (window.location.pathname === '/compiler') {
        setupView.classList.add('hidden');
        compilerView.classList.remove('hidden');
    } else {
        setupView.classList.remove('hidden');
        compilerView.classList.add('hidden');
    }
});

checkSetup().then(() => {
    // URL이 /compiler이면 스택 준비 확인 후 컴파일러 뷰 표시
    if (window.location.pathname === '/compiler') {
        fetch(`${API_BASE}/setup/status`)
            .then(r => r.json())
            .then(data => {
                if (data.stack.ready) {
                    setupView.classList.add('hidden');
                    compilerView.classList.remove('hidden');
                }
            });
    }
});
