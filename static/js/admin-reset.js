/**
 * Painel admin — reinício do sistema: habilita botão e confirma envio.
 */
(() => {
    'use strict';

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
                'O estoque voltará aos valores de “estoque inicial” e o histórico ' +
                'de movimentações (exceto inicial) será removido. Esta ação não pode ser desfeita.'
        );
        if (!ok) event.preventDefault();
    });
})();
