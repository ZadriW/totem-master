/**
 * Aguardo da maquininha + confirmação da venda (vendedor autenticado).
 */
(() => {
    'use strict';

    const Cart = window.Cart;
    if (!Cart) return;

    const FLOW = window.__TOTEM_FLOW__ || {};
    const SUMMARY_URL = FLOW.payment || '/vendedor/pagamento';
    const CATALOG_URL = FLOW.catalog || '/vendedor/venda';
    const HOME_URL = FLOW.home || '/';
    const SUCCESS_REDIRECT_MS = 30000;

    const content = document.getElementById('waitingContent');
    const success = document.getElementById('paymentSuccess');
    const itemsEl = document.getElementById('waitingItems');
    const countEl = document.getElementById('waitingCount');
    const totalEl = document.getElementById('waitingTotal');
    const confirmBtn = document.getElementById('waitingConfirm');
    const backBtn = document.getElementById('waitingBack');
    const successOrder = document.getElementById('successOrder');
    const successCountdown = document.getElementById('successCountdown');
    const successPrint = document.getElementById('successPrint');
    const successFinish = document.getElementById('successFinish');

    /** Número do pedido retornado pela API (ex.: OMyymmdd-1234), definido após sucesso. */
    let confirmedOrderNumber = null;

    function paymentMethodLabel(raw, installments) {
        const v = String(raw || '').toLowerCase();
        if (v === 'pix') return 'PIX';
        const n = parseInt(String(installments ?? ''), 10);
        if (Number.isFinite(n) && n > 1) return `Cartão em ${n}x`;
        return 'Cartão';
    }

    function syncWaitingPaymentMethodUi() {
        const clientData = window.PaymentForm ? window.PaymentForm.load() : null;
        const pm = clientData && clientData.payment_method;
        const inst = clientData && clientData.installments;
        const methodEl = document.getElementById('waitingPaymentMethod');
        if (methodEl) methodEl.textContent = paymentMethodLabel(pm, inst);
        const heroIcon = document.querySelector('.payment-wait__pulse i');
        if (heroIcon) {
            if (String(pm || '').toLowerCase() === 'pix') {
                heroIcon.className = 'fa-brands fa-pix';
            } else {
                heroIcon.className = 'fa-solid fa-credit-card';
            }
        }
    }

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
                    <p class="payment-item__meta">${item.quantidade} × ${unit}</p>
                </div>
                <div class="payment-item__total">${subtotal}</div>
            </article>
        `;
    }

    function renderWaiting() {
        const items = Cart.getItems();
        if (items.length === 0) {
            window.location.replace(CATALOG_URL);
            return;
        }
        itemsEl.innerHTML = items.map(renderItem).join('');
        countEl.textContent = Cart.count();
        totalEl.textContent = Cart.formatBRL(Cart.total());
        syncWaitingPaymentMethodUi();
    }

    async function registerTransaction() {
        const clientData = window.PaymentForm ? window.PaymentForm.load() : null;
        const payload = {
            items: Cart.getItems(),
            client: clientData || {},
            payment_method: (clientData && clientData.payment_method)
                ? clientData.payment_method
                : 'cartao',
        };
        const response = await fetch('/api/transacoes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(payload),
        });
        let data;
        try {
            data = await response.json();
        } catch (_) {
            data = {};
        }
        if (!response.ok) {
            throw new Error(data.error || 'Não foi possível registrar a transação.');
        }
        return data.order_number;
    }

    function showSuccess(orderNumber) {
        confirmedOrderNumber = orderNumber != null ? String(orderNumber) : null;
        successOrder.textContent = `Pedido #${orderNumber}`;
        document.querySelector('.payment')?.classList.add('payment--success-only');
        if (content) {
            content.hidden = true;
            content.setAttribute('aria-hidden', 'true');
        }
        success.hidden = false;
        success.removeAttribute('aria-hidden');
        Cart.clear();
        if (window.PaymentForm) window.PaymentForm.clear();

        let remaining = Math.floor(SUCCESS_REDIRECT_MS / 1000);
        successCountdown.textContent = String(remaining);
        const tick = setInterval(() => {
            remaining -= 1;
            if (remaining <= 0) {
                clearInterval(tick);
                successCountdown.textContent = '0';
            } else {
                successCountdown.textContent = String(remaining);
            }
        }, 1000);

        setTimeout(() => {
            window.location.assign(HOME_URL);
        }, SUCCESS_REDIRECT_MS);
    }

    async function runConfirmSale() {
        const originalLabel = confirmBtn.innerHTML;
        confirmBtn.disabled = true;
        confirmBtn.innerHTML =
            '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Registrando...';
        try {
            const orderNumber = await registerTransaction();
            confirmBtn.innerHTML = originalLabel;
            showSuccess(orderNumber);
        } catch (err) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = originalLabel;
            window.alert(err.message || 'Não foi possível registrar a venda. Tente novamente.');
        }
    }

    confirmBtn.addEventListener('click', async () => {
        if (Cart.isEmpty()) return;
        await runConfirmSale();
    });

    backBtn.addEventListener('click', () => {
        window.location.assign(SUMMARY_URL);
    });

    function openReceiptPrint() {
        if (!confirmedOrderNumber) return;
        const path = `/nota/${encodeURIComponent(confirmedOrderNumber)}?print=1`;
        const w = window.open(path, '_blank', 'noopener');
        if (!w) {
            window.location.assign(path);
        }
    }

    successPrint?.addEventListener('click', () => {
        openReceiptPrint();
    });

    successFinish.addEventListener('click', () => {
        window.location.assign(HOME_URL);
    });

    Cart.subscribe(() => {
        if (!success.hidden) return;
        renderWaiting();
    });

    renderWaiting();
    syncWaitingPaymentMethodUi();
})();
