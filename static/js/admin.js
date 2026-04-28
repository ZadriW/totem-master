/**
 * Painel administrativo — interações de front.
 *
 * - Toggle de detalhes por transação (aria-expanded + hidden).
 * - Confirmação opcional antes de submeter formulários sensíveis
 *   (entrada/saída/ajuste/ativação de produto) via `data-confirm`.
 * - Confirmação em botões isolados via `data-confirm`.
 */
(() => {
    'use strict';

    // --- 1. Expansão de linhas do histórico de transações --------------------
    // Delegação para linhas injetadas depois (ex.: atualização ao vivo no painel do vendedor).
    document.querySelector('.admin-main')?.addEventListener('click', event => {
        const btn = event.target.closest('.admin-tx__row');
        if (!btn) return;
        const expanded = btn.getAttribute('aria-expanded') === 'true';
        const targetId = btn.getAttribute('aria-controls');
        const details = targetId ? document.getElementById(targetId) : null;
        btn.setAttribute('aria-expanded', String(!expanded));
        btn.classList.toggle('is-open', !expanded);
        if (details) details.hidden = expanded;
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
