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

    function customerDetails(movement) {
        if (!movement.has_customer_details) return '';
        const address = movement.client_address || movement.client_number || movement.client_city
            ? `${escapeHtml(movement.client_address || '—')}${movement.client_number ? `, ${escapeHtml(movement.client_number)}` : ''}${movement.client_complement ? ` — ${escapeHtml(movement.client_complement)}` : ''}<br>${escapeHtml(movement.client_city || '—')}${movement.client_state ? ` — ${escapeHtml(movement.client_state)}` : ''}`
            : '—';

        const payLabel = (() => {
            const v = String(movement.payment_method || '').toLowerCase();
            if (v === 'pix') return 'PIX';
            if (v === 'cartao') return 'Cartão';
            return '—';
        })();

        return `
            <div class="admin-mov__details" id="details-${escapeHtml(movement.id)}" hidden>
                <div class="admin-mov__details-content">
                    <h4 class="admin-mov__details-title">
                        <i class="fa-solid fa-user" aria-hidden="true"></i>
                        Dados do cliente (pedido)
                    </h4>
                    <dl class="admin-mov__details-list">
                        <div class="admin-mov__details-item">
                            <dt>Nome</dt>
                            <dd>${escapeHtml(movement.client_name || '—')}</dd>
                        </div>
                        <div class="admin-mov__details-item">
                            <dt>CPF</dt>
                            <dd>${escapeHtml(movement.client_cpf || '—')}</dd>
                        </div>
                        <div class="admin-mov__details-item">
                            <dt>Forma de pagamento</dt>
                            <dd>${escapeHtml(payLabel)}</dd>
                        </div>
                        <div class="admin-mov__details-item">
                            <dt>CEP</dt>
                            <dd>${escapeHtml(movement.client_zipcode || '—')}</dd>
                        </div>
                        <div class="admin-mov__details-item admin-mov__details-item--wide">
                            <dt>Endereço</dt>
                            <dd>${address}</dd>
                        </div>
                    </dl>
                </div>
            </div>
        `;
    }

    function renderProductMovementRow(movement) {
        const reasonParts = [];
        if (movement.reference) reasonParts.push(`<code>${escapeHtml(movement.reference)}</code>`);
        reasonParts.push(escapeHtml(movement.reason || '-'));

        const toggle = movement.has_customer_details
            ? `<button type="button" class="admin-mov__toggle" data-toggle="${escapeHtml(movement.id)}" aria-expanded="false" aria-controls="details-${escapeHtml(movement.id)}" aria-label="Exibir ou ocultar dados do cliente">
                    <i class="fa-solid fa-chevron-down" aria-hidden="true"></i>
               </button>`
            : '';

        const receipt = movement.receipt_url
            ? `<a href="${escapeHtml(movement.receipt_url)}" target="_blank" rel="noopener noreferrer" class="admin-mov__note-btn" title="Abrir nota de retirada (${escapeHtml(movement.reference)})" aria-label="Abrir nota de retirada do pedido ${escapeHtml(movement.reference)}">
                    <i class="fa-solid fa-clipboard-list" aria-hidden="true"></i>
               </a>`
            : '';

        return `
            <div class="admin-mov__wrapper" data-movement-id="${escapeHtml(movement.id)}">
                <div class="admin-mov__row" role="row">
                    <span>${escapeHtml(movement.created_at_display)}</span>
                    <span class="admin-mov__type-cell">
                        <span class="admin-badge admin-mov__badge admin-mov__badge--${escapeHtml(movement.movement_type)}">
                            ${escapeHtml(movement.movement_label)}
                        </span>
                        ${toggle}
                    </span>
                    <span class="admin-table__col--num admin-mov__delta admin-mov__delta--${escapeHtml(movement.delta_kind)}">
                        ${escapeHtml(movement.delta_display)}
                    </span>
                    <span class="admin-table__col--num"><strong>${escapeHtml(movement.balance_after)}</strong></span>
                    <span class="admin-mov__reason">${reasonParts.join(' ')}</span>
                    <span class="admin-mov__user-cell">
                        ${escapeHtml(movement.created_by_display || movement.created_by || '-')}
                        ${receipt}
                    </span>
                </div>
                ${customerDetails(movement)}
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

    function setupProductMovementToggles() {
        const table = document.querySelector('[data-product-movements]');
        if (!table || table.dataset.toggleBound === '1') return;
        table.dataset.toggleBound = '1';

        function closeAllDetails() {
            table.querySelectorAll('.admin-mov__details').forEach(panel => {
                panel.hidden = true;
            });
            table.querySelectorAll('.admin-mov__toggle').forEach(b => {
                b.setAttribute('aria-expanded', 'false');
                const i = b.querySelector('i');
                if (i) i.className = 'fa-solid fa-chevron-down';
            });
        }

        table.addEventListener('click', event => {
            const btn = event.target.closest('.admin-mov__toggle');
            if (!btn || !table.contains(btn)) return;
            const movId = btn.dataset.toggle;
            const details = document.getElementById(`details-${movId}`);
            const icon = btn.querySelector('i');
            if (!details) return;

            const willOpen = details.hidden;
            if (willOpen) {
                closeAllDetails();
                details.hidden = false;
                if (icon) icon.className = 'fa-solid fa-chevron-up';
                btn.setAttribute('aria-expanded', 'true');
            } else {
                details.hidden = true;
                if (icon) icon.className = 'fa-solid fa-chevron-down';
                btn.setAttribute('aria-expanded', 'false');
            }
        });
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
        const movementsTable = root.parentElement.querySelector('[data-product-movements]');

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
                        headers: {
                            Accept: 'application/json',
                            'X-Requested-With': 'fetch',
                        },
                    });
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.error || 'Não foi possível atualizar o estoque.');
                    updateProductView(data, { forceInputSync: true });
                    succeeded = true;
                    if (form.closest('.admin-card--entrada') || form.closest('.admin-card--saida')) {
                        form.reset();
                    }
                    flash(data.message || 'Estoque atualizado.');
                } catch (err) {
                    flash(err.message || 'Não foi possível atualizar o estoque.', 'error');
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

    setupProductMovementToggles();
    setupProductForms();
    setupStockList();
})();
