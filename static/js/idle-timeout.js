/**
 * Inatividade no totem:
 * - 2 min sem interação → modal "Ainda está aí?"
 * - após o modal, +1 min sem interação → redireciona para a página inicial (/)
 *
 * Qualquer interação válida reinicia o ciclo de 2 min e fecha o modal, se estiver aberto.
 */
(() => {
    'use strict';

    const IDLE_WARNING_MS = 2 * 60 * 1000;
    const IDLE_HOME_MS = 1 * 60 * 1000;

    /** mousemove dispara com muita frequência; demais eventos usam intervalo menor. */
    const THROTTLE_MOUSEMOVE_MS = 1000;
    const THROTTLE_OTHER_MS = 250;

    const overlay = document.getElementById('idle-timeout-overlay');
    const btnContinue = document.getElementById('idle-timeout-continue');

    if (!overlay) return;

    let warningTimerId = null;
    let homeTimerId = null;
    let modalVisible = false;
    let lastHandledActivity = 0;

    function clearTimers() {
        if (warningTimerId !== null) {
            clearTimeout(warningTimerId);
            warningTimerId = null;
        }
        if (homeTimerId !== null) {
            clearTimeout(homeTimerId);
            homeTimerId = null;
        }
    }

    function openModal() {
        overlay.classList.add('is-open');
        overlay.setAttribute('aria-hidden', 'false');
        document.body.classList.add('idle-modal-open');
        modalVisible = true;
        const focusable = btnContinue || overlay.querySelector('button');
        if (focusable && typeof focusable.focus === 'function') {
            focusable.focus();
        }
    }

    function closeModal() {
        overlay.classList.remove('is-open');
        overlay.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('idle-modal-open');
        modalVisible = false;
    }

    function goHome() {
        window.location.assign('/');
    }

    function showWarning() {
        clearTimers();
        openModal();
        homeTimerId = setTimeout(goHome, IDLE_HOME_MS);
    }

    /**
     * Reinicia o período de 2 min até o aviso. Fecha o modal se estiver aberto.
     */
    function resetIdleCycle() {
        clearTimers();
        if (modalVisible) {
            closeModal();
        }
        warningTimerId = setTimeout(showWarning, IDLE_WARNING_MS);
    }

    function onUserActivity(event) {
        const now = Date.now();
        const throttle =
            event && event.type === 'mousemove'
                ? THROTTLE_MOUSEMOVE_MS
                : THROTTLE_OTHER_MS;
        if (now - lastHandledActivity < throttle) return;
        lastHandledActivity = now;
        resetIdleCycle();
    }

    const otherEvents = [
        'click',
        'keydown',
        'touchstart',
        'pointerdown',
        'wheel',
        'scroll',
    ];

    otherEvents.forEach(evt => {
        document.addEventListener(evt, onUserActivity, { passive: true, capture: true });
    });

    document.addEventListener(
        'mousemove',
        onUserActivity,
        { passive: true, capture: true }
    );

    if (btnContinue) {
        btnContinue.addEventListener('click', e => {
            e.preventDefault();
            resetIdleCycle();
        });
    }

    overlay.addEventListener('click', e => {
        if (e.target === overlay) {
            resetIdleCycle();
        }
    });

    resetIdleCycle();
})();
