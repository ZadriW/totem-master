/**
 * Modo escuro dos painéis Admin e Vendedor — alternância e persistência (localStorage).
 */
(() => {
    'use strict';

    const STORAGE_KEY = 'totem-admin-theme';
    const root = document.documentElement;

    function isDark() {
        return root.classList.contains('admin-theme--dark');
    }

    function apply(theme) {
        const dark = theme === 'dark';
        root.classList.toggle('admin-theme--dark', dark);
        try {
            localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light');
        } catch (_) {
            /* ignore */
        }
        syncToggleButtons();
    }

    function syncToggleButtons() {
        const dark = isDark();
        document.querySelectorAll('[data-admin-theme-toggle]').forEach((btn) => {
            btn.setAttribute('aria-pressed', dark ? 'true' : 'false');
            btn.setAttribute('aria-label', dark ? 'Ativar modo claro' : 'Ativar modo escuro');
            btn.setAttribute('title', dark ? 'Modo claro' : 'Modo escuro');
            const icon = btn.querySelector('i');
            if (icon) {
                icon.className = dark ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
            }
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        syncToggleButtons();
        document.querySelectorAll('[data-admin-theme-toggle]').forEach((btn) => {
            btn.addEventListener('click', () => {
                apply(isDark() ? 'light' : 'dark');
            });
        });
    });

    window.TotemAdminTheme = { apply, isDark };
})();
