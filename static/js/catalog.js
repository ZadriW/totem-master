(() => {
    'use strict';

    const grid = document.getElementById('productGrid');
    if (!grid) return;
    const cards = Array.from(grid.querySelectorAll('.product-card'));
    const searchInput = document.getElementById('searchInput');
    const categoryChips = document.querySelectorAll('.category-chip[data-category]');
    const categoryDropdownRoots = document.querySelectorAll('.category-dropdown');
    const categoryLetterTriggers = document.querySelectorAll('.category-letter-trigger');
    const clearFiltersBtn = document.getElementById('clearCatalogFilters');
    const categoriesScroll = document.querySelector('.categories__scroll');
    const emptyState = document.getElementById('emptyState');
    const resultsInfo = document.getElementById('resultsInfo');
    const cartCountEl = document.getElementById('cartCount');
    const openCartBtn = document.getElementById('openCartBtn');

    // Dicionário id -> produto (injetado via Jinja no painel do vendedor).
    const FLOW = window.__TOTEM_FLOW__ || {};
    const STOCK_API = typeof window.__CATALOG_STOCK_API__ === 'string'
        ? window.__CATALOG_STOCK_API__
        : '';
    const PROMO_REFRESH_API = typeof window.__CATALOG_PROMO_REFRESH_API__ === 'string'
        ? window.__CATALOG_PROMO_REFRESH_API__
        : '';
    const productsById = new Map();
    (window.__PRODUCTS__ || []).forEach(p => {
        productsById.set(String(p.id), p);
    });

    const Cart = window.Cart;

    const state = {
        category: 'todos',
        query: '',
    };

    let searchTimer;

    let stockStaleNotice = false;
    let promoStaleNotice = false;

    function closeAllCategoryDropdowns() {
        categoryDropdownRoots.forEach(drop => {
            drop.classList.remove('is-open');
            const panel = drop.querySelector('.category-dropdown__panel');
            const trig = drop.querySelector('.category-letter-trigger');
            if (panel) {
                panel.hidden = true;
                panel.style.top = '';
                panel.style.left = '';
                panel.style.width = '';
                panel.style.maxWidth = '';
                panel.style.maxHeight = '';
            }
            if (trig) trig.setAttribute('aria-expanded', 'false');
        });
    }

    function positionCategoryPanel(drop) {
        const trigger = drop.querySelector('.category-letter-trigger');
        const panel = drop.querySelector('.category-dropdown__panel');
        if (!trigger || !panel || panel.hidden) return;

        const rect = trigger.getBoundingClientRect();
        const gap = 6;
        const margin = 8;
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const panelWidth = Math.min(360, Math.max(220, vw - margin * 2));

        panel.style.width = `${panelWidth}px`;
        panel.style.maxWidth = `${panelWidth}px`;

        let left = rect.left;
        if (left + panelWidth > vw - margin) {
            left = vw - margin - panelWidth;
        }
        if (left < margin) {
            left = margin;
        }
        panel.style.left = `${left}px`;

        const belowTop = rect.bottom + gap;
        const maxHBelow = Math.max(160, vh - belowTop - margin);
        const maxHAbove = Math.max(160, rect.top - margin - gap);
        panel.style.top = `${belowTop}px`;
        panel.style.maxHeight = `${Math.min(320, maxHBelow)}px`;

        requestAnimationFrame(() => {
            const ph = panel.getBoundingClientRect().height;
            const spaceBelow = vh - belowTop - margin;
            const spaceAbove = rect.top - margin - gap;

            if (ph > spaceBelow && spaceAbove > spaceBelow) {
                const topAbove = rect.top - gap - ph;
                panel.style.top = `${Math.max(margin, topAbove)}px`;
                panel.style.maxHeight = `${Math.min(320, maxHAbove)}px`;
            } else {
                panel.style.top = `${belowTop}px`;
                panel.style.maxHeight = `${Math.min(320, maxHBelow)}px`;
            }
        });
    }

    function closeDropdownsOnScrollOrResize(ev) {
        if (ev && ev.type === 'scroll' && ev.target && typeof ev.target.closest === 'function') {
            if (ev.target.closest('.category-dropdown__panel')) {
                return;
            }
        }
        if (document.querySelector('.category-dropdown.is-open')) {
            closeAllCategoryDropdowns();
        }
    }

    categoryLetterTriggers.forEach(trigger => {
        trigger.addEventListener('click', e => {
            e.preventDefault();
            e.stopPropagation();
            const drop = trigger.closest('.category-dropdown');
            if (!drop) return;
            const panel = drop.querySelector('.category-dropdown__panel');
            const wasOpen = drop.classList.contains('is-open');
            closeAllCategoryDropdowns();
            if (!wasOpen) {
                drop.classList.add('is-open');
                if (panel) {
                    panel.hidden = false;
                    trigger.setAttribute('aria-expanded', 'true');
                    requestAnimationFrame(() => {
                        requestAnimationFrame(() => positionCategoryPanel(drop));
                    });
                }
            }
        });
    });

    document.addEventListener('click', () => {
        closeAllCategoryDropdowns();
    });

    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') closeAllCategoryDropdowns();
    });

    window.addEventListener('scroll', closeDropdownsOnScrollOrResize, { passive: true });
    window.addEventListener('resize', closeDropdownsOnScrollOrResize);
    if (categoriesScroll) {
        categoriesScroll.addEventListener('scroll', closeDropdownsOnScrollOrResize, { passive: true });
    }

    /* -------------------------------------------------------------------- */
    /* Utilidades do catálogo                                               */
    /* -------------------------------------------------------------------- */

    function normalize(text) {
        return text
            .toString()
            .toLowerCase()
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '');
    }

    function getStock(card) {
        const n = parseInt(card.dataset.estoque, 10);
        return Number.isFinite(n) && n >= 0 ? n : 999;
    }

    function clampQty(card, raw) {
        const stock = getStock(card);
        let n = parseInt(String(raw), 10);
        if (!Number.isFinite(n)) n = 1;
        n = Math.max(1, n);
        // Painel do vendedor: sem teto de estoque (retirada posterior).
        if (window.__SELLER_BACKORDER__) return n;
        if (stock > 0) n = Math.min(n, stock);
        return n;
    }

    function formatStockLabel(n) {
        return `${n} no estoque.`;
    }

    const STOCK_TONES = ['product-card__stock--ok', 'product-card__stock--low', 'product-card__stock--empty'];

    function stockToneFromLevels(estoque, minStock) {
        const n = Math.max(0, Math.floor(Number(estoque)) || 0);
        const min = Math.max(0, Math.floor(Number(minStock)) || 0);
        if (n <= 0) return 'product-card__stock--empty';
        if (min > 0 && n < min) return 'product-card__stock--low';
        return 'product-card__stock--ok';
    }

    function syncStockDisplayTone(card) {
        const label = card.querySelector('[data-stock-display]');
        if (!label) return;
        const minRaw = parseInt(card.dataset.estoqueMin, 10);
        const minSafe = Number.isFinite(minRaw) && minRaw >= 0 ? minRaw : 0;
        const tone = stockToneFromLevels(getStock(card), minSafe);
        STOCK_TONES.forEach(c => label.classList.remove(c));
        label.classList.add(tone);
    }

    function applyStockToCard(card, estoqueRaw) {
        const n = Math.max(0, Math.floor(Number(estoqueRaw)) || 0);
        card.dataset.estoque = String(n);
        const label = card.querySelector('[data-stock-display]');
        if (label) label.textContent = formatStockLabel(n);
        syncStockDisplayTone(card);
        const input = card.querySelector('.product-card__counter-input');
        if (input) {
            input.setAttribute('max', String(n));
            input.value = String(clampQty(card, input.value));
        }
        const p = productsById.get(String(card.dataset.id));
        if (p) p.estoque = n;
    }

    function escapeCatalogHtml(value) {
        const d = document.createElement('div');
        d.textContent = value == null ? '' : String(value);
        return d.innerHTML;
    }

    function formatCatalogPriceBRL(n) {
        const x = Number(n);
        if (!Number.isFinite(x)) return 'R$ 0,00';
        return `R$ ${x.toFixed(2).replace('.', ',')}`;

    }

    function renderPromoBadgeMarkup(product) {
        if (!product.em_promocao || !product.promo_badge) return '';
        return `<div class="product-card__promo-badge"><i class="fa-solid fa-tag" aria-hidden="true"></i> ${escapeCatalogHtml(product.promo_badge)}</div>`;
    }

    function renderPricingBlockMarkup(product) {
        const em = !!product.em_promocao;
        const po = Number(product.preco_original);
        const pp = Number(product.preco);
        const tipo = product.promo_tipo || '';

        // percent / fixed: preço unitário já reduzido — exibe riscado + novo preço.
        if (em && (tipo === 'percent' || tipo === 'fixed') && Number.isFinite(po) && po > pp + 0.001) {
            return `<div class="product-card__price-wrap"><span class="product-card__price-original">${formatCatalogPriceBRL(po)}</span><p class="product-card__price product-card__price--promo">${formatCatalogPriceBRL(pp)}</p></div>`;
        }

        // bogo / min_bundle / exact_bundle: desconto depende da quantidade — exibe preço
        // de lista + nome da promoção. O desconto só aparece quando a regra é atingida no carrinho.
        if (em && (tipo === 'bogo' || tipo === 'min_bundle' || tipo === 'exact_bundle')) {
            const nome = escapeCatalogHtml(product.promo_nome || '');
            return `<div class="product-card__price-wrap"><p class="product-card__price">${formatCatalogPriceBRL(pp)}</p>${nome ? `<span class="product-card__promo-name">${nome}</span>` : ''}</div>`;
        }

        return `<p class="product-card__price">${formatCatalogPriceBRL(pp)}</p>`;
    }

    function applyCatalogSnapshotToCard(card, p) {
        productsById.set(String(p.id), { ...p });

        const minRaw = Math.max(0, Math.floor(Number(p.estoque_minimo)) || 0);
        card.dataset.estoqueMin = String(minRaw);

        applyStockToCard(card, p.estoque);

        card.dataset.preco = String(Number(p.preco) || 0);

        card.classList.toggle('product-card--promo', !!p.em_promocao);

        const badgeRoot = card.querySelector('[data-promo-badge-root]');
        if (badgeRoot) badgeRoot.innerHTML = renderPromoBadgeMarkup(p);

        const priceRoot = card.querySelector('[data-catalog-pricing]');
        if (priceRoot) priceRoot.innerHTML = renderPricingBlockMarkup(p);
    }

    async function fetchCatalogPromoRefresh() {
        if (!PROMO_REFRESH_API) return;
        try {
            const res = await fetch(PROMO_REFRESH_API, { credentials: 'same-origin' });
            const T = window.TotemApiErrors;
            const data = T ? await T.parseJsonSafe(res) : await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(
                    T ? T.messageFromBadResponse(res, data) : 'Não foi possível atualizar preços e promoções.',
                );
            }
            const list = data.products;
            if (!Array.isArray(list)) return;
            const byId = new Map(list.map(row => [String(row.id), row]));
            cards.forEach(card => {
                const row = byId.get(String(card.dataset.id));
                if (!row) return;
                applyCatalogSnapshotToCard(card, row);
            });
            if (Cart && typeof Cart.syncPricesFromProductMap === 'function') {
                Cart.syncPricesFromProductMap(productsById);
            }
        } catch (err) {
            const T = window.TotemApiErrors;
            const msg = T ? T.formatCatchMessage(err) : String(err.message || err);
            if (!promoStaleNotice && resultsInfo && T) {
                promoStaleNotice = true;
                const hint = document.createElement('span');
                hint.className = 'catalog-stock-sync-warning';
                hint.textContent = ' • Preços/promoções ao vivo indisponíveis';
                hint.title = msg.replace(/\n\n/g, ' ');
                resultsInfo.appendChild(hint);
            }
        }
    }

    async function fetchCatalogStock() {
        if (!STOCK_API) return;
        try {
            const res = await fetch(STOCK_API, { credentials: 'same-origin' });
            const T = window.TotemApiErrors;
            const data = T ? await T.parseJsonSafe(res) : await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(
                    T ? T.messageFromBadResponse(res, data) : 'Não foi possível atualizar o estoque ao vivo.',
                );
            }
            const list = data.products;
            if (!Array.isArray(list)) return;
            const byId = new Map(
                list.map(row => [String(row.id), row.estoque]),
            );
            cards.forEach(card => {
                const id = card.dataset.id;
                if (!byId.has(id)) return;
                applyStockToCard(card, byId.get(id));
            });
        } catch (err) {
            const T = window.TotemApiErrors;
            const msg = T ? T.formatCatchMessage(err) : String(err.message || err);
            if (!stockStaleNotice && resultsInfo && T) {
                stockStaleNotice = true;
                const hint = document.createElement('span');
                hint.className = 'catalog-stock-sync-warning';
                hint.textContent = ' • Estoque ao vivo indisponível';
                hint.title = msg.replace(/\n\n/g, ' ');
                resultsInfo.appendChild(hint);
            }
        }
    }

    function resetFiltersToDefault() {
        clearTimeout(searchTimer);
        state.category = 'todos';
        state.query = '';
        if (searchInput) searchInput.value = '';
        closeAllCategoryDropdowns();
        if (categoriesScroll) {
            categoriesScroll.scrollTo({ left: 0, behavior: 'smooth' });
        }
        applyFilters();
    }

    function applyFilters() {
        const query = normalize(state.query.trim());
        let visible = 0;

        cards.forEach(card => {
            const matchesCategory =
                state.category === 'todos'
                || card.dataset.category === state.category;
            const sku = card.dataset.sku || '';
            const matchesQuery =
                !query
                || normalize(card.dataset.name).includes(query)
                || (sku && normalize(sku).includes(query));
            const show = matchesCategory && matchesQuery;
            card.style.display = show ? '' : 'none';
            if (show) visible += 1;
        });

        emptyState.hidden = visible !== 0;
        resultsInfo.textContent =
            visible === 1
                ? '1 produto disponível'
                : `${visible} produtos disponíveis`;

        categoryChips.forEach(c => {
            c.classList.toggle('is-active', c.dataset.category === state.category);
        });
        categoryLetterTriggers.forEach(tr => {
            const drop = tr.closest('.category-dropdown');
            const match = state.category !== 'todos' && drop && Array.from(
                drop.querySelectorAll('.category-chip[data-category]'),
            ).some(el => el.dataset.category === state.category);
            tr.classList.toggle('is-active', Boolean(match));
        });
    }

    function updateCartBadge() {
        const total = Cart ? Cart.count() : 0;
        cartCountEl.textContent = total;
        cartCountEl.classList.toggle('is-visible', total > 0);
    }

    /* -------------------------------------------------------------------- */
    /* Filtros e busca                                                      */
    /* -------------------------------------------------------------------- */

    categoryChips.forEach(chip => {
        chip.addEventListener('click', e => {
            e.stopPropagation();
            state.category = chip.dataset.category;
            closeAllCategoryDropdowns();
            applyFilters();
        });
    });

    if (clearFiltersBtn) {
        clearFiltersBtn.addEventListener('click', () => {
            resetFiltersToDefault();
        });
    }

    searchInput.addEventListener('input', event => {
        clearTimeout(searchTimer);
        const value = event.target.value;
        searchTimer = setTimeout(() => {
            state.query = value;
            applyFilters();
        }, 120);
    });

    /* -------------------------------------------------------------------- */
    /* Toast — produto adicionado (canto superior direito)                   */
    /* -------------------------------------------------------------------- */

    const addToast = document.getElementById('catalogAddToast');
    let addToastTimer = null;

    function showAddToCartToast() {
        if (!addToast) return;
        addToast.classList.add('is-visible');
        addToast.setAttribute('aria-hidden', 'false');
        clearTimeout(addToastTimer);
        addToastTimer = setTimeout(() => {
            addToast.classList.remove('is-visible');
            addToast.setAttribute('aria-hidden', 'true');
        }, 3200);
    }

    /* -------------------------------------------------------------------- */
    /* Contador no card e adição ao carrinho                                */
    /* -------------------------------------------------------------------- */

    grid.addEventListener('click', event => {
        const counterBtn = event.target.closest('.product-card__counter-btn');
        if (counterBtn) {
            const card = counterBtn.closest('.product-card');
            const input = card.querySelector('.product-card__counter-input');
            const action = counterBtn.dataset.action;
            const stock = getStock(card);
            let val = clampQty(card, input.value);

            if (action === 'inc') {
                val = (stock > 0 && !window.__SELLER_BACKORDER__)
                    ? Math.min(val + 1, stock)
                    : val + 1;
            } else if (action === 'dec') {
                val = Math.max(1, val - 1);
            }
            input.value = String(val);
            return;
        }

        const button = event.target.closest('.product-card__button');
        if (!button || button.disabled) return;

        const card = button.closest('.product-card');
        const input = card.querySelector('.product-card__counter-input');
        const qty = clampQty(card, input.value);

        const id = button.dataset.id;
        const product = productsById.get(String(id));
        if (Cart && product) {
            Cart.add(product, qty);
            showAddToCartToast();
        }

        input.value = '1';

        button.classList.add('is-added');
        clearTimeout(button._addedTimer);
        button._addedTimer = setTimeout(() => {
            button.classList.remove('is-added');
        }, 900);
    });

    grid.addEventListener('change', event => {
        const input = event.target;
        if (!input.classList.contains('product-card__counter-input')) return;
        const card = input.closest('.product-card');
        input.value = String(clampQty(card, input.value));
    });

    grid.addEventListener('blur', event => {
        const input = event.target;
        if (!input.classList.contains('product-card__counter-input')) return;
        const card = input.closest('.product-card');
        input.value = String(clampQty(card, input.value));
    }, true);

    /* -------------------------------------------------------------------- */
    /* Drawer do carrinho                                                   */
    /* -------------------------------------------------------------------- */

    const drawer = document.getElementById('cartDrawer');
    const drawerBackdrop = document.getElementById('cartBackdrop');
    const drawerClose = document.getElementById('cartDrawerClose');
    const drawerItems = document.getElementById('cartDrawerItems');
    const drawerEmpty = document.getElementById('cartDrawerEmpty');
    const drawerCount = document.getElementById('cartDrawerCount');
    const drawerTotal = document.getElementById('cartDrawerTotal');
    const drawerCheckout = document.getElementById('cartDrawerCheckout');
    const drawerClear = document.getElementById('cartDrawerClear');

    function openDrawer() {
        if (!drawer) return;
        drawer.classList.add('is-open');
        drawerBackdrop.classList.add('is-open');
        document.body.classList.add('cart-open');
        drawer.setAttribute('aria-hidden', 'false');
    }

    function closeDrawer() {
        if (!drawer) return;
        drawer.classList.remove('is-open');
        drawerBackdrop.classList.remove('is-open');
        document.body.classList.remove('cart-open');
        drawer.setAttribute('aria-hidden', 'true');
    }

    function renderCartItem(item) {
        const subtotal = Cart.formatBRL(item.subtotal != null ? item.subtotal : item.preco * item.quantidade);
        const unit = Cart.formatBRL(item.preco);
        const listUnit = Number(item.preco_lista) || Number(item.preco) || 0;
        const showOriginal = item.promo_aplicada && listUnit > Number(item.preco) + 0.001;
        const unitLine = showOriginal
            ? `<span class="line-item__price-original">${Cart.formatBRL(listUnit)}</span> ${unit}`
            : unit;
        const promoHint = item.promo_aplicada && item.promo_nome
            ? `<p class="line-item__promo"><i class="fa-solid fa-tag" aria-hidden="true"></i> ${item.promo_nome}</p>`
            : '';
        const stock = Number(item.estoque);
        const missing = Number.isFinite(stock) ? item.quantidade - Math.max(0, stock) : 0;
        const backorderHint = (window.__SELLER_BACKORDER__ && missing > 0)
            ? `<p class="cart-item__backorder"><i class="fa-solid fa-box-open" aria-hidden="true"></i> ${missing} un. sem estoque — retirada posterior pelo cliente</p>`
            : '';
        return `
            <article class="cart-item" data-id="${item.id}">
                <div class="cart-item__image">
                    <img src="${item.imagem}" alt="${item.nome}" loading="lazy">
                </div>
                <div class="cart-item__info">
                    <span class="cart-item__category">${item.categoria || ''}</span>
                    <h3 class="cart-item__name">${item.nome}</h3>
                    ${item.sku ? `<p class="cart-item__sku">SKU ${item.sku}</p>` : ''}
                    <p class="cart-item__price">
                        ${unitLine} un. &middot; <strong>${subtotal}</strong>
                    </p>
                    ${promoHint}
                    ${backorderHint}
                    <div class="cart-item__counter" role="group" aria-label="Quantidade">
                        <button type="button" class="cart-item__counter-btn" data-cart-action="dec" aria-label="Diminuir">
                            <i class="fa-solid fa-minus" aria-hidden="true"></i>
                        </button>
                        <span class="cart-item__counter-qty">${item.quantidade}</span>
                        <button type="button" class="cart-item__counter-btn" data-cart-action="inc" aria-label="Aumentar">
                            <i class="fa-solid fa-plus" aria-hidden="true"></i>
                        </button>
                    </div>
                </div>
                <div class="cart-item__side">
                    <button type="button" class="cart-item__remove" data-cart-action="remove" aria-label="Remover">
                        <i class="fa-solid fa-trash" aria-hidden="true"></i>
                    </button>
                </div>
            </article>
        `;
    }

    function renderDrawer() {
        if (!drawer) return;
        const totals = Cart.getTotals();
        const items = totals.items;

        drawerCount.textContent = totals.count;
        drawerTotal.textContent = Cart.formatBRL(totals.total);
        drawerCheckout.disabled = items.length === 0;
        if (drawerClear) drawerClear.disabled = items.length === 0;

        if (items.length === 0) {
            drawerItems.innerHTML = '';
            drawerItems.hidden = true;
            drawerEmpty.hidden = false;
        } else {
            drawerEmpty.hidden = true;
            drawerItems.hidden = false;
            drawerItems.innerHTML = items.map(renderCartItem).join('');
        }
    }

    if (openCartBtn) {
        openCartBtn.addEventListener('click', () => {
            renderDrawer();
            openDrawer();
        });
    }
    if (drawerClose) drawerClose.addEventListener('click', closeDrawer);
    if (drawerBackdrop) drawerBackdrop.addEventListener('click', closeDrawer);

    if (drawerItems) {
        drawerItems.addEventListener('click', event => {
            const btn = event.target.closest('[data-cart-action]');
            if (!btn) return;
            const itemEl = btn.closest('.cart-item');
            const id = itemEl.dataset.id;
            const action = btn.dataset.cartAction;
            if (action === 'inc') Cart.increment(id);
            else if (action === 'dec') Cart.decrement(id);
            else if (action === 'remove') Cart.remove(id);
        });
    }

    if (drawerCheckout) {
        drawerCheckout.addEventListener('click', () => {
            if (Cart.isEmpty()) return;
            const payUrl = FLOW.payment || '/vendedor/pagamento';
            window.location.assign(payUrl);
        });
    }

    if (drawerClear) {
        drawerClear.addEventListener('click', () => {
            if (Cart.isEmpty()) return;
            if (!window.confirm('Remover todos os itens do carrinho?')) return;
            Cart.clear();
        });
    }

    /* -------------------------------------------------------------------- */
    /* Inicialização e listener global                                       */
    /* -------------------------------------------------------------------- */

    if (Cart) {
        Cart.subscribe(() => {
            updateCartBadge();
            renderDrawer();
        });
    }

    applyFilters();
    updateCartBadge();

    if (PROMO_REFRESH_API) {
        fetchCatalogPromoRefresh();
        setInterval(fetchCatalogPromoRefresh, 30000);
    } else if (STOCK_API) {
        fetchCatalogStock();
        setInterval(fetchCatalogStock, 30000);
    }
})();
