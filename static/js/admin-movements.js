/**
 * Toggle dos dados do cliente nas movimentações de venda.
 * Comportamento em acordeão: apenas um painel aberto por vez.
 */
(() => {
    'use strict';

    const table =
        document.querySelector('.admin-table[aria-label="Movimentações"]') ||
        document.querySelector('.admin-table');
    const scope = table || document;

    function closeAllDetails() {
        scope.querySelectorAll('.admin-mov__details').forEach(panel => {
            panel.hidden = true;
        });
        scope.querySelectorAll('.admin-mov__toggle').forEach(b => {
            b.setAttribute('aria-expanded', 'false');
            const i = b.querySelector('i');
            if (i) i.className = 'fa-solid fa-chevron-down';
        });
    }

    const toggleButtons = scope.querySelectorAll('.admin-mov__toggle');

    toggleButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const movId = btn.dataset.toggle;
            const details = document.getElementById(`details-${movId}`);
            const icon = btn.querySelector('i');

            if (!details) return;

            const willOpen = details.hidden;

            if (willOpen) {
                closeAllDetails();
                details.hidden = false;
                if (icon) icon.className = 'fa-solid fa-chevron-up';
                btn.setAttribute('aria-expanded', 'true');
            } else {
                details.hidden = true;
                if (icon) icon.className = 'fa-solid fa-chevron-down';
                btn.setAttribute('aria-expanded', 'false');
            }
        });
    });
})();
