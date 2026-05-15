(() => {
    'use strict';

    const POLL_MS = 2000;

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

    function renderProductMovementRow(movement) {
        const reasonParts = [];
        if (movement.reference) reasonParts.push(`<code>${escapeHtml(movement.reference)}</code>`);
        reasonParts.push(escapeHtml(movement.reason || '-'));

        return `
            <div class="admin-mov__wrapper" data-movement-id="${escapeHtml(movement.id)}">
                <div class="admin-mov__row" role="row">
                    <span>${escapeHtml(movement.created_at_display)}</span>
                    <span class="admin-mov__type-cell">
                        <span class="admin-badge admin-mov__badge admin-mov__badge--${escapeHtml(movement.movement_type)}">
                            ${escapeHtml(movement.movement_label)}
                        </span>
                    </span>
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

    function renderProductMovements(table, movements) {
        Array.from(table.children).forEach(child => {
            if (!child.classList.contains('admin-table__head')) child.remove();
        });
        if (!movements.length) {
            table.insertAdjacentHTML('beforeend', `
                <div class="admin-empty">
                    <i class="fa-regular fa-folder-open" aria-hidden="true"></i>
                    <p>Nenhuma movimentação registrada.</p>
                </div>
            `);
            return;
        }
        table.insertAdjacentHTML('beforeend', movements.map(renderProductMovementRow).join(''));
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
        if (movementsTable) renderProductMovements(movementsTable, payload.movements || []);
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
                const status = row.querySelector('[data-stock-status]');
                if (qty) qty.innerHTML = `<strong>${escapeHtml(product.estoque)}</strong>`;
                if (status) status.innerHTML = badge(product.status);
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
