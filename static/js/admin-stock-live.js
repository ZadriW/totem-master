(() => {
    'use strict';

    const POLL_MS = 2000;
    const PROMO_ICON_FALLBACK_TITLE = 'Produto com promoção ativa neste evento';

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[char]));
    }

    const fetchJsonOpts = {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
    };

    function mergeHeadersForMethod(method, headers) {
        var T = window.TotemApiErrors;
        if (T && typeof T.csrfFetchHeaders === 'function') {
            return T.csrfFetchHeaders(method, Object.assign({}, headers || {}));
        }
        return Object.assign({}, headers || {});
    }

    function badge(status) {
        return `<span class="admin-badge admin-badge--${escapeHtml(status.kind)}">${escapeHtml(status.label)}</span>`;
    }

    function updateStockStatusCell(row, product) {
        const statusCell = row.querySelector('[data-stock-status]');
        if (!statusCell || !product.status) return;
        const statusBadge = statusCell.querySelector('[data-stock-status-badge]');
        if (statusBadge) {
            statusBadge.className = `admin-badge admin-badge--${product.status.kind}`;
            statusBadge.textContent = product.status.label;
        }
        const promoBadge = statusCell.querySelector('[data-stock-promo-badge]');
        const promoBadgeText = statusCell.querySelector('[data-stock-promo-badge-text]');
        if (promoBadge && promoBadgeText && Object.prototype.hasOwnProperty.call(product, 'active_promo')) {
            const promoName = String(product.promo_name ?? '').trim();
            if (product.active_promo && promoName) {
                promoBadge.removeAttribute('hidden');
                promoBadgeText.textContent = promoName;
            } else {
                promoBadge.setAttribute('hidden', '');
                promoBadgeText.textContent = '';
            }
        }
    }

    function flash(message, category = 'success') {
        const main = document.querySelector('.admin-main');
        if (!main || !message) return;
        const wrap = main.querySelector('.admin-flashes') || document.createElement('div');
        wrap.className = 'admin-flashes';
        if (!wrap.parentElement) main.prepend(wrap);
        const icon = category === 'success' ? 'fa-circle-check' : 'fa-triangle-exclamation';
        const el = document.createElement('div');
        el.className = `admin-flash admin-flash--${category}`;
        el.setAttribute('role', 'status');
        el.innerHTML = `<i class="fa-solid ${icon}" aria-hidden="true"></i><span>${escapeHtml(message)}</span>`;
        wrap.appendChild(el);
        setTimeout(() => el.remove(), 4500);
    }

    function eventBadgeInline(movement) {
        const bg = movement.event_badge_bg;
        const fg = movement.event_badge_fg;
        if (!bg || !fg) return '';
        return ` style="background-color:${escapeHtml(bg)};color:${escapeHtml(fg)};"`;
    }

    function eventCell(movement) {
        const eid = movement.event_id;
        const ename = String(movement.event_name ?? '').trim();
        const hasId = eid !== undefined && eid !== null && eid !== '';
        const attrStyle = eventBadgeInline(movement);
        if (hasId && ename) {
            return `<span class="admin-mov__event-cell"><span class="admin-nav__event-badge admin-mov__event-badge"${attrStyle} title="Evento #${escapeHtml(eid)}">${escapeHtml(ename)}</span></span>`;
        }
        if (hasId) {
            return `<span class="admin-mov__event-cell"><span class="admin-mov__event-unknown" title="Evento #${escapeHtml(eid)} (cadastro indisponível)">#${escapeHtml(eid)}</span></span>`;
        }
        return '<span class="admin-mov__event-cell"><span class="admin-mov__event-none" title="Movimentação no estoque global do catálogo (sem evento)">—</span></span>';
    }

    function renderProductMovementRow(movement) {
        const reasonParts = [];
        if (movement.reference) reasonParts.push(`<code>${escapeHtml(movement.reference)}</code>`);
        reasonParts.push(escapeHtml(movement.reason || '-'));

        return `
            <div class="admin-mov__wrapper" data-movement-id="${escapeHtml(movement.id)}">
                <div class="admin-mov__row admin-mov__row--with-event" role="row">
                    <span>${escapeHtml(movement.created_at_display)}</span>
                    <span class="admin-mov__type-cell">
                        <span class="admin-badge admin-mov__badge admin-mov__badge--${escapeHtml(movement.movement_type)}">
                            ${escapeHtml(movement.movement_label)}
                        </span>
                    </span>
                    ${eventCell(movement)}
                    <span class="admin-table__col--num admin-mov__delta admin-mov__delta--${escapeHtml(movement.delta_kind)}">
                        ${escapeHtml(movement.delta_display)}
                    </span>
                    <span class="admin-table__col--num"><strong>${escapeHtml(movement.balance_after)}</strong></span>
                    <span class="admin-mov__reason">${reasonParts.join(' ')}</span>
                    <span class="admin-mov__user-cell">
                        ${escapeHtml(movement.created_by_display || movement.created_by || '-')}
                    </span>
                </div>
            </div>
        `;
    }

    function readLatestMovementIdFromDom(table) {
        let max = 0;
        if (!table) return max;
        table.querySelectorAll('[data-movement-id]').forEach(el => {
            const id = Number(el.dataset.movementId);
            if (Number.isFinite(id) && id > max) max = id;
        });
        return max;
    }

    const productMovementsLatestByTable = new WeakMap();

    function mergeProductMovements(table, movements) {
        if (!table) return;

        const head = table.querySelector('.admin-table__head');
        if (!head) return;

        let prevLatest = productMovementsLatestByTable.get(table);
        if (prevLatest === undefined) {
            prevLatest = readLatestMovementIdFromDom(table);
            productMovementsLatestByTable.set(table, prevLatest);
        }

        const list = movements || [];
        const nextLatest = list.reduce(
            (max, movement) => Math.max(max, Number(movement.id) || 0),
            prevLatest,
        );

        if (nextLatest <= prevLatest) return;

        const newItems = [];
        for (let i = 0; i < list.length; i += 1) {
            const movement = list[i];
            const mid = Number(movement.id);
            if (!Number.isFinite(mid) || mid <= prevLatest) break;
            if (table.querySelector(`.admin-mov__wrapper[data-movement-id="${mid}"]`)) continue;
            newItems.push(movement);
        }

        productMovementsLatestByTable.set(table, nextLatest);
        if (!newItems.length) return;

        table.querySelector('.admin-empty')?.remove();
        head.insertAdjacentHTML('afterend', newItems.map(renderProductMovementRow).join(''));

        const trimCap = 120;
        while (table.querySelectorAll('.admin-mov__wrapper').length > trimCap) {
            const wrappers = table.querySelectorAll('.admin-mov__wrapper');
            wrappers[wrappers.length - 1].remove();
        }
    }

    function isEditingLiveStockForm(root) {
        const el = document.activeElement;
        if (!el || !root.contains(el)) return false;
        const form = el.closest('[data-live-stock-form]');
        if (!form || !root.contains(form)) return false;
        return el.matches('input:not([type="hidden"]), textarea, select');
    }

    function updateProductView(payload, options = {}) {
        const product = payload.product;
        const root = document.querySelector('[data-admin-product]');
        if (!root || !product) return;

        const forceInputSync = options.forceInputSync === true;
        const skipInputSync = !forceInputSync && isEditingLiveStockForm(root);

        const status = root.querySelector('[data-product-status]');
        const stock = root.querySelector('[data-product-stock]');
        const minStock = root.querySelector('[data-product-min-stock]');
        const stockValue = root.querySelector('[data-product-stock-value]');
        const minInput = root.querySelector('input[name="min_stock"]');
        const exitInput = root.querySelector('input[name="quantity"][max]');
        const exitButton = root.querySelector('.admin-card--saida button[type="submit"]');
        const adjustInput = root.querySelector('input[name="new_stock"]');
        const activeForm = root.querySelector('.admin-product__toggle');
        const movementsTable =
            root.querySelector('[data-product-movements]') ||
            root.parentElement.querySelector('[data-product-movements]');

        if (status) status.innerHTML = badge(product.status);
        if (stock) stock.innerHTML = `<strong>${escapeHtml(product.estoque)}</strong> un.`;
        if (minStock) minStock.textContent = `${product.estoque_minimo} un.`;
        if (stockValue) stockValue.textContent = product.stock_value_display;
        if (!skipInputSync && minInput) {
            minInput.value = product.estoque_minimo;
        }
        /* Inventário: não sobrescrever no poll (evita apagar rascunho); só alinhar ao servidor após POST com forceInputSync. */
        if (forceInputSync && adjustInput) {
            adjustInput.value = product.estoque;
        }
        if (exitInput) exitInput.max = product.estoque;
        if (exitButton) exitButton.disabled = product.estoque <= 0;
        if (activeForm) {
            const hidden = activeForm.querySelector('input[name="active"]');
            const button = activeForm.querySelector('button[type="submit"]');
            if (hidden) hidden.value = product.ativo ? '0' : '1';
            if (button) {
                button.className = product.ativo
                    ? 'admin-btn admin-btn--ghost admin-btn--danger'
                    : 'admin-btn admin-btn--ghost';
                button.innerHTML = product.ativo
                    ? '<i class="fa-solid fa-eye-slash" aria-hidden="true"></i> Desativar produto'
                    : '<i class="fa-solid fa-eye" aria-hidden="true"></i> Ativar produto';
                if (product.ativo) {
                    button.setAttribute('data-confirm', 'Desativar este produto? Ele deixará de aparecer no totem.');
                } else {
                    button.setAttribute('data-confirm', 'Ativar este produto no totem?');
                }
            }
        }
        if (movementsTable) mergeProductMovements(movementsTable, payload.movements || []);
    }

    async function refreshProduct() {
        const root = document.querySelector('[data-admin-product]');
        if (!root) return;
        const response = await fetch(root.dataset.apiUrl, fetchJsonOpts);
        if (!response.ok) return;
        updateProductView(await response.json());
    }

    function setupProductForms() {
        const root = document.querySelector('[data-admin-product]');
        if (!root) return;

        root.querySelectorAll('form[data-live-stock-form]').forEach(form => {
            form.addEventListener('submit', async event => {
                if (event.defaultPrevented) return;
                event.preventDefault();
                const submit = form.querySelector('button[type="submit"]');
                const originalDisabled = submit ? submit.disabled : false;
                let succeeded = false;
                if (submit) submit.disabled = true;
                try {
                    const response = await fetch(form.action, {
                        method: 'POST',
                        body: new FormData(form),
                        headers: mergeHeadersForMethod('POST', {
                            Accept: 'application/json',
                            'X-Requested-With': 'fetch',
                        }),
                    });
                    const TAE = window.TotemApiErrors;
                    const data = TAE
                        ? await TAE.parseJsonSafe(response)
                        : await response.json().catch(() => ({}));
                    if (!response.ok) {
                        throw new Error(
                            TAE
                                ? TAE.messageFromBadResponse(response, data)
                                : (data.error || 'Não foi possível atualizar o estoque.'),
                        );
                    }
                    updateProductView(data, { forceInputSync: true });
                    succeeded = true;
                    if (form.closest('.admin-card--entrada') || form.closest('.admin-card--saida')) {
                        form.reset();
                    }
                    flash(data.message || 'Estoque atualizado.');
                } catch (err) {
                    const TAE = window.TotemApiErrors;
                    flash(
                        TAE ? TAE.formatCatchMessage(err) : (err.message || 'Não foi possível atualizar o estoque.'),
                        'error',
                    );
                } finally {
                    if (submit && (!succeeded || !form.closest('.admin-card--saida'))) {
                        submit.disabled = originalDisabled;
                    }
                }
            });
        });

        setInterval(refreshProduct, POLL_MS);
    }

    async function refreshStockList() {
        const tables = document.querySelectorAll(
            '[data-admin-stock-list], [data-seller-stock-list], [data-admin-event-stock]',
        );
        if (!tables.length) return;
        for (const table of tables) {
            const url = table.dataset.apiUrl;
            if (!url) continue;
            const response = await fetch(url, fetchJsonOpts);
            if (!response.ok) continue;
            const data = await response.json();
            const byId = new Map((data.products || []).map(product => [String(product.id), product]));
            table.querySelectorAll('[data-product-id]').forEach(row => {
                const product = byId.get(String(row.dataset.productId));
                if (!product) return;
                const qty = row.querySelector('[data-stock-qty]');
                const promoWrap = row.querySelector('[data-stock-promo-wrap]');
                const promoIcon = row.querySelector('[data-stock-promo-icon]');
                const promoTipPanel = row.querySelector('[data-stock-promo-tooltip-panel]');
                if (qty) qty.innerHTML = `<strong>${escapeHtml(product.estoque)}</strong>`;
                updateStockStatusCell(row, product);
                const promoRoot = promoWrap || promoIcon;
                if (promoRoot && promoIcon && Object.prototype.hasOwnProperty.call(product, 'active_promo')) {
                    if (product.active_promo) {
                        if (promoWrap) promoWrap.removeAttribute('hidden');
                        else promoIcon.removeAttribute('hidden');
                        const tip = String(product.promo_tooltip ?? '').trim();
                        if (promoTipPanel) {
                            promoTipPanel.textContent = tip;
                            promoTipPanel.classList.toggle('admin-stock__promo-tooltip-panel--empty', !tip);
                        }
                        promoIcon.title = tip || PROMO_ICON_FALLBACK_TITLE;
                        if (tip) promoIcon.setAttribute('aria-label', `Promoções: ${tip}`);
                        else promoIcon.setAttribute('aria-label', 'Em promoção');
                    } else {
                        if (promoWrap) promoWrap.setAttribute('hidden', '');
                        else promoIcon.setAttribute('hidden', '');
                        if (promoTipPanel) {
                            promoTipPanel.textContent = '';
                            promoTipPanel.classList.add('admin-stock__promo-tooltip-panel--empty');
                        }
                        promoIcon.removeAttribute('title');
                        promoIcon.setAttribute('aria-label', 'Em promoção');
                    }
                }
            });
            const subtitle = document.querySelector('[data-seller-stock-subtitle]');
            if (subtitle && data.stock) {
                const s = data.stock;
                subtitle.innerHTML =
                    `${escapeHtml(s.products_count)} produtos cadastrados &middot; `
                    + `${escapeHtml(s.units_in_stock)} unidades &middot; `
                    + `<strong>${escapeHtml(s.below_min)}</strong> abaixo do mínimo. `
                    + 'Visualização somente leitura.';
            }
        }
    }

    function setupStockList() {
        if (!document.querySelector('[data-admin-stock-list], [data-seller-stock-list], [data-admin-event-stock]')) return;
        setInterval(refreshStockList, POLL_MS);
    }

    setupProductForms();
    setupStockList();
})();
