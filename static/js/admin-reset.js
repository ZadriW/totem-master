/**
 * Painel admin — reinício do sistema: collapse da danger zone, habilita botão e confirma envio.
 */
(() => {
    'use strict';

    const zone = document.querySelector('.admin-section--danger-zone');
    const toggle = document.getElementById('adminDangerZoneToggle');
    const panel = document.getElementById('adminDangerZonePanel');

    if (toggle && panel && zone) {
        toggle.addEventListener('click', () => {
            const willOpen = panel.hidden;
            panel.hidden = !willOpen;
            toggle.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
            zone.classList.toggle('is-expanded', willOpen);
        });
    }

    const form = document.getElementById('adminResetForm');
    const checkbox = document.getElementById('adminResetConfirm');
    const submit = document.getElementById('adminResetSubmit');

    if (!form || !checkbox || !submit) return;

    checkbox.addEventListener('change', () => {
        submit.disabled = !checkbox.checked;
    });

    form.addEventListener('submit', event => {
        if (!checkbox.checked) {
            event.preventDefault();
            return;
        }
        const ok = window.confirm(
            'Confirma o reinício do sistema?\n\n' +
                'Todas as vendas e dados de clientes serão apagados. ' +
                'O estoque no cadastro e em cada evento voltará a zero com novo “estoque inicial”, ' +
                'e o histórico de movimentações (exceto inicial) será removido. Esta ação não pode ser desfeita.'
        );
        if (!ok) event.preventDefault();
    });
})();
