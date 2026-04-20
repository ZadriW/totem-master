/**
 * Painel administrativo — interações de front.
 * Toggle de detalhes por transação (aria-expanded + atributo hidden).
 */
(() => {
    'use strict';

    const rows = document.querySelectorAll('.admin-tx__row');
    rows.forEach(btn => {
        btn.addEventListener('click', () => {
            const expanded = btn.getAttribute('aria-expanded') === 'true';
            const targetId = btn.getAttribute('aria-controls');
            const details = targetId ? document.getElementById(targetId) : null;
            btn.setAttribute('aria-expanded', String(!expanded));
            btn.classList.toggle('is-open', !expanded);
            if (details) details.hidden = expanded;
        });
    });
})();
