/**
 * Módulo de carrinho compartilhado entre catálogo e pagamento.
 *
 * Armazena o estado em sessionStorage (dura enquanto a aba do totem estiver
 * aberta; é limpo na tela de welcome). Emite o evento "cart:changed" em
 * window sempre que o carrinho muda, permitindo que badge, drawer e demais
 * telas se mantenham em sincronia sem acoplamento.
 *
 * Estrutura de cada item:
 * { id, nome, categoria, preco, imagem, estoque?, quantidade }
 */
(() => {
    'use strict';

    const STORAGE_KEY = 'totem_cart_v1';
    const EVENT_NAME = 'cart:changed';

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

    const Cart = {
        KEY: STORAGE_KEY,
        EVENT: EVENT_NAME,

        getItems() {
            return readRaw();
        },

        setItems(items) {
            writeRaw(Array.isArray(items) ? items : []);
        },

        add(product, qty = 1) {
            if (!product || product.id === undefined || product.id === null) return;
            const quantidade = clampQty(qty, product.estoque);
            const items = readRaw();
            const idStr = String(product.id);
            const existing = items.find(i => String(i.id) === idStr);
            if (existing) {
                existing.quantidade = clampQty(
                    existing.quantidade + quantidade,
                    product.estoque
                );
            } else {
                items.push({
                    id: product.id,
                    nome: product.nome,
                    categoria: product.categoria,
                    preco: Number(product.preco) || 0,
                    imagem: product.imagem,
                    estoque: Number.isFinite(product.estoque) ? product.estoque : undefined,
                    quantidade,
                });
            }
            writeRaw(items);
        },

        updateQty(id, qty) {
            const items = readRaw();
            const idStr = String(id);
            const item = items.find(i => String(i.id) === idStr);
            if (!item) return;
            item.quantidade = clampQty(qty, item.estoque);
            writeRaw(items);
        },

        increment(id, step = 1) {
            const items = readRaw();
            const idStr = String(id);
            const item = items.find(i => String(i.id) === idStr);
            if (!item) return;
            item.quantidade = clampQty(item.quantidade + step, item.estoque);
            writeRaw(items);
        },

        decrement(id, step = 1) {
            const items = readRaw();
            const idStr = String(id);
            const item = items.find(i => String(i.id) === idStr);
            if (!item) return;
            const next = item.quantidade - step;
            if (next <= 0) {
                const filtered = items.filter(i => String(i.id) !== idStr);
                writeRaw(filtered);
            } else {
                item.quantidade = clampQty(next, item.estoque);
                writeRaw(items);
            }
        },

        remove(id) {
            const idStr = String(id);
            const items = readRaw().filter(i => String(i.id) !== idStr);
            writeRaw(items);
        },

        clear() {
            try {
                sessionStorage.removeItem(STORAGE_KEY);
            } catch (_) { /* noop */ }
            window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: { items: [] } }));
        },

        count() {
            return readRaw().reduce((acc, i) => acc + (Number(i.quantidade) || 0), 0);
        },

        total() {
            return readRaw().reduce(
                (acc, i) => acc + (Number(i.preco) || 0) * (Number(i.quantidade) || 0),
                0
            );
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

        subscribe(handler) {
            const listener = e => handler(e.detail ? e.detail.items : readRaw(), e);
            window.addEventListener(EVENT_NAME, listener);
            return () => window.removeEventListener(EVENT_NAME, listener);
        },
    };

    window.Cart = Cart;
})();
