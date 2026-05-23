/**
 * Shared card context menu.
 * Activated by right-click or long-press on any element with:
 *   data-scryfall-id="..."   (required)
 *   data-card-name="..."     (for display only)
 */
(function () {
    'use strict';

    // ── Menu DOM ─────────────────────────────────────────────────────
    const menu = document.createElement('div');
    menu.id = 'cardContextMenu';
    menu.className = 'deck-ctx-menu';
    menu.style.display = 'none';
    menu.innerHTML = `
        <div class="deck-ctx-header">Add to collection</div>
        <div id="collCtxList" class="deck-ctx-list"><div class="deck-ctx-loading">Loading…</div></div>
        <div class="deck-ctx-divider"></div>
        <div class="deck-ctx-header">Add to deck</div>
        <div class="deck-ctx-board-row">
            <label><input type="radio" name="deckCtxBoard" value="main" checked> Main</label>
            <label><input type="radio" name="deckCtxBoard" value="side"> Side</label>
            <label><input type="radio" name="deckCtxBoard" value="commander"> Cmdr</label>
        </div>
        <div id="deckCtxList" class="deck-ctx-list"><div class="deck-ctx-loading">Loading…</div></div>
        <div class="deck-ctx-footer">
            <a href="/decks/new" class="deck-ctx-newlink">+ New deck</a>
        </div>
    `;
    document.body.appendChild(menu);

    // ── Toast DOM ────────────────────────────────────────────────────
    const toast = document.createElement('div');
    toast.id = 'deckToast';
    toast.className = 'deck-toast';
    document.body.appendChild(toast);
    let toastTimer = null;

    function showToast(msg, ok = true) {
        toast.textContent = msg;
        toast.className = 'deck-toast ' + (ok ? 'deck-toast-ok' : 'deck-toast-err');
        toast.style.opacity = '1';
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { toast.style.opacity = '0'; }, 2400);
    }

    // ── State ────────────────────────────────────────────────────────
    let currentScryfallId = null;
    let decksCache        = null;
    let collsCache        = null;
    let longPressTimer    = null;

    function getBoard() {
        return menu.querySelector('input[name="deckCtxBoard"]:checked').value;
    }

    // ── Fetch lists ───────────────────────────────────────────────────
    function loadDecks(cb) {
        if (decksCache !== null) { cb(decksCache); return; }
        fetch('/decks/api/list')
            .then(r => r.json())
            .then(data => { decksCache = data; cb(data); })
            .catch(() => cb([]));
    }

    function loadCollections(cb) {
        if (collsCache !== null) { cb(collsCache); return; }
        fetch('/collection/api/collections')
            .then(r => r.json())
            .then(data => { collsCache = data; cb(data); })
            .catch(() => cb([]));
    }

    // ── Render both lists ─────────────────────────────────────────────
    function renderMenu() {
        // Collections
        const collList = document.getElementById('collCtxList');
        collList.innerHTML = '<div class="deck-ctx-loading">Loading…</div>';
        loadCollections(colls => {
            if (!colls.length) {
                collList.innerHTML = '<div class="deck-ctx-loading">No collections.</div>';
                return;
            }
            collList.innerHTML = colls.map(c =>
                `<div class="deck-ctx-item" data-coll-id="${c.id}" data-coll-name="${c.name}">
                    <span class="deck-ctx-item-name">${c.name}</span>
                 </div>`
            ).join('');
            collList.querySelectorAll('.deck-ctx-item').forEach(el => {
                el.addEventListener('click', () => addToCollection(el.dataset.collId, el.dataset.collName));
            });
        });

        // Decks
        const deckList = document.getElementById('deckCtxList');
        deckList.innerHTML = '<div class="deck-ctx-loading">Loading…</div>';
        loadDecks(decks => {
            if (!decks.length) {
                deckList.innerHTML = '<div class="deck-ctx-loading">No decks yet.</div>';
                return;
            }
            deckList.innerHTML = decks.map(d =>
                `<div class="deck-ctx-item" data-deck-id="${d.id}" data-deck-name="${d.name}">
                    <span class="deck-ctx-item-name">${d.name}</span>
                    <span class="deck-ctx-item-fmt">${d.format}</span>
                 </div>`
            ).join('');
            deckList.querySelectorAll('.deck-ctx-item').forEach(el => {
                el.addEventListener('click', () => addToDeck(el.dataset.deckId, el.dataset.deckName));
            });
        });
    }

    function addToCollection(collId, collName) {
        closeMenu();
        fetch('/collection/quick-add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scryfall_id: currentScryfallId, collection_id: collId ? parseInt(collId) : null })
        })
        .then(r => r.json())
        .then(d => {
            if (d.ok) showToast(`Added ${d.name} → ${collName}`);
            else      showToast(d.error || 'Error', false);
        })
        .catch(() => showToast('Network error', false));
    }

    function addToDeck(deckId, deckName) {
        closeMenu();
        const board = getBoard();
        fetch(`/decks/${deckId}/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scryfall_id: currentScryfallId, board, count: 1 })
        })
        .then(r => r.json())
        .then(d => {
            if (d.ok) showToast(`Added ${d.name} → ${deckName} (${board})`);
            else      showToast(d.error || 'Error', false);
        })
        .catch(() => showToast('Network error', false));
    }

    // ── Show / hide menu ─────────────────────────────────────────────
    function openMenu(x, y, scryfallId, cardName) {
        currentScryfallId = scryfallId;
        renderMenu();
        menu.style.display = 'block';
        const mw = 220, mh = 420;
        let left = x, top = y;
        if (left + mw > window.innerWidth  - 8) left = window.innerWidth  - mw - 8;
        if (top  + mh > window.innerHeight - 8) top  = window.innerHeight - mh - 8;
        if (left < 8) left = 8;
        if (top  < 8) top  = 8;
        menu.style.left = left + 'px';
        menu.style.top  = top  + 'px';
    }

    function closeMenu() {
        menu.style.display = 'none';
    }

    function findCard(el) {
        return el.closest('[data-scryfall-id]');
    }

    // ── Right-click ───────────────────────────────────────────────────
    document.addEventListener('contextmenu', e => {
        const card = findCard(e.target);
        if (!card) return;
        e.preventDefault();
        openMenu(e.clientX, e.clientY, card.dataset.scryfallId, card.dataset.cardName || '');
    });

    // ── Long-press (touch) ────────────────────────────────────────────
    document.addEventListener('touchstart', e => {
        const card = findCard(e.target);
        if (!card) return;
        const touch = e.touches[0];
        longPressTimer = setTimeout(() => {
            openMenu(touch.clientX, touch.clientY, card.dataset.scryfallId, card.dataset.cardName || '');
        }, 420);
    }, { passive: true });

    document.addEventListener('touchend',   () => clearTimeout(longPressTimer));
    document.addEventListener('touchmove',  () => clearTimeout(longPressTimer));
    document.addEventListener('touchcancel',() => clearTimeout(longPressTimer));

    // ── Close on outside click / Escape ──────────────────────────────
    document.addEventListener('click', e => {
        if (!menu.contains(e.target)) closeMenu();
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') closeMenu();
    });
})();
