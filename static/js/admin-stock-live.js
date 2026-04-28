(() => {
    'use strict';

    const POLL_MS = 2000;
    /** Polling do dashboard do vendedor (`/vendedor/api/dashboard`) — menos frequente que estoque/produto admin. */
    const SELLER_DASHBOARD_POLL_MS = 10000;

    /** Última “assinatura” da lista de transações — só atualiza o DOM quando mudar (fetch não recarrega a página; substituir HTML inteiro fechava os accordions). */
    let lastSellerDashboardTxFingerprint = null;

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[char]));
    }

    function formatBrl(value) {
        try {
            return Number(value ?? 0).toLocaleString('pt-BR', {
                style: 'currency',
                currency: 'BRL',
            });
        } catch {
            return String(value ?? '');
        }
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
                        ${escapeHtml(movement.created_by || '-')}
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

    function updateProductView(payload) {
        const product = payload.product;
        const root = document.querySelector('[data-admin-product]');
        if (!root || !product) return;

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
        if (minInput && document.activeElement !== minInput) minInput.value = product.estoque_minimo;
        if (exitInput) exitInput.max = product.estoque;
        if (exitButton) exitButton.disabled = product.estoque <= 0;
        if (adjustInput && document.activeElement !== adjustInput) adjustInput.value = product.estoque;
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
                    updateProductView(data);
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
        const tables = document.querySelectorAll('[data-admin-stock-list], [data-seller-stock-list]');
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

    function setDashText(key, text) {
        document.querySelectorAll(`[data-dash-kpi="${key}"]`).forEach(el => {
            el.textContent = text;
        });
    }

    function renderSellerTransaction(tx) {
        const badgeKind = tx.status === 'confirmado' ? 'success' : 'neutral';
        const items = tx.items || [];
        const n = items.length;
        const prodWord = n === 1 ? 'produto' : 'produtos';
        const rowsHtml = items.map(it => `
            <div class="admin-tx__items-row">
                <span class="admin-tx__product">${escapeHtml(it.product_name)}</span>
                <span>${it.product_sku ? `<code>${escapeHtml(it.product_sku)}</code>` : '—'}</span>
                <span>${escapeHtml(it.category || '-')}</span>
                <span class="admin-table__col--num">${escapeHtml(it.quantity)}</span>
                <span class="admin-table__col--num">${escapeHtml(it.unit_price_display)}</span>
                <span class="admin-table__col--num"><strong>${escapeHtml(it.subtotal_display)}</strong></span>
            </div>
        `).join('');
        return `
            <div class="admin-tx" data-tx-id="${escapeHtml(tx.id)}">
                <button
                    type="button"
                    class="admin-tx__row"
                    aria-expanded="false"
                    aria-controls="seller-tx-details-${escapeHtml(tx.id)}"
                >
                    <span class="admin-table__col admin-table__col--toggle">
                        <i class="fa-solid fa-chevron-right admin-tx__chevron" aria-hidden="true"></i>
                    </span>
                    <span class="admin-table__col admin-tx__order"><strong>#${escapeHtml(tx.order_number)}</strong></span>
                    <span class="admin-table__col">${escapeHtml(tx.created_at_display)}</span>
                    <span class="admin-table__col admin-table__col--num">${escapeHtml(tx.items_count)}</span>
                    <span class="admin-table__col admin-table__col--num"><strong>${escapeHtml(tx.total_display)}</strong></span>
                    <span class="admin-table__col">
                        <span class="admin-badge admin-badge--${badgeKind}">
                            ${escapeHtml(tx.status)}
                        </span>
                    </span>
                </button>
                <div class="admin-tx__details" id="seller-tx-details-${escapeHtml(tx.id)}" hidden>
                    <div class="admin-tx__details-inner">
                        <h3 class="admin-tx__details-title">
                            Itens do pedido
                            <span class="admin-tx__details-count">
                                ${escapeHtml(n)} ${prodWord}
                            </span>
                        </h3>
                        <div class="admin-tx__items">
                            <div class="admin-tx__items-head">
                                <span>Produto</span>
                                <span>SKU</span>
                                <span>Categoria</span>
                                <span class="admin-table__col--num">Qtd.</span>
                                <span class="admin-table__col--num">Unit.</span>
                                <span class="admin-table__col--num">Subtotal</span>
                            </div>
                            ${rowsHtml}
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    function renderSellerTransactionsRoot(transactions) {
        if (!transactions.length) {
            return `
                <div class="admin-empty">
                    <i class="fa-regular fa-folder-open" aria-hidden="true"></i>
                    <p>Nenhuma transação registrada até o momento.</p>
                </div>
            `;
        }
        return transactions.map(renderSellerTransaction).join('');
    }

    function sellerTransactionsFingerprint(data) {
        const txs = data.transactions || [];
        const lid = data.latest_tx_id ?? 0;
        if (!txs.length) {
            return `empty:${lid}`;
        }
        return `${lid}:${txs.map(t => String(t.id)).join(',')}`;
    }

    function collectExpandedSellerTxIds(txRoot) {
        if (!txRoot) return [];
        const ids = [];
        txRoot.querySelectorAll('.admin-tx .admin-tx__row').forEach(btn => {
            if (btn.getAttribute('aria-expanded') !== 'true') return;
            const wrap = btn.closest('.admin-tx');
            const id = wrap && wrap.getAttribute('data-tx-id');
            if (id != null && id !== '') ids.push(String(id));
        });
        return ids;
    }

    function restoreSellerExpandedTransactions(txRoot, ids) {
        if (!txRoot || !ids.length) return;
        const open = new Set(ids);
        txRoot.querySelectorAll('.admin-tx').forEach(wrap => {
            const id = wrap.getAttribute('data-tx-id');
            if (id == null || !open.has(String(id))) return;
            const btn = wrap.querySelector('.admin-tx__row');
            const details = wrap.querySelector('.admin-tx__details');
            if (!btn || !details) return;
            btn.setAttribute('aria-expanded', 'true');
            btn.classList.add('is-open');
            details.hidden = false;
        });
    }

    async function refreshSellerDashboard() {
        const mount = document.getElementById('seller-dashboard-live');
        if (!mount || !mount.dataset.apiUrl) return;
        const response = await fetch(mount.dataset.apiUrl, fetchJsonOpts);
        if (!response.ok) return;
        const data = await response.json();
        const stats = data.stats || {};
        const stock = data.stock || {};
        const transactions = data.transactions || [];

        setDashText('transactions_count', String(stats.transactions_count ?? ''));
        setDashText('transactions_today', `${stats.transactions_today ?? 0} hoje`);
        setDashText('total_revenue', formatBrl(stats.total_revenue));
        setDashText('revenue_today', `${formatBrl(stats.revenue_today)} hoje`);
        setDashText('items_sold', String(stats.items_sold ?? ''));
        setDashText('below_min', String(stock.below_min ?? ''));
        setDashText('out_of_stock', `${stock.out_of_stock ?? 0} sem estoque`);
        setDashText('products_count', String(stock.products_count ?? ''));
        setDashText('products_active', `${stock.products_active ?? 0} ativos`);
        setDashText('units_in_stock', String(stock.units_in_stock ?? ''));
        setDashText('stock_value', formatBrl(stock.stock_value));

        const warnCard = document.querySelector('[data-dash-kpi-card="below_min"]');
        if (warnCard) {
            warnCard.classList.toggle('kpi--warn', Number(stock.below_min) > 0);
        }

        const subtitle = document.querySelector('[data-seller-tx-subtitle]');
        if (subtitle) {
            const n = transactions.length;
            subtitle.textContent = `Exibindo ${n} ${n === 1 ? 'transação' : 'transações'}.`;
        }

        const fp = sellerTransactionsFingerprint(data);
        const txRoot = document.querySelector('[data-seller-transactions-root]');
        if (!txRoot) return;

        if (fp === lastSellerDashboardTxFingerprint) {
            return;
        }

        const expandedIds = collectExpandedSellerTxIds(txRoot);
        lastSellerDashboardTxFingerprint = fp;
        txRoot.innerHTML = renderSellerTransactionsRoot(transactions);
        restoreSellerExpandedTransactions(txRoot, expandedIds);
    }

    function setupStockList() {
        if (!document.querySelector('[data-admin-stock-list], [data-seller-stock-list]')) return;
        setInterval(refreshStockList, POLL_MS);
    }

    function setupSellerDashboard() {
        if (!document.getElementById('seller-dashboard-live')) return;
        setInterval(refreshSellerDashboard, SELLER_DASHBOARD_POLL_MS);
    }

    setupProductMovementToggles();
    setupProductForms();
    setupStockList();
    setupSellerDashboard();
})();
