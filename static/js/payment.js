/**
 * Página /pagamento — apenas resumo do pedido.
 * O cliente segue para /pagamento/aguardando para usar a maquininha e confirmar.
 */
(() => {
    'use strict';

    const Cart = window.Cart;
    if (!Cart) return;

    const FLOW = window.__TOTEM_FLOW__ || {};
    const WAITING_URL = FLOW.paymentWaiting || '/vendedor/pagamento/aguardando';
    const CATALOG_URL = FLOW.catalog || '/vendedor/venda';
    const RESUME_PENDING_TX_KEY = 'totem_resume_pending_tx_id';

    function readResumePendingTxId() {
        try {
            const raw = sessionStorage.getItem(RESUME_PENDING_TX_KEY);
            return raw && /^\d+$/.test(raw.trim()) ? raw.trim() : null;
        } catch (_) {
            return null;
        }
    }

    function clearResumePendingTxId() {
        try {
            sessionStorage.removeItem(RESUME_PENDING_TX_KEY);
        } catch (_) {}
    }

    function waitingUrlWithOptionalResume(baseUrl, txId) {
        const sep = baseUrl.includes('?') ? '&' : '?';
        return `${baseUrl}${sep}pendente=${encodeURIComponent(txId)}`;
    }

    const itemsEl = document.getElementById('paymentItems');
    const countEl = document.getElementById('paymentCount');
    const subtotalEl = document.getElementById('paymentSubtotal');
    const totalEl = document.getElementById('paymentTotal');
    const continueBtn = document.getElementById('paymentContinue');
    const cancelBtn = document.getElementById('paymentCancel');

    function renderItem(item) {
        const subtotal = Cart.formatBRL(item.preco * item.quantidade);
        const unit = Cart.formatBRL(item.preco);
        return `
            <article class="payment-item" data-id="${item.id}">
                <div class="payment-item__image">
                    <img src="${item.imagem}" alt="${item.nome}" loading="lazy">
                </div>
                <div class="payment-item__info">
                    <span class="payment-item__category">${item.categoria || ''}</span>
                    <h3 class="payment-item__name">${item.nome}</h3>
                    ${item.sku ? `<p class="payment-item__sku">SKU ${item.sku}</p>` : ''}
                    <p class="payment-item__meta">
                        ${item.quantidade} × ${unit}
                    </p>
                </div>
                <div class="payment-item__total">${subtotal}</div>
            </article>
        `;
    }

    function renderSummary() {
        const items = Cart.getItems();
        if (items.length === 0) {
            clearResumePendingTxId();
            window.location.replace(CATALOG_URL);
            return;
        }
        itemsEl.innerHTML = items.map(renderItem).join('');
        countEl.textContent = Cart.count();
        const total = Cart.total();
        subtotalEl.textContent = Cart.formatBRL(total);
        totalEl.textContent = Cart.formatBRL(total);
    }

    continueBtn.addEventListener('click', () => {
        if (Cart.isEmpty()) return;
        if (!window.PaymentForm || !window.PaymentForm.save()) {
            return;
        }
        const resumeId = readResumePendingTxId();
        const targetUrl = resumeId ? waitingUrlWithOptionalResume(WAITING_URL, resumeId) : WAITING_URL;
        window.location.assign(targetUrl);
    });

    cancelBtn.addEventListener('click', () => {
        clearResumePendingTxId();
        window.location.assign(CATALOG_URL);
    });

    Cart.subscribe(() => {
        renderSummary();
        if (window.PaymentForm && typeof window.PaymentForm.syncInstallmentsFromCart === 'function') {
            window.PaymentForm.syncInstallmentsFromCart();
        }
    });

    renderSummary();
})();
