// ==========================================
// Admin Dashboard Logic (Phase 5)
// ==========================================

let adminToken = sessionStorage.getItem('vericlaim_admin_token');
let wsMatrix = null;
let headers = {};
let currentActiveClaim = null;

// ── INIT & AUTH ──
window.addEventListener('DOMContentLoaded', async () => {
    updateThemeIcon();
    
    if (adminToken) {
        // Try to validate token
        headers = { 'Authorization': `Bearer ${adminToken}`, 'Content-Type': 'application/json' };
        try {
            const checkAdmin = await fetch('/api/v1/admin/stats', { headers });
            if (checkAdmin.ok) {
                document.getElementById('adminLoginOverlay').classList.add('hidden');
                initDashboard();
            } else {
                // Token expired or invalid
                sessionStorage.removeItem('vericlaim_admin_token');
                adminToken = null;
            }
        } catch (e) {
            // Network error (backend might be restarting)
            console.error("Initial auth check failed", e);
            // We assume token might still be valid, just network down.
            // But to be safe, if we can't reach the server, we might want to stay on the login screen.
        }
    }
    
    // Hide the initial loader once auth checks are completed
    const initLoader = document.getElementById('initLoader');
    if (initLoader) {
        initLoader.classList.add('opacity-0');
        setTimeout(() => initLoader.classList.add('hidden'), 300);
    }
});

async function handleAdminLogin(e) {
    e.preventDefault();
    const user = document.getElementById('adminUser').value;
    const pass = document.getElementById('adminPass').value;
    const btn = document.getElementById('loginBtn');
    const err = document.getElementById('loginError');
    
    btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i>';
    err.classList.add('hidden');
    
    try {
        const res = await fetch('/api/v1/admin/auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: user, password: pass })
        });
        
        if (res.ok) {
            const data = await res.json();
            adminToken = data.token;
            sessionStorage.setItem('vericlaim_admin_token', adminToken);
            headers = { 'Authorization': `Bearer ${adminToken}`, 'Content-Type': 'application/json' };
            
            document.getElementById('adminLoginOverlay').classList.add('hidden');
            initDashboard();
        } else {
            err.classList.remove('hidden');
        }
    } catch (error) {
        err.innerText = "Network Error.";
        err.classList.remove('hidden');
    }
    
    btn.innerHTML = 'Authenticate';
}

async function initDashboard() {
    await loadData();
    setupMatrixWebSocket();
}

// ── NAVIGATION ──
function showSection(id) {
    ['overview', 'claims', 'users', 'telemetry', 'special'].forEach(sec => {
        document.getElementById(`section-${sec}`).classList.replace('block', 'hidden');
        document.getElementById(`section-${sec}`).classList.replace('flex', 'hidden');
        document.getElementById(`nav-${sec}`).classList.remove('active');
    });
    
    const section = document.getElementById(`section-${id}`);
    if (id === 'telemetry') {
        section.classList.replace('hidden', 'flex'); // Flex is needed for the terminal layout
    } else {
        section.classList.replace('hidden', 'block');
    }
    document.getElementById(`nav-${id}`).classList.add('active');
}

// ── DATA LOADING ──
async function loadData() {
    await Promise.all([
        loadStats(),
        loadClaims(),
        loadUsers()
    ]);
}

async function loadStats() {
    try {
        const res = await fetch('/api/v1/admin/stats', { headers });
        const stats = await res.json();
        
        document.getElementById('statsContainer').innerHTML = `
            <div class="glass-card rounded-2xl p-6 relative overflow-hidden group">
                <div class="absolute -right-4 -top-4 w-24 h-24 bg-slate-100 dark:bg-white/5 rounded-full flex items-center justify-center transition-transform group-hover:scale-110">
                    <i class="fa-solid fa-layer-group text-3xl text-slate-200 dark:text-white/10 relative top-2 right-2"></i>
                </div>
                <p class="text-xs text-slate-500 dark:text-gray-400 uppercase tracking-widest font-medium mb-2 relative z-10">Total Claims</p>
                <p class="text-3xl font-light text-slate-900 dark:text-white relative z-10">${stats.total || 0}</p>
            </div>
            <div class="glass-card rounded-2xl p-6 relative overflow-hidden group">
                <div class="absolute -right-4 -top-4 w-24 h-24 bg-slate-100 dark:bg-white/5 rounded-full flex items-center justify-center transition-transform group-hover:scale-110">
                    <i class="fa-solid fa-hourglass-half text-3xl text-slate-200 dark:text-white/10 relative top-2 right-2"></i>
                </div>
                <p class="text-xs text-slate-500 dark:text-gray-400 uppercase tracking-widest font-medium mb-2 relative z-10">Pending Review</p>
                <p class="text-3xl font-light text-slate-900 dark:text-white relative z-10">${stats.pending || 0}</p>
            </div>
            <div class="glass-card rounded-2xl p-6 border-l-4 border-l-red-500 relative overflow-hidden group">
                <div class="absolute -right-4 -top-4 w-24 h-24 bg-red-50 dark:bg-red-500/10 rounded-full flex items-center justify-center transition-transform group-hover:scale-110">
                    <i class="fa-solid fa-flag text-3xl text-red-200 dark:text-red-900/30 relative top-2 right-2"></i>
                </div>
                <p class="text-xs text-red-500 dark:text-red-400 uppercase tracking-widest font-medium mb-2 relative z-10">Flagged</p>
                <p class="text-3xl font-light text-red-600 dark:text-red-400 font-semibold relative z-10">${stats.flagged || 0}</p>
            </div>
            <div class="glass-card rounded-2xl p-6 border-l-4 border-l-green-500 relative overflow-hidden group">
                <div class="absolute -right-4 -top-4 w-24 h-24 bg-green-50 dark:bg-green-500/10 rounded-full flex items-center justify-center transition-transform group-hover:scale-110">
                    <i class="fa-solid fa-check-circle text-3xl text-green-200 dark:text-green-900/30 relative top-2 right-2"></i>
                </div>
                <p class="text-xs text-green-500 dark:text-green-400 uppercase tracking-widest font-medium mb-2 relative z-10">Auto-Approved</p>
                <p class="text-3xl font-light text-green-600 dark:text-green-400 font-semibold relative z-10">${stats.approved || 0}</p>
            </div>
            <div class="glass-card rounded-2xl p-6 border-l-4 border-l-slate-400 dark:border-l-slate-600 relative overflow-hidden group">
                <div class="absolute -right-4 -top-4 w-24 h-24 bg-slate-100 dark:bg-slate-800/50 rounded-full flex items-center justify-center transition-transform group-hover:scale-110">
                    <i class="fa-solid fa-ban text-3xl text-slate-200 dark:text-slate-700 relative top-2 right-2"></i>
                </div>
                <p class="text-xs text-slate-500 dark:text-slate-400 uppercase tracking-widest font-medium mb-2 relative z-10">Denied</p>
                <p class="text-3xl font-light text-slate-700 dark:text-slate-300 font-semibold relative z-10">${stats.denied || 0}</p>
            </div>
        `;
    } catch (e) {
        console.error("Failed to load stats", e);
        document.getElementById('statsContainer').innerHTML = `
            <div class="col-span-1 md:col-span-4 glass-card rounded-2xl p-6 flex flex-col items-center justify-center text-center">
                <p class="text-red-500 mb-3">Network error while fetching statistics.</p>
                <button onclick="loadStats()" class="px-4 py-2 bg-slate-200 dark:bg-white/10 hover:bg-slate-300 dark:hover:bg-white/20 rounded-lg text-xs font-medium transition-colors">Click to Retry</button>
            </div>
        `;
    }
}

async function loadClaims() {
    try {
        const res = await fetch('/api/v1/admin/claims', { headers });
        const data = await res.json();
        const claims = data.claims || [];
        
        const flaggedTable = document.getElementById('flaggedTableBody');
        const allTable = document.getElementById('claimsTableBody');
        
        flaggedTable.innerHTML = '';
        allTable.innerHTML = '';
        
        if (claims.length === 0) {
            allTable.innerHTML = `<tr><td colspan="4" class="px-6 py-8 text-center text-slate-500">No claims in system.</td></tr>`;
            flaggedTable.innerHTML = `<tr><td colspan="4" class="px-6 py-8 text-center text-slate-500">No flagged claims.</td></tr>`;
            return;
        }

        let flaggedCount = 0;

        claims.forEach(claim => {
            const date = new Date(claim.created_at).toLocaleDateString();
            const statusColor = claim.status === 'approved' ? 'text-green-500 bg-green-500/10' :
                                claim.status === 'denied' ? 'text-red-500 bg-red-500/10' :
                                claim.status === 'flagged' ? 'text-red-600 font-bold bg-red-600/20' :
                                'text-cyan-500 bg-cyan-500/10';
            
            const btnHtml = `<button onclick="openClaimModal('${claim.conversation_id}', '${claim.id}')" class="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:underline uppercase tracking-widest">Review</button>`;

            // Add to All Claims Table
            allTable.insertAdjacentHTML('beforeend', `
                <tr class="hover:bg-slate-50 dark:hover:bg-white/5 transition-colors">
                    <td class="px-6 py-4">${date}</td>
                    <td class="px-6 py-4 font-medium text-slate-900 dark:text-white">${claim.title}</td>
                    <td class="px-6 py-4"><span class="px-2 py-1 rounded text-[10px] uppercase tracking-widest ${statusColor}">${claim.status || 'pending'}</span></td>
                    <td class="px-6 py-4 text-right">${btnHtml}</td>
                </tr>
            `);

            // Add to Flagged Table if status is flagged
            if (claim.status === 'flagged' || claim.status === 'pending') {
                flaggedCount++;
                flaggedTable.insertAdjacentHTML('beforeend', `
                    <tr class="hover:bg-slate-50 dark:hover:bg-white/5 transition-colors">
                        <td class="px-6 py-4 text-xs font-mono text-slate-500">${claim.id.split('-')[0]}</td>
                        <td class="px-6 py-4 font-medium text-slate-900 dark:text-white">${claim.title}</td>
                        <td class="px-6 py-4 text-xs font-mono text-slate-500">${claim.user_id.split('-')[0]}</td>
                        <td class="px-6 py-4 text-right">${btnHtml}</td>
                    </tr>
                `);
            }
        });
        
        if (flaggedCount === 0) {
            flaggedTable.innerHTML = `<tr><td colspan="4" class="px-6 py-8 text-center text-slate-500">Zero pending or flagged claims. You're all caught up!</td></tr>`;
        }
    } catch (e) {
        console.error("Failed to load claims", e);
        const flaggedTable = document.getElementById('flaggedTableBody');
        const allTable = document.getElementById('claimsTableBody');
        const retryHtml = `<tr><td colspan="4" class="px-6 py-8 text-center text-slate-500">
            <p class="text-red-500 mb-3">Network error while fetching claims.</p>
            <button onclick="loadClaims()" class="px-4 py-2 bg-slate-200 dark:bg-white/10 hover:bg-slate-300 dark:hover:bg-white/20 rounded-lg text-xs font-medium transition-colors">Click to Retry</button>
        </td></tr>`;
        if (allTable) allTable.innerHTML = retryHtml;
        if (flaggedTable) flaggedTable.innerHTML = retryHtml;
    }
}

async function loadUsers() {
    try {
        const res = await fetch('/api/v1/admin/users', { headers });
        const data = await res.json();
        const users = data.users || [];
        
        const table = document.getElementById('usersTableBody');
        table.innerHTML = '';
        
        if (users.length === 0) {
            table.innerHTML = `<tr><td colspan="5" class="px-6 py-8 text-center text-slate-500">No users found.</td></tr>`;
            return;
        }

        users.forEach(u => {
            let statusColor = "bg-green-100 text-green-700 dark:bg-green-500/20 dark:text-green-400";
            if (u.status === "banned") statusColor = "bg-yellow-100 text-yellow-700 dark:bg-yellow-500/20 dark:text-yellow-400";
            
            const actionHtml = `
                <div class="flex items-center justify-end gap-2">
                    ${u.status === 'banned' 
                        ? `<button onclick="handleUserActionBtn('unban', '${u.user_id}')" class="px-3 py-1.5 rounded text-xs font-medium bg-green-50 text-green-700 hover:bg-green-100 dark:bg-green-500/10 dark:text-green-400 dark:hover:bg-green-500/20 transition-colors border border-green-200 dark:border-green-500/20">Unban</button>` 
                        : `<button onclick="handleUserActionBtn('suspend', '${u.user_id}')" class="px-3 py-1.5 rounded text-xs font-medium bg-yellow-50 text-yellow-700 hover:bg-yellow-100 dark:bg-yellow-500/10 dark:text-yellow-400 dark:hover:bg-yellow-500/20 transition-colors border border-yellow-200 dark:border-yellow-500/20">Suspend</button>`
                    }
                    <button onclick="handleUserActionBtn('hardban', '${u.user_id}')" class="px-3 py-1.5 rounded text-xs font-medium bg-red-50 text-red-700 hover:bg-red-100 dark:bg-red-500/10 dark:text-red-400 dark:hover:bg-red-500/20 transition-colors border border-red-200 dark:border-red-500/20">Purge</button>
                    <button onclick="handleUserActionBtn('clear', '${u.user_id}')" class="px-3 py-1.5 rounded text-xs font-medium bg-blue-50 text-blue-700 hover:bg-blue-100 dark:bg-blue-500/10 dark:text-blue-400 dark:hover:bg-blue-500/20 transition-colors border border-blue-200 dark:border-blue-500/20" title="Clear User Data"><i class="fa-solid fa-broom"></i></button>
                    <button onclick="handleUserActionBtn('delete', '${u.user_id}')" class="px-3 py-1.5 rounded text-xs font-medium bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-white/5 dark:text-gray-300 dark:hover:bg-white/10 transition-colors border border-slate-200 dark:border-white/10" title="Delete Account"><i class="fa-solid fa-trash-can"></i></button>
                </div>
            `;

            table.insertAdjacentHTML('beforeend', `
                <tr class="hover:bg-slate-50 dark:hover:bg-white/5 transition-colors">
                    <td class="px-6 py-4 font-medium text-slate-900 dark:text-white">${u.name}</td>
                    <td class="px-6 py-4 text-slate-600 dark:text-gray-400 text-xs">${u.email}</td>
                    <td class="px-6 py-4 text-center font-medium text-cyan-600 dark:text-cyan-400">${u.total_conversations}</td>
                    <td class="px-6 py-4 text-center"><span class="px-2 py-1 rounded text-[10px] uppercase tracking-widest ${statusColor}">${u.status}</span></td>
                    <td class="px-6 py-4 text-right">${actionHtml}</td>
                </tr>
            `);
        });
    } catch (e) {
        console.error("Failed to load users", e);
        const table = document.getElementById('usersTableBody');
        table.innerHTML = `<tr><td colspan="5" class="px-6 py-8 text-center text-slate-500">
            <p class="text-red-500 mb-3">Network error while fetching users.</p>
            <button onclick="loadUsers()" class="px-4 py-2 bg-slate-200 dark:bg-white/10 hover:bg-slate-300 dark:hover:bg-white/20 rounded-lg text-xs font-medium transition-colors">Click to Retry</button>
        </td></tr>`;
    }
}

// ── USER ACTIONS (BAN / DELETE) ──
let activeUserId = null;
let activeUserAction = null;

function handleUserActionBtn(action, userId) {
    if (!action) return;
    
    activeUserId = userId;
    activeUserAction = action;
    
    const titleEl = document.getElementById('userActionTitle');
    const descEl = document.getElementById('userActionDesc');
    const btnEl = document.getElementById('confirmUserActionButton');
    const headerEl = document.getElementById('userActionHeader');
    const reasonContainer = document.getElementById('userActionReasonContainer');
    
    // Reset styles
    headerEl.className = "px-6 py-4 border-b border-slate-200 dark:border-white/10 flex justify-between items-center bg-slate-50 dark:bg-white/5";
    btnEl.className = "px-4 py-2 rounded-lg text-sm font-medium text-white transition-colors";
    reasonContainer.style.display = "block";
    titleEl.classList.remove("text-red-600", "dark:text-red-400");
    
    if (action === "suspend") {
        titleEl.innerText = "Suspend User";
        descEl.innerText = "This will block the user from logging in but will preserve all their data. You can unban them later.";
        btnEl.innerText = "Suspend";
        btnEl.classList.add("bg-yellow-500", "hover:bg-yellow-600");
    } else if (action === "hardban") {
        titleEl.innerText = "Hard Ban (Purge)";
        descEl.innerText = "WARNING: This will permanently ban the user AND ruthlessly delete all their claims, images, and transcripts to save space. This cannot be undone.";
        headerEl.classList.add("bg-red-50", "dark:bg-red-500/10");
        titleEl.classList.add("text-red-600", "dark:text-red-400");
        btnEl.innerText = "Purge & Ban";
        btnEl.classList.add("bg-red-600", "hover:bg-red-700");
    } else if (action === "delete") {
        titleEl.innerText = "Delete Account";
        descEl.innerText = "This will completely scrub the user from the database and free up their email address.";
        btnEl.innerText = "Delete";
        btnEl.classList.add("bg-slate-800", "hover:bg-slate-900", "dark:bg-gray-700", "dark:hover:bg-gray-600");
        reasonContainer.style.display = "none";
    } else if (action === "unban") {
        titleEl.innerText = "Unban User";
        descEl.innerText = "This will allow the user to log in again.";
        btnEl.innerText = "Unban";
        btnEl.classList.add("bg-green-600", "hover:bg-green-700");
        reasonContainer.style.display = "none";
    } else if (action === "clear") {
        titleEl.innerText = "Clear User Data";
        descEl.innerText = "This will wipe all claims, conversations, and images for this user, but keep their account active for testing.";
        btnEl.innerText = "Clear Data";
        btnEl.classList.add("bg-blue-600", "hover:bg-blue-700");
        reasonContainer.style.display = "none";
    }
    
    document.getElementById('userActionReason').value = "";
    document.getElementById('userActionModal').classList.remove('hidden');
}

function closeUserActionModal() {
    document.getElementById('userActionModal').classList.add('hidden');
    activeUserId = null;
    activeUserAction = null;
    document.getElementById('userActionTitle').classList.remove("text-red-600", "dark:text-red-400");
}

async function executeUserAction() {
    if (!activeUserId || !activeUserAction) return;
    
    const reason = document.getElementById('userActionReason').value.trim();
    if (['suspend', 'hardban'].includes(activeUserAction) && !reason) {
        alert("Please provide a reason for the audit log.");
        return;
    }
    
    const btn = document.getElementById('confirmUserActionButton');
    const originalText = btn.innerText;
    btn.innerText = "Executing...";
    btn.disabled = true;
    
    try {
        let endpoint = `/api/v1/admin/users/${activeUserId}`;
        let method = "POST";
        let body = null;
        
        if (activeUserAction === "suspend") {
            endpoint += "/ban";
            body = JSON.stringify({ reason: reason, hard_ban: false });
        } else if (activeUserAction === "hardban") {
            endpoint += "/ban";
            body = JSON.stringify({ reason: reason, hard_ban: true });
        } else if (activeUserAction === "unban") {
            endpoint += "/unban";
        } else if (activeUserAction === "delete") {
            method = "DELETE";
        } else if (activeUserAction === "clear") {
            endpoint += "/data";
            method = "DELETE";
        }
        
        const reqOpts = { method, headers };
        if (body) reqOpts.body = body;
        
        const res = await fetch(endpoint, reqOpts);
        if (!res.ok) throw new Error("Action failed");
        
        closeUserActionModal();
        await loadUsers();
    } catch (e) {
        alert("Error executing action: " + e.message);
        btn.innerText = originalText;
        btn.disabled = false;
    }
}

// ── CLAIM MODAL (REVIEW & ACTION) ──

async function openClaimModal(conversationId, claimId) {
    if (!conversationId || conversationId === 'null' || conversationId === 'undefined') {
        alert("This claim does not have a linked conversation transcript.");
        return;
    }

    currentActiveClaim = claimId;
    document.getElementById('modalId').innerText = `ID: ${claimId}`;
    document.getElementById('modalTranscript').innerHTML = '<div class="animate-pulse flex gap-2 items-center"><div class="w-4 h-4 bg-slate-200 dark:bg-white/20 rounded-full"></div> Loading transcript...</div>';
    document.getElementById('modalEvidence').innerHTML = '';
    
    document.getElementById('claimModal').classList.remove('hidden');

    try {
        // Fetch full conversation via the secure admin endpoint
        const res = await fetch(`/api/v1/admin/conversations/${conversationId}`, { headers });
        if (!res.ok) throw new Error("Failed to load claim details");
        const data = await res.json();
        const conv = data.conversation;

        document.getElementById('modalTitle').innerText = conv.title || 'Claim Details';
        
        // Render Transcript
        const tsDiv = document.getElementById('modalTranscript');
        tsDiv.innerHTML = '';
        if (conv.messages && conv.messages.length > 0) {
            conv.messages.forEach(msg => {
                const isUser = msg.role === 'user';
                const color = isUser ? 'text-indigo-600 dark:text-indigo-400' : 'text-slate-600 dark:text-gray-400';
                const sender = isUser ? 'User' : 'Agent';
                tsDiv.insertAdjacentHTML('beforeend', `
                    <div class="mb-3">
                        <span class="text-[10px] uppercase tracking-widest ${color} font-bold mr-2">${sender}:</span>
                        <span class="text-slate-800 dark:text-gray-300 font-light leading-relaxed">${msg.content}</span>
                    </div>
                `);
            });
        } else {
            tsDiv.innerHTML = '<p class="text-slate-500 italic">No transcript available.</p>';
        }

        // Render Evidence
        const evDiv = document.getElementById('modalEvidence');
        if (conv.evidence && conv.evidence.length > 0) {
            conv.evidence.forEach(img => {
                const imgSrc = img.file_path.startsWith('http') ? img.file_path : '/' + img.file_path;
                evDiv.insertAdjacentHTML('beforeend', `
                    <a href="${imgSrc}" target="_blank" class="block w-20 h-20 rounded-lg overflow-hidden border border-slate-200 dark:border-white/10 hover:border-cyan-500 transition-colors">
                        <img src="${imgSrc}" class="w-full h-full object-cover">
                    </a>
                `);
            });
        } else {
            evDiv.innerHTML = '<p class="text-slate-500 italic text-xs">No evidence uploaded.</p>';
        }

    } catch (e) {
        console.error(e);
        document.getElementById('modalTranscript').innerHTML = '<p class="text-red-500">Error loading details.</p>';
    }
}

function closeClaimModal() {
    document.getElementById('claimModal').classList.add('hidden');
    currentActiveClaim = null;
}

async function submitVerdict(verdict) {
    if (!currentActiveClaim) return;

    if (!confirm(`Are you sure you want to ${verdict.toUpperCase()} this claim? This will instantly notify the user.`)) return;

    try {
        const payload = {
            verdict: verdict,
            reasoning: "Manual Admin Review"
        };
        
        const res = await fetch(`/api/v1/admin/claims/${currentActiveClaim}/verdict`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            alert(`Claim successfully ${verdict}! User notified.`);
            closeClaimModal();
            loadData(); // Refresh UI
        } else {
            const err = await res.json();
            alert("Error: " + err.detail);
        }
    } catch (e) {
        console.error("Error submitting verdict", e);
        alert("A network error occurred.");
    }
}

// ── LIVE TELEMETRY (MATRIX) ──
function setupMatrixWebSocket() {
    const wsProto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    wsMatrix = new WebSocket(`${wsProto}://${window.location.host}/ws/admin_matrix?token=${adminToken}`);
    const statusDot = document.getElementById('wsDot');
    const statusLabel = document.getElementById('wsLabel');
    const terminal = document.getElementById('matrixTerminal');

    wsMatrix.onopen = () => {
        statusDot.classList.replace('bg-red-500', 'bg-green-500');
        statusDot.classList.add('animate-pulse', 'shadow-[0_0_8px_rgba(34,197,94,0.8)]');
        statusLabel.innerText = 'Connected';
    };

    wsMatrix.onclose = () => {
        statusDot.classList.replace('bg-green-500', 'bg-red-500');
        statusDot.classList.remove('animate-pulse', 'shadow-[0_0_8px_rgba(34,197,94,0.8)]');
        statusLabel.innerText = 'Disconnected';
        setTimeout(setupMatrixWebSocket, 5000);
    };

    let isTelemetryStreaming = false;

    window.toggleTelemetryStream = function() {
        const toggle = document.getElementById('streamToggle');
        if (toggle) {
            isTelemetryStreaming = toggle.checked;
            if (isTelemetryStreaming) {
                terminal.insertAdjacentHTML('beforeend', `<div class="text-cyan-400 font-bold mt-4 mb-2">> TELEMETRY STREAM ESTABLISHED. LISTENING...</div>`);
            } else {
                terminal.insertAdjacentHTML('beforeend', `<div class="text-yellow-500/70 italic mt-4 mb-2">> TELEMETRY STREAM PAUSED.</div>`);
            }
            terminal.scrollTop = terminal.scrollHeight;
        }
    };

    window.clearTelemetry = function() {
        terminal.innerHTML = '<div class="text-cyan-500/50 italic mb-4">VeriClaim Secure Agent Matrix v8.0</div>';
    };

    wsMatrix.onmessage = (event) => {
        if (!isTelemetryStreaming) return; // Only process if streaming is ON
        try {
            const data = JSON.parse(event.data);
            let color = "text-gray-400";
            if (data.agent) {
                const ag = data.agent.toLowerCase();
                if (ag.includes('vision')) color = "text-blue-400";
                else if (ag.includes('fraud')) color = "text-purple-400";
                else if (ag.includes('assessor')) color = "text-yellow-400";
                else if (ag.includes('orchestrator')) color = "text-green-400";
                else if (ag.includes('user')) color = "text-gray-300";
                else if (ag.includes('admin')) color = "text-red-400 font-bold";
            }
            
            const time = new Date().toISOString().split('T')[1].slice(0, 12);
            const html = `
                <div class="flex gap-3 w-full border-b border-white/5 pb-1 mb-1">
                    <span class="text-gray-600 shrink-0">[${time}]</span>
                    <span class="${color} shrink-0 w-24 overflow-hidden text-ellipsis">&lt;${data.agent || 'SYSTEM'}&gt;</span>
                    <span class="text-gray-300 flex-1 break-words">${data.message}</span>
                </div>
            `;
            
            terminal.insertAdjacentHTML('beforeend', html);
            
            // Performance Limit: Max 100 lines to prevent DOM bloat
            if (terminal.childElementCount > 100) {
                terminal.removeChild(terminal.firstElementChild);
            }
            
            terminal.scrollTop = terminal.scrollHeight;
        } catch (e) {}
    };
}

// ── SPECIAL ACCESS ──
async function executeSpecialAction(type) {
    let msg = "";
    if (type === 'memory') msg = "Are you sure you want to permanently wipe all Agent Memory? They will forget all past cases.";
    if (type === 'logs') msg = "Are you sure you want to wipe all System Logs? This destroys the audit trail.";
    if (type === 'ratelimit') msg = "Are you sure you want to reset all rate limits? This will unblock all IPs immediately.";
    
    if (!confirm(msg)) return;
    
    try {
        const res = await fetch(`/api/v1/admin/system/${type}`, {
            method: 'DELETE',
            headers: headers
        });
        if (res.ok) {
            alert(`Action successfully executed.`);
        } else {
            const err = await res.json();
            alert("Error: " + err.detail);
        }
    } catch (e) {
        alert("A network error occurred.");
    }
}

// ── UTILS ──
function toggleTheme() {
    const html = document.documentElement;
    if (html.classList.contains('dark')) {
        html.classList.remove('dark');
        localStorage.setItem('theme', 'light');
    } else {
        html.classList.add('dark');
        localStorage.setItem('theme', 'dark');
    }
    updateThemeIcon();
}

function updateThemeIcon() {
    const icon = document.getElementById('themeIcon');
    const isDark = document.documentElement.classList.contains('dark');
    if (icon) icon.className = isDark ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
}

async function logout() {
    try {
        await fetch('/api/v1/admin/logout', { method: 'POST', headers });
    } catch (e) {}
    sessionStorage.removeItem('vericlaim_admin_token');
    window.location.reload();
}
