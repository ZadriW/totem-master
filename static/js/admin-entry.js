/**
 * Gatilho oculto: 5 cliques consecutivos na logo da tela de boas-vindas
 * (`.welcome__logo`) abrem o login do painel administrativo. O contador
 * reinicia se o usuário passar mais de 2 segundos sem clicar, evitando
 * falso positivo.
 */
(() => {
    'use strict';

    const logo = document.querySelector('.welcome__logo');
    if (!logo) return;

    const REQUIRED_CLICKS = 5;
    const WINDOW_MS = 2000;

    let count = 0;
    let resetTimer = null;

    logo.addEventListener('click', event => {
        count += 1;
        clearTimeout(resetTimer);
        resetTimer = setTimeout(() => { count = 0; }, WINDOW_MS);

        if (count >= REQUIRED_CLICKS) {
            event.preventDefault();
            event.stopPropagation();
            count = 0;
            window.location.assign('/admin/login');
        }
    });
})();
