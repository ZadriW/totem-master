/**
 * Página /pagamento/aguardando — aguardo da maquininha + confirmação "Pagamento realizado".
 * Registra a transação na API e exibe a tela de sucesso.
 */
(() => {
    'use strict';

    const Cart = window.Cart;
    if (!Cart) return;

    const SUMMARY_URL = '/pagamento';
    const CATALOG_URL = '/catalogo';
    const HOME_URL = '/';
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
    const successFinish = document.getElementById('successFinish');

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
    }

    function localOrderNumber() {
        const now = new Date();
        const y = now.getFullYear().toString().slice(-2);
        const m = String(now.getMonth() + 1).padStart(2, '0');
        const d = String(now.getDate()).padStart(2, '0');
        const rnd = Math.floor(Math.random() * 9000) + 1000;
        return `OM${y}${m}${d}-${rnd}`;
    }

    async function registerTransaction() {
        const clientData = window.PaymentForm ? window.PaymentForm.load() : null;
        const payload = {
            items: Cart.getItems(),
            client: clientData || {},
        };
        try {
            const response = await fetch('/api/transacoes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            return data.order_number || localOrderNumber();
        } catch (err) {
            console.warn('Falha ao registrar transação no servidor:', err);
            return localOrderNumber();
        }
    }

    function showSuccess(orderNumber) {
        successOrder.textContent = `Pedido #${orderNumber}`;
        content.hidden = true;
        success.hidden = false;
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

    confirmBtn.addEventListener('click', async () => {
        if (Cart.isEmpty()) return;
        confirmBtn.disabled = true;
        const originalLabel = confirmBtn.innerHTML;
        confirmBtn.innerHTML =
            '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Registrando...';
        const orderNumber = await registerTransaction();
        confirmBtn.innerHTML = originalLabel;
        showSuccess(orderNumber);
    });

    backBtn.addEventListener('click', () => {
        window.location.assign(SUMMARY_URL);
    });

    successFinish.addEventListener('click', () => {
        window.location.assign(HOME_URL);
    });

    Cart.subscribe(() => {
        if (!success.hidden) return;
        renderWaiting();
    });

    renderWaiting();
})();
