/**
 * Página /pagamento — apenas resumo do pedido.
 * O cliente segue para /pagamento/aguardando para usar a maquininha e confirmar.
 */
(() => {
    'use strict';

    const Cart = window.Cart;
    const PromoPricing = window.PromoPricing;
    if (!Cart) return;

    const FLOW = window.__TOTEM_FLOW__ || {};
    const WAITING_URL = FLOW.paymentWaiting || '/vendedor/pagamento/aguardando';
    const CATALOG_URL = FLOW.catalog || '/vendedor/venda';
    const RESUME_PENDING_TX_KEY = 'totem_resume_pending_tx_id';
    const QUOTE_API = '/api/carrinho/cotacao';
    /** Intervalo entre cotações promocionais no servidor (POST /api/carrinho/cotacao). */
    const QUOTE_POLL_MS = 5000;

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
    const discountRow = document.getElementById('paymentDiscountRow');
    const discountEl = document.getElementById('paymentDiscount');
    const continueBtn = document.getElementById('paymentContinue');
    const cancelBtn = document.getElementById('paymentCancel');

    let quotePollTimer = null;

    function renderItem(item) {
        if (PromoPricing && typeof PromoPricing.renderLineItemHtml === 'function') {
            return PromoPricing.renderLineItemHtml(item, Cart.formatBRL.bind(Cart), 'payment-item');
        }
        const subtotal = Cart.formatBRL(item.subtotal != null ? item.subtotal : item.preco * item.quantidade);
        const unit = Cart.formatBRL(item.preco);
        const backorderIcon = PromoPricing && typeof PromoPricing.backorderIndicatorHtml === 'function'
            ? PromoPricing.backorderIndicatorHtml(item, 'payment-item')
            : '';
        const backorderClass = backorderIcon ? ' payment-item--backorder' : '';
        return `
            <article class="payment-item${backorderClass}" data-id="${item.id}">
                <div class="payment-item__image">
                    <img src="${item.imagem}" alt="${item.nome}" loading="lazy">
                </div>
                <div class="payment-item__info">
                    <span class="payment-item__category">${item.categoria || ''}</span>
                    <div class="payment-item__name-row">
                        <h3 class="payment-item__name">${item.nome}</h3>
                        ${backorderIcon}
                    </div>
                    ${item.sku ? `<p class="payment-item__sku">SKU ${item.sku}</p>` : ''}
                    <p class="payment-item__meta">${item.quantidade} × ${unit}</p>
                </div>
                <div class="payment-item__total">${subtotal}</div>
            </article>
        `;
    }

    function updateSummaryTotals(totals) {
        countEl.textContent = totals.count;
        const hasDiscount = totals.economiaTotal > 0.009;
        if (discountRow) discountRow.hidden = !hasDiscount;
        if (discountEl && hasDiscount) {
            discountEl.textContent = `-${Cart.formatBRL(totals.economiaTotal)}`;
        }
        if (hasDiscount) {
            subtotalEl.textContent = Cart.formatBRL(totals.subtotalLista);
        } else {
            subtotalEl.textContent = Cart.formatBRL(totals.total);
        }
        totalEl.textContent = Cart.formatBRL(totals.total);
    }

    function backorderNoticeHtml(items) {
        if (!window.__SELLER_BACKORDER__) return '';
        const hasBackorder = items.some(item => {
            const stock = Number(item.estoque);
            return Number.isFinite(stock) && item.quantidade > Math.max(0, stock);
        });
        if (!hasBackorder) return '';
        return `
            <div class="payment-backorder-note" role="note">
                <i class="fa-solid fa-box-open" aria-hidden="true"></i>
                Este pedido tem itens sem estoque suficiente. O pagamento é integral;
                os itens faltantes ficarão pendentes de retirada posterior pelo cliente.
            </div>
        `;
    }

    function renderSummary() {
        const items = Cart.getItems();
        if (items.length === 0) {
            clearResumePendingTxId();
            window.location.replace(CATALOG_URL);
            return;
        }
        itemsEl.innerHTML = backorderNoticeHtml(items) + items.map(renderItem).join('');
        updateSummaryTotals(Cart.getTotals());
    }

    async function syncServerQuote() {
        if (!window.TotemApiErrors) return;
        try {
            const data = await window.TotemApiErrors.fetchJson(QUOTE_API, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ items: Cart.getItems() }),
            });
            if (data && Array.isArray(data.items)) {
                Cart.applyServerQuote(data);
            }
        } catch (_) {
            /* cotação opcional — mantém cálculo local */
        }
        renderSummary();
    }

    function startQuotePolling() {
        stopQuotePolling();
        void syncServerQuote();
        quotePollTimer = setInterval(syncServerQuote, QUOTE_POLL_MS);
    }

    function stopQuotePolling() {
        if (quotePollTimer) {
            clearInterval(quotePollTimer);
            quotePollTimer = null;
        }
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
    startQuotePolling();
    window.addEventListener('pagehide', stopQuotePolling);
})();
