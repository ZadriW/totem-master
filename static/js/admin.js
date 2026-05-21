/**
 * Painel administrativo — interações de front.
 *
 * - Histórico de transações: copiar código do pedido; apenas um detalhe expandido por tabela (acordeão).
 * - Toggle do painel “Dados e acesso” no detalhe do vendedor (aria-expanded + hidden).
 * - Confirmação opcional antes de submeter formulários sensíveis
 *   (entrada/saída/ajuste/ativação de produto) via `data-confirm`.
 * - Confirmação em botões isolados via `data-confirm`.
 */
(() => {
    'use strict';

    function copyTextToClipboard(text) {
        const v = String(text || '').trim();
        if (!v) return Promise.reject(new Error('empty'));
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            return navigator.clipboard.writeText(v);
        }
        return new Promise((resolve, reject) => {
            const ta = document.createElement('textarea');
            ta.value = v;
            ta.setAttribute('readonly', '');
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            ta.style.top = '0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            try {
                const ok = document.execCommand('copy');
                document.body.removeChild(ta);
                if (ok) resolve();
                else reject(new Error('execCommand'));
            } catch (e) {
                document.body.removeChild(ta);
                reject(e);
            }
        });
    }

    function triggerAdminTxCopyOrder(el) {
        const order = (el.getAttribute('data-copy-order') || '').trim();
        if (!order) return;
        const restoreLabel = `Copiar código do pedido ${order}`;
        copyTextToClipboard(order)
            .then(() => {
                el.classList.add('is-done');
                el.setAttribute('aria-label', `Copiado: ${order}`);
                window.clearTimeout(el._adminTxCopyReset);
                el._adminTxCopyReset = window.setTimeout(() => {
                    el.classList.remove('is-done');
                    el.setAttribute('aria-label', restoreLabel);
                }, 1600);
            })
            .catch(() => {});
    }

    // --- 1. Expansão de linhas do histórico de transações --------------------
    // Delegação para linhas injetadas depois (ex.: atualização ao vivo no painel do vendedor).
    document.querySelector('.admin-main')?.addEventListener('click', event => {
        const collapseBtn = event.target.closest('.admin-section__collapse-toggle');
        if (collapseBtn) {
            const panelId = collapseBtn.getAttribute('aria-controls');
            const panel = panelId ? document.getElementById(panelId) : null;
            if (!panel) return;
            const expanded = collapseBtn.getAttribute('aria-expanded') === 'true';
            const nextExpanded = !expanded;
            collapseBtn.setAttribute('aria-expanded', String(nextExpanded));
            panel.hidden = !nextExpanded;
            const icon = collapseBtn.querySelector('i');
            if (icon) {
                icon.className = nextExpanded
                    ? 'fa-solid fa-chevron-up'
                    : 'fa-solid fa-chevron-down';
            }
            return;
        }

        const copyCtrl = event.target.closest('.admin-tx__copy-order');
        if (copyCtrl) {
            event.preventDefault();
            event.stopPropagation();
            triggerAdminTxCopyOrder(copyCtrl);
            return;
        }

        const btn = event.target.closest('.admin-tx__row');
        if (!btn) return;
        if (event.target.closest('a')) return;
        if (event.target.closest('.admin-tx__discard-form')) return;
        toggleAdminTxRowAccordion(btn);
    });

    /** Uma única linha de transação aberta por `.admin-table` (detalhe tipo acordeão). */
    function closeSiblingAdminTxRows(tableRoot, exceptBtn) {
        if (!tableRoot || !exceptBtn) return;
        tableRoot.querySelectorAll('.admin-tx__row.is-open').forEach(row => {
            if (row === exceptBtn) return;
            row.setAttribute('aria-expanded', 'false');
            row.classList.remove('is-open');
            const oid = row.getAttribute('aria-controls');
            const otherDetails = oid ? document.getElementById(oid) : null;
            if (otherDetails) otherDetails.hidden = true;
        });
    }

    function toggleAdminTxRowAccordion(btn) {
        const expanded = btn.getAttribute('aria-expanded') === 'true';
        const opening = !expanded;
        const targetId = btn.getAttribute('aria-controls');
        const details = targetId ? document.getElementById(targetId) : null;
        if (opening) {
            const tableRoot = btn.closest('.admin-table');
            closeSiblingAdminTxRows(tableRoot, btn);
        }
        btn.setAttribute('aria-expanded', String(!expanded));
        btn.classList.toggle('is-open', !expanded);
        if (details) details.hidden = expanded;
    }

    // `<div role="button">` no painel do vendedor: Enter/Espaço abrem/fecham (sem click nativo).
    document.querySelector('.admin-main')?.addEventListener('keydown', event => {
        if (event.key !== 'Enter' && event.key !== ' ') return;

        const copyCtrl = event.target.closest('.admin-tx__copy-order');
        if (copyCtrl) {
            event.preventDefault();
            event.stopPropagation();
            triggerAdminTxCopyOrder(copyCtrl);
            return;
        }

        const btn = event.target.closest('.admin-tx__row');
        if (!btn || btn.tagName === 'BUTTON') return;
        if (btn.getAttribute('role') !== 'button') return;
        if (event.target.closest('a')) return;
        if (event.target.closest('.admin-tx__discard-form')) return;
        event.preventDefault();
        toggleAdminTxRowAccordion(btn);
    });

    // --- 2. Confirmação de formulários sensíveis -----------------------------
    document.querySelectorAll('form[data-confirm]').forEach(form => {
        form.addEventListener('submit', event => {
            const message = form.getAttribute('data-confirm') || 'Confirmar?';
            if (!window.confirm(message)) {
                event.preventDefault();
            }
        });
    });

    // --- 3. Confirmação em botões isolados -----------------------------------
    // Útil para botões de ativar/desativar que ficam em um <form> sem data-confirm
    // próprio mas precisam perguntar antes.
    document.querySelectorAll('button[data-confirm]').forEach(btn => {
        btn.addEventListener('click', event => {
            const message = btn.getAttribute('data-confirm') || 'Confirmar?';
            if (!window.confirm(message)) {
                event.preventDefault();
            }
        });
    });

    // --- 4. Auto-dismiss das flash messages ----------------------------------
    document.querySelectorAll('.admin-flash').forEach(el => {
        setTimeout(() => {
            el.style.transition = 'opacity 400ms ease, transform 400ms ease';
            el.style.opacity = '0';
            el.style.transform = 'translateY(-6px)';
            setTimeout(() => el.remove(), 450);
        }, 6000);
    });
})();
