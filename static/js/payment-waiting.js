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
    const pinModal = document.getElementById('sellerPinModal');
    const pinForm = document.getElementById('sellerPinForm');
    const pinInput = document.getElementById('sellerPinInput');
    const pinError = document.getElementById('sellerPinError');

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

    function openPinModal() {
        if (!pinModal || !pinInput) return;
        pinModal.hidden = false;
        pinModal.setAttribute('aria-hidden', 'false');
        pinInput.value = '';
        if (pinError) {
            pinError.hidden = true;
            pinError.textContent = '';
        }
        setTimeout(() => pinInput.focus(), 0);
    }

    function closePinModal() {
        if (!pinModal) return;
        pinModal.hidden = true;
        pinModal.setAttribute('aria-hidden', 'true');
    }

    function getPinValue() {
        const value = (pinInput ? pinInput.value : '').trim();
        if (!/^\d{4}$/.test(value)) {
            throw new Error('Digite um PIN de 4 dígitos.');
        }
        return value;
    }

    async function registerTransaction(sellerPin) {
        const clientData = window.PaymentForm ? window.PaymentForm.load() : null;
        const payload = {
            items: Cart.getItems(),
            client: clientData || {},
            seller_pin: sellerPin,
        };
        try {
            const response = await fetch('/api/transacoes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Não foi possível registrar a transação.');
            }
            return data.order_number;
        } catch (err) {
            console.warn('Falha ao registrar transação no servidor:', err);
            throw err;
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
        openPinModal();
    });

    if (pinInput) {
        pinInput.addEventListener('input', () => {
            pinInput.value = pinInput.value.replace(/\D/g, '').slice(0, 4);
            if (pinError) {
                pinError.hidden = true;
                pinError.textContent = '';
            }
        });
    }

    if (pinModal) {
        pinModal.querySelectorAll('[data-pin-close]').forEach(btn => {
            btn.addEventListener('click', closePinModal);
        });
    }

    if (pinForm) {
        pinForm.addEventListener('submit', async event => {
            event.preventDefault();
            if (Cart.isEmpty()) return;
            let sellerPin;
            try {
                sellerPin = getPinValue();
            } catch (err) {
                if (pinError) {
                    pinError.textContent = err.message;
                    pinError.hidden = false;
                }
                return;
            }

        confirmBtn.disabled = true;
        const originalLabel = confirmBtn.innerHTML;
        confirmBtn.innerHTML =
            '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Registrando...';
        try {
            const submit = pinForm.querySelector('button[type="submit"]');
            if (submit) submit.disabled = true;
            const orderNumber = await registerTransaction(sellerPin);
            confirmBtn.innerHTML = originalLabel;
            closePinModal();
            showSuccess(orderNumber);
        } catch (err) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = originalLabel;
            const message = err.message || 'Não foi possível registrar a venda. Tente novamente.';
            if (pinError) {
                pinError.textContent = message;
                pinError.hidden = false;
            } else {
                window.alert(message);
            }
        } finally {
            const submit = pinForm.querySelector('button[type="submit"]');
            if (submit) submit.disabled = false;
        }
        });
    }

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && pinModal && !pinModal.hidden) {
            closePinModal();
        }
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
