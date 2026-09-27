// ==UserScript==
// @name         TornIntel Bank Request
// @namespace    http://tampermonkey.net/
// @version      0.5.0
// @description  Request money from the faction vault; posts to the TornIntel Discord bot with a prefilled fulfill link.
// @author       TornIntel
// @match        https://www.torn.com/*
// @match        https://torn.com/*
// @connect      *
// @grant        GM_xmlhttpRequest
// @grant        GM_addStyle
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @homepageURL  https://github.com/xDp64xG/Torn-Intel
// @supportURL   https://github.com/xDp64xG/Torn-Intel/issues
// @updateURL    https://raw.githubusercontent.com/xDp64xG/Torn-Intel/main/scripts/tampermonkey/bank_request.user.js
// @downloadURL  https://raw.githubusercontent.com/xDp64xG/Torn-Intel/main/scripts/tampermonkey/bank_request.user.js
// ==/UserScript==

(() => {
    'use strict';

    const DEFAULT_BASE_URLS = ['http://127.0.0.1:8765', 'http://localhost:8765'];
    const DISCOVERY_URL = 'https://raw.githubusercontent.com/xDp64xG/Torn-Intel/main/scripts/tampermonkey/revive_request_endpoint.json';
    const OVERRIDE_KEY = 'tornintel_bank_listener_override';
    const BUTTON_POSITION_KEY = 'tornintel_bank_button_position';
    const BUTTON_ID = 'tornintel-bank-btn';
    const MODAL_ID = 'tornintel-bank-modal';
    const MAX_AMOUNT = 1e12;

    const trimSlash = url => String(url || '').replace(/\/+$/, '');
    const isHttpUrl = url => /^https?:\/\/.+/i.test(String(url || ''));

    const getValue = (key, fallback = '') => {
        try { return typeof GM_getValue === 'function' ? GM_getValue(key, fallback) : fallback; } catch { return fallback; }
    };
    const setValue = (key, value) => {
        try { if (typeof GM_setValue === 'function') GM_setValue(key, value); } catch { /* ignore */ }
    };

    const request = (method, url, data = null, timeoutMs = 4000) => new Promise((resolve, reject) => {
        GM_xmlhttpRequest({
            method,
            url,
            headers: { 'Content-Type': 'application/json' },
            data: data ? JSON.stringify(data) : undefined,
            timeout: timeoutMs,
            onload: r => {
                let body = null;
                try { body = JSON.parse(r.responseText); } catch { body = null; }
                if (r.status >= 200 && r.status < 300) resolve(body || { ok: true });
                else reject(new Error(body?.error || `HTTP ${r.status}`));
            },
            ontimeout: () => reject(new Error('Listener timed out')),
            onerror: () => reject(new Error(`Request failed (${method} ${url})`))
        });
    });

    let activeBaseUrl = null;

    const resolveBaseUrl = async () => {
        if (activeBaseUrl) return activeBaseUrl;
        let discovered = [];
        try {
            const res = await request('GET', DISCOVERY_URL);
            discovered = [...(Array.isArray(res?.base_urls) ? res.base_urls : []), res?.base_url];
        } catch { /* discovery optional */ }

        const candidates = [...new Set([getValue(OVERRIDE_KEY, ''), ...discovered, ...DEFAULT_BASE_URLS]
            .map(trimSlash).filter(isHttpUrl))];

        for (const baseUrl of candidates) {
            try {
                const res = await request('GET', `${baseUrl}/health`, null, 2500);
                if (res?.ok) {
                    activeBaseUrl = baseUrl;
                    return baseUrl;
                }
            } catch { /* try next */ }
        }
        throw new Error(
            `No reachable TornIntel listener. Start it with: python main.py revive_listener serve --host 0.0.0.0 --port 8765. ` +
            `For an internet tunnel, refresh the ephemeral trycloudflare URL in scripts/tampermonkey/revive_request_endpoint.json. ` +
            `Tried: ${candidates.join(', ')}`
        );
    };

    if (typeof GM_registerMenuCommand === 'function') {
        GM_registerMenuCommand('TornIntel Bank: Set/Clear Listener URL', () => {
            const input = window.prompt('Listener URL override (blank = auto discovery):', getValue(OVERRIDE_KEY, ''));
            if (input === null) return;
            const url = trimSlash(input);
            if (url && !isHttpUrl(url)) {
                window.alert('Invalid URL. Example: http://192.168.1.50:8765');
                return;
            }
            setValue(OVERRIDE_KEY, url);
            activeBaseUrl = null;
        });
    }

    // Name comes from the sidebar link: <a href="/profiles.php?XID=..." aria-label="Name: JeffBezas">JeffBezas</a>
    const getCurrentUser = () => {
        const link = document.querySelector('a[class*="menu-value"][href*="profiles.php?XID="][aria-label^="Name:"]')
            || document.querySelector('a[href*="profiles.php?XID="][aria-label^="Name:"]');
        if (link) {
            const name = (link.textContent || '').trim()
                || String(link.getAttribute('aria-label') || '').replace(/^Name:\s*/, '').trim();
            const id = link.getAttribute('href')?.match(/XID=(\d+)/)?.[1];
            if (name) return { name, id: id ? Number(id) : null };
        }
        try {
            const key = Object.keys(sessionStorage).find(k => /sidebarData\d+/.test(k));
            const user = key ? JSON.parse(sessionStorage.getItem(key))?.user : null;
            if (user?.name) return { name: String(user.name), id: user.userID ? Number(user.userID) : null };
        } catch { /* ignore */ }
        return { name: null, id: null };
    };

    // Accepts "1500000", "1,500,000", "1.5m", "250k", "2b", "all". The listener caps it to your vault balance.
    const parseAmount = (text) => {
        const cleaned = String(text || '').trim().toLowerCase().replace(/[,$\s]/g, '');
        if (cleaned === 'all' || cleaned === 'max') return 'all';
        const match = cleaned.match(/^(\d+(?:\.\d+)?)([kmb])?$/);
        if (!match) return null;
        const mult = { k: 1e3, m: 1e6, b: 1e9 }[match[2]] || 1;
        const value = Math.round(Number(match[1]) * mult);
        return Number.isFinite(value) && value > 0 && value <= MAX_AMOUNT ? value : null;
    };

    GM_addStyle(`
        #${BUTTON_ID} {
            position: fixed; left: 12px; bottom: calc(12px + env(safe-area-inset-bottom, 0px)); z-index: 2147483645;
            padding: 5px 10px; border: none; border-radius: 6px; cursor: grab; touch-action: none;
            background: #2e7d32; color: #fff; font-size: 12px; font-weight: 700;
            box-shadow: 0 4px 12px rgba(0,0,0,0.35);
        }
        #${MODAL_ID} {
            position: fixed; inset: 0; z-index: 2147483646; display: flex; align-items: center; justify-content: center;
            background: rgba(0,0,0,0.55);
        }
        #${MODAL_ID} .ti-bank-box {
            width: min(300px, calc(100vw - 32px)); padding: 14px; border-radius: 8px;
            background: #1f242b; color: #eef2f6; font-size: 13px; box-shadow: 0 12px 28px rgba(0,0,0,0.45);
        }
        #${MODAL_ID} .ti-bank-title { font-weight: 700; font-size: 14px; margin-bottom: 8px; }
        #${MODAL_ID} .ti-bank-name { margin-bottom: 10px; color: #b8c4d0; }
        #${MODAL_ID} input {
            width: 100%; box-sizing: border-box; padding: 7px 8px; border-radius: 4px;
            border: 1px solid #3a434e; background: #12161b; color: #fff; font-size: 13px;
        }
        #${MODAL_ID} .ti-bank-status { min-height: 16px; margin-top: 6px; font-size: 12px; }
        #${MODAL_ID} .ti-bank-status.err { color: #ff7b7b; }
        #${MODAL_ID} .ti-bank-status.ok { color: #6fdc8c; }
        #${MODAL_ID} .ti-bank-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
        #${MODAL_ID} button { padding: 6px 12px; border: none; border-radius: 4px; cursor: pointer; font-weight: 700; color: #fff; }
        #${MODAL_ID} .ti-bank-cancel { background: #555e69; }
        #${MODAL_ID} .ti-bank-confirm { background: #2e7d32; }
        #${MODAL_ID} button:disabled { opacity: 0.6; cursor: default; }
    `);

    const closeModal = () => document.getElementById(MODAL_ID)?.remove();

    const openModal = () => {
        if (document.getElementById(MODAL_ID)) return;
        const user = getCurrentUser();

        const overlay = document.createElement('div');
        overlay.id = MODAL_ID;
        overlay.innerHTML = `
            <div class="ti-bank-box" role="dialog" aria-label="Bank request">
                <div class="ti-bank-title">Bank Request</div>
                <div class="ti-bank-name"></div>
                <input type="text" placeholder="Amount (e.g. 5000000, 5m or all)" autocomplete="off">
                <div class="ti-bank-status"></div>
                <div class="ti-bank-actions">
                    <button type="button" class="ti-bank-cancel">Cancel</button>
                    <button type="button" class="ti-bank-confirm">Confirm</button>
                </div>
            </div>`;

        const nameEl = overlay.querySelector('.ti-bank-name');
        const input = overlay.querySelector('input');
        const status = overlay.querySelector('.ti-bank-status');
        const cancelBtn = overlay.querySelector('.ti-bank-cancel');
        const confirmBtn = overlay.querySelector('.ti-bank-confirm');

        nameEl.textContent = user.name ? `Name: ${user.name}` : 'Name: (not found)';

        const setStatus = (text, kind = '') => {
            status.textContent = text;
            status.className = `ti-bank-status ${kind}`;
        };

        const submit = async () => {
            if (!user.name || !user.id) {
                setStatus('Could not find your name/ID on the page.', 'err');
                return;
            }
            const amount = parseAmount(input.value);
            if (!amount) {
                setStatus('Enter a valid amount (e.g. 5m or all).', 'err');
                return;
            }

            const amountLabel = amount === 'all' ? 'your full balance' : `$${amount.toLocaleString()}`;
            confirmBtn.disabled = true;
            cancelBtn.disabled = true;
            setStatus(`Checking balance and requesting ${amountLabel}...`);
            try {
                const baseUrl = await resolveBaseUrl();
                const res = await request('POST', `${baseUrl}/bank-request`, {
                    requester_name: user.name,
                    requester_id: user.id,
                    amount,
                    source: 'tampermonkey-bank',
                    notes: `Requested from ${window.location.pathname}`
                }, 20000);
                if (!res?.ok) throw new Error(res?.error || 'unknown_error');
                const granted = Number(res.request?.amount || 0);
                const capped = res.capped ? ' (capped to your vault balance)' : '';
                setStatus(`Requested $${granted.toLocaleString()} from ${res.faction_name || 'your faction'}${capped}.`, 'ok');
                window.setTimeout(closeModal, 2500);
            } catch (err) {
                activeBaseUrl = null;
                setStatus(err?.message || String(err), 'err');
                confirmBtn.disabled = false;
                cancelBtn.disabled = false;
            }
        };

        cancelBtn.addEventListener('click', closeModal);
        confirmBtn.addEventListener('click', submit);
        overlay.addEventListener('click', (e) => { if (e.target === overlay && !cancelBtn.disabled) closeModal(); });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') submit();
            if (e.key === 'Escape' && !cancelBtn.disabled) closeModal();
        });

        document.body.appendChild(overlay);
        input.focus();
    };

    const mountButton = () => {
        if (!document.body || document.getElementById(BUTTON_ID)) return;
        const btn = document.createElement('button');
        btn.id = BUTTON_ID;
        btn.type = 'button';
        btn.textContent = 'Bank';
        let pointerStart = null;
        let dragged = false;
        const savedPosition = getValue(BUTTON_POSITION_KEY, null);
        if (savedPosition && Number.isFinite(savedPosition.left) && Number.isFinite(savedPosition.top)) {
            btn.style.left = `${savedPosition.left}px`;
            btn.style.top = `${savedPosition.top}px`;
            btn.style.bottom = 'auto';
        }
        btn.addEventListener('pointerdown', event => {
            if (event.button !== 0) return;
            pointerStart = {
                pointerId: event.pointerId,
                x: event.clientX,
                y: event.clientY,
                left: btn.getBoundingClientRect().left,
                top: btn.getBoundingClientRect().top
            };
            dragged = false;
            btn.setPointerCapture(event.pointerId);
        });
        btn.addEventListener('pointermove', event => {
            if (!pointerStart || pointerStart.pointerId !== event.pointerId) return;
            const deltaX = event.clientX - pointerStart.x;
            const deltaY = event.clientY - pointerStart.y;
            if (!dragged && Math.hypot(deltaX, deltaY) < 5) return;
            dragged = true;
            const left = Math.max(0, Math.min(window.innerWidth - btn.offsetWidth, pointerStart.left + deltaX));
            const top = Math.max(0, Math.min(window.innerHeight - btn.offsetHeight, pointerStart.top + deltaY));
            btn.style.left = `${left}px`;
            btn.style.top = `${top}px`;
            btn.style.bottom = 'auto';
            btn.style.cursor = 'grabbing';
        });
        const stopDragging = event => {
            if (!pointerStart || pointerStart.pointerId !== event.pointerId) return;
            if (dragged) {
                const bounds = btn.getBoundingClientRect();
                setValue(BUTTON_POSITION_KEY, { left: bounds.left, top: bounds.top });
            }
            pointerStart = null;
            btn.style.cursor = 'grab';
        };
        btn.addEventListener('pointerup', stopDragging);
        btn.addEventListener('pointercancel', stopDragging);
        btn.addEventListener('click', event => {
            if (dragged) {
                event.preventDefault();
                dragged = false;
                return;
            }
            openModal();
        });
        document.body.appendChild(btn);
    };

    mountButton();
    window.setInterval(mountButton, 5000);
})();
