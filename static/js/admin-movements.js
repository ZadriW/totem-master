(() => {
    'use strict';

    const POLL_MS = 2000;
    const table =
        document.querySelector('[data-admin-movements]') ||
        document.querySelector('.admin-table');
    const scope = table || document;
    let latestId = Number(
        (table && table.querySelector('[data-movement-id]') || {}).dataset?.movementId || 0
    );

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[char]));
    }

    function closeAllDetails() {
        scope.querySelectorAll('.admin-mov__details').forEach(panel => {
            panel.hidden = true;
        });
        scope.querySelectorAll('.admin-mov__toggle').forEach(b => {
            b.setAttribute('aria-expanded', 'false');
            const i = b.querySelector('i');
            if (i) i.className = 'fa-solid fa-chevron-down';
        });
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

    function renderMovement(movement) {
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
        const reason = `${movement.reference ? `<code>${escapeHtml(movement.reference)}</code>` : ''} ${escapeHtml(movement.reason || '-')}`;

        return `
            <div class="admin-mov__wrapper" data-movement-id="${escapeHtml(movement.id)}">
                <div class="admin-mov__row admin-mov__row--wide" role="row">
                    <span>${escapeHtml(movement.created_at_display)}</span>
                    <span class="admin-mov__product">
                        <a href="${escapeHtml(movement.product_url)}">${escapeHtml(movement.product_name || '—')}</a>
                        <small>#${escapeHtml(movement.product_id)} &middot; ${escapeHtml(movement.product_category || '-')}</small>
                    </span>
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
                    <span class="admin-mov__reason">${reason}</span>
                    <span class="admin-mov__user-cell">
                        ${escapeHtml(movement.created_by || '-')}
                        ${receipt}
                    </span>
                </div>
                ${customerDetails(movement)}
            </div>
        `;
    }

    function renderMovements(movements) {
        if (!table) return;
        Array.from(table.children).forEach(child => {
            if (!child.classList.contains('admin-table__head')) child.remove();
        });
        if (!movements.length) {
            table.insertAdjacentHTML('beforeend', `
                <div class="admin-empty">
                    <i class="fa-regular fa-folder-open" aria-hidden="true"></i>
                    <p>Nenhuma movimentação encontrada para os filtros aplicados.</p>
                </div>
            `);
            return;
        }
        table.insertAdjacentHTML('beforeend', movements.map(renderMovement).join(''));
    }

    async function refreshMovements() {
        if (!table || !table.dataset.apiUrl) return;
        const response = await fetch(table.dataset.apiUrl, { headers: { Accept: 'application/json' } });
        if (!response.ok) return;
        const data = await response.json();
        const nextLatest = Number(data.latest_id || 0);
        if (nextLatest === latestId) return;
        latestId = nextLatest;
        renderMovements(data.movements || []);
    }

    scope.addEventListener('click', event => {
        const btn = event.target.closest('.admin-mov__toggle');
        if (!btn) return;
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

    if (table && table.dataset.apiUrl) {
        setInterval(refreshMovements, POLL_MS);
    }
})();
