/**
 * Módulo de carrinho compartilhado entre catálogo e pagamento.
 *
 * Armazena o estado em sessionStorage (dura enquanto a aba do totem estiver
 * aberta; é limpo na tela de welcome). Emite o evento "cart:changed" em
 * window sempre que o carrinho muda, permitindo que badge, drawer e demais
 * telas se mantenham em sincronia sem acoplamento.
 *
 * Estrutura de cada item:
 * { id, sku, nome, categoria, preco, preco_lista, imagem, estoque?, quantidade,
 *   subtotal, em_promocao, promo_tipo, promo_rule_value, promo_min_qty,
 *   promo_free_qty, promo_nome, promo_badge, promo_aplicada, economia }
 */
(() => {
    'use strict';

    const STORAGE_KEY = 'totem_cart_v1';
    const EVENT_NAME = 'cart:changed';
    const PromoPricing = () => window.PromoPricing;

    function readRaw() {
        try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return [];
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : [];
        } catch (_) {
            return [];
        }
    }

    function writeRaw(items) {
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(items));
        } catch (_) {
            /* storage cheio/desabilitado — ignora silenciosamente */
        }
        window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: { items } }));
    }

    function clampQty(qty, stock) {
        let n = parseInt(String(qty), 10);
        if (!Number.isFinite(n)) n = 1;
        n = Math.max(1, n);
        if (Number.isFinite(stock) && stock > 0) n = Math.min(n, stock);
        return n;
    }

    function itemFromProduct(product, qty) {
        const PP = PromoPricing();
        const listPrice = Number(product.preco_original ?? product.preco) || 0;
        const promo = PP ? PP.promoMetaFromProduct(product) : null;
        const base = {
            id: product.id,
            sku: product.sku || '',
            nome: product.nome,
            categoria: product.categoria,
            preco_lista: listPrice,
            preco: Number(product.preco) || listPrice,
            imagem: product.imagem,
            estoque: Number.isFinite(product.estoque) ? product.estoque : undefined,
            quantidade: qty,
            em_promocao: !!product.em_promocao,
            promo_tipo: promo ? promo.promo_tipo : '',
            promo_rule_value: promo ? promo.promo_rule_value : 0,
            promo_min_qty: promo ? promo.promo_min_qty : 1,
            promo_free_qty: promo ? promo.promo_free_qty : 0,
            promo_nome: promo ? promo.promo_nome : '',
            promo_badge: promo ? promo.promo_badge : '',
        };
        return PP ? PP.applyPromoToItem(base) : base;
    }

    function mergeProductMeta(item, product) {
        const PP = PromoPricing();
        const listPrice = Number(product.preco_original ?? product.preco) || item.preco_lista || item.preco;
        const promo = PP ? PP.promoMetaFromProduct(product) : null;
        const merged = {
            ...item,
            sku: product.sku || item.sku,
            nome: product.nome || item.nome,
            categoria: product.categoria || item.categoria,
            imagem: product.imagem || item.imagem,
            estoque: Number.isFinite(product.estoque) ? product.estoque : item.estoque,
            preco_lista: listPrice,
            em_promocao: !!product.em_promocao,
            promo_tipo: promo ? promo.promo_tipo : '',
            promo_rule_value: promo ? promo.promo_rule_value : 0,
            promo_min_qty: promo ? promo.promo_min_qty : 1,
            promo_free_qty: promo ? promo.promo_free_qty : 0,
            promo_nome: promo ? promo.promo_nome : '',
            promo_badge: promo ? promo.promo_badge : '',
        };
        return PP ? PP.applyPromoToItem(merged) : merged;
    }

    function recalculateAll(items) {
        const PP = PromoPricing();
        if (!PP) return items;
        return PP.recalculateItems(items);
    }

    const Cart = {
        KEY: STORAGE_KEY,
        EVENT: EVENT_NAME,

        getItems() {
            return recalculateAll(readRaw());
        },

        setItems(items) {
            writeRaw(recalculateAll(Array.isArray(items) ? items : []));
        },

        add(product, qty = 1) {
            if (!product || product.id === undefined || product.id === null) return;
            const quantidade = clampQty(qty, product.estoque);
            const items = recalculateAll(readRaw());
            const idStr = String(product.id);
            const existing = items.find(i => String(i.id) === idStr);
            if (existing) {
                existing.quantidade = clampQty(
                    existing.quantidade + quantidade,
                    product.estoque,
                );
                Object.assign(existing, mergeProductMeta(existing, product));
            } else {
                items.push(itemFromProduct(product, quantidade));
            }
            writeRaw(recalculateAll(items));
        },

        updateQty(id, qty) {
            const items = recalculateAll(readRaw());
            const idStr = String(id);
            const item = items.find(i => String(i.id) === idStr);
            if (!item) return;
            item.quantidade = clampQty(qty, item.estoque);
            writeRaw(recalculateAll(items));
        },

        increment(id, step = 1) {
            const items = recalculateAll(readRaw());
            const idStr = String(id);
            const item = items.find(i => String(i.id) === idStr);
            if (!item) return;
            item.quantidade = clampQty(item.quantidade + step, item.estoque);
            writeRaw(recalculateAll(items));
        },

        decrement(id, step = 1) {
            const items = recalculateAll(readRaw());
            const idStr = String(id);
            const item = items.find(i => String(i.id) === idStr);
            if (!item) return;
            const next = item.quantidade - step;
            if (next <= 0) {
                writeRaw(items.filter(i => String(i.id) !== idStr));
            } else {
                item.quantidade = clampQty(next, item.estoque);
                writeRaw(recalculateAll(items));
            }
        },

        remove(id) {
            const idStr = String(id);
            writeRaw(readRaw().filter(i => String(i.id) !== idStr));
        },

        clear() {
            try {
                sessionStorage.removeItem(STORAGE_KEY);
            } catch (_) { /* noop */ }
            window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: { items: [] } }));
        },

        count() {
            return this.getItems().reduce((acc, i) => acc + (Number(i.quantidade) || 0), 0);
        },

        total() {
            return this.getItems().reduce(
                (acc, i) => acc + (Number(i.subtotal != null ? i.subtotal : i.preco * i.quantidade) || 0),
                0,
            );
        },

        subtotalLista() {
            return this.getItems().reduce(
                (acc, i) => acc + (Number(i.preco_lista ?? i.preco) || 0) * (Number(i.quantidade) || 0),
                0,
            );
        },

        economiaTotal() {
            return Math.max(0, this.subtotalLista() - this.total());
        },

        getTotals() {
            const PP = PromoPricing();
            if (PP) return PP.getTotals(readRaw());
            return {
                items: this.getItems(),
                total: this.total(),
                subtotalLista: this.subtotalLista(),
                economiaTotal: this.economiaTotal(),
                count: this.count(),
            };
        },

        isEmpty() {
            return readRaw().length === 0;
        },

        formatBRL(value) {
            const n = Number(value) || 0;
            return n.toLocaleString('pt-BR', {
                style: 'currency',
                currency: 'BRL',
            });
        },

        /** Atualiza metadados e preços a partir do mapa id→produto do catálogo. */
        syncPricesFromProductMap(productMap) {
            if (!productMap || typeof productMap.forEach !== 'function') return;
            const items = readRaw();
            let changed = false;
            const next = items.map(item => {
                const p = productMap.get(String(item.id));
                if (!p) return item;
                const merged = mergeProductMeta(item, p);
                if (JSON.stringify(merged) !== JSON.stringify(item)) changed = true;
                return merged;
            });
            if (changed) writeRaw(recalculateAll(next));
        },

        /** Aplica cotação do servidor (POST /api/carrinho/cotacao). */
        applyServerQuote(quote) {
            if (!quote || !Array.isArray(quote.items)) return;
            const byId = new Map(quote.items.map(row => [String(row.id), row]));
            const items = readRaw().map(item => {
                const row = byId.get(String(item.id));
                if (!row) return item;
                return {
                    ...item,
                    preco_lista: Number(row.preco_lista ?? item.preco_lista) || item.preco_lista,
                    preco: Number(row.preco) || item.preco,
                    subtotal: Number(row.subtotal) || item.subtotal,
                    economia: Number(row.economia) || 0,
                    promo_aplicada: !!row.em_promocao,
                    promo_nome: row.promo_nome || item.promo_nome || '',
                };
            });
            writeRaw(items);
        },

        subscribe(handler) {
            const listener = e => handler(e.detail ? e.detail.items : readRaw(), e);
            window.addEventListener(EVENT_NAME, listener);
            return () => window.removeEventListener(EVENT_NAME, listener);
        },
    };

    window.Cart = Cart;
})();
