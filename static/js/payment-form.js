/**
 * Formulário de dados do cliente em /pagamento
 * - Validação de CPF
 * - Busca automática de endereço via ViaCEP
 * - Verificação de CRO via API Consultar.io (proxy Flask)
 * - Salva em sessionStorage e disponibiliza para payment-waiting.js
 *
 * Em /pagamento/aguardando não há formulário: load/clear continuam disponíveis.
 */
(() => {
    'use strict';

    const STORAGE_KEY = 'totem_client_data_v1';

    const form = document.getElementById('paymentForm');
    const zipInput = document.getElementById('paymentZipcode');
    const searchCepBtn = document.getElementById('paymentSearchCep');
    const cepLoading = document.getElementById('cepLoading');
    const addressInput = document.getElementById('paymentAddress');
    const cityInput = document.getElementById('paymentCity');
    const stateSelect = document.getElementById('paymentState');

    // Elementos do bloco CRO
    const croUFSelect = document.getElementById('paymentCroUF');
    const croNumeroInput = document.getElementById('paymentCroNumero');
    const croCategoriaSelect = document.getElementById('paymentCroCategoria');
    const searchCroBtn = document.getElementById('paymentSearchCro');
    const croLoading = document.getElementById('croLoading');
    const croResult = document.getElementById('croResult');
    const croResultIcon = document.getElementById('croResultIcon');
    const croResultTitle = document.getElementById('croResultTitle');
    const croResultDetails = document.getElementById('croResultDetails');

    function load() {
        try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (_) {
            return null;
        }
    }

    function clear() {
        try {
            sessionStorage.removeItem(STORAGE_KEY);
        } catch (_) {}
    }

    window.PaymentForm = {
        load,
        clear,
        isValid() {
            return false;
        },
        getData() {
            return null;
        },
        save() {
            return false;
        },
    };

    if (!form) {
        return;
    }

    /* -------------------------------------------------------------------- */
    /* Máscaras                                                             */
    /* -------------------------------------------------------------------- */
    function maskCPF(value) {
        const digits = value.replace(/\D/g, '');
        if (digits.length <= 3) return digits;
        if (digits.length <= 6) return `${digits.slice(0, 3)}.${digits.slice(3)}`;
        if (digits.length <= 9) return `${digits.slice(0, 3)}.${digits.slice(3, 6)}.${digits.slice(6)}`;
        return `${digits.slice(0, 3)}.${digits.slice(3, 6)}.${digits.slice(6, 9)}-${digits.slice(9, 11)}`;
    }

    function maskCEP(value) {
        const digits = value.replace(/\D/g, '');
        if (digits.length <= 5) return digits;
        return `${digits.slice(0, 5)}-${digits.slice(5, 8)}`;
    }

    function maskCRO(value) {
        return value.replace(/\D/g, '').slice(0, 7);
    }

    function validateCPF(cpf) {
        const digits = cpf.replace(/\D/g, '');
        if (digits.length !== 11) return false;
        if (/^(\d)\1+$/.test(digits)) return false;

        let sum = 0;
        for (let i = 0; i < 9; i++) sum += parseInt(digits.charAt(i), 10) * (10 - i);
        let check = 11 - (sum % 11);
        if (check >= 10) check = 0;
        if (check !== parseInt(digits.charAt(9), 10)) return false;

        sum = 0;
        for (let i = 0; i < 10; i++) sum += parseInt(digits.charAt(i), 10) * (11 - i);
        check = 11 - (sum % 11);
        if (check >= 10) check = 0;
        return check === parseInt(digits.charAt(10), 10);
    }

    /* -------------------------------------------------------------------- */
    /* CRO — verificação via API proxy                                      */
    /* -------------------------------------------------------------------- */
    function croFieldsReady() {
        return (
            croUFSelect && croUFSelect.value &&
            croNumeroInput && croNumeroInput.value.trim().length >= 1 &&
            croCategoriaSelect && croCategoriaSelect.value
        );
    }

    function syncCroBtn() {
        if (searchCroBtn) searchCroBtn.disabled = !croFieldsReady();
    }

    function croResultState(state, data) {
        if (!croResult) return;
        croResult.hidden = false;
        croResult.className = 'payment-cro__result payment-cro__result--' + state;

        if (state === 'success') {
            croResultIcon.innerHTML = '<i class="fa-solid fa-circle-check" aria-hidden="true"></i>';
            croResultTitle.textContent = data.nome_razao_social || 'Registro encontrado';
            croResultDetails.innerHTML = `
                <div class="payment-cro__result-row">
                    <dt>Situação</dt>
                    <dd class="payment-cro__situacao payment-cro__situacao--${(data.situacao || '').toLowerCase()}">${data.situacao || '—'}</dd>
                </div>
                <div class="payment-cro__result-row">
                    <dt>Categoria</dt>
                    <dd>${data.categoria || '—'}</dd>
                </div>
                <div class="payment-cro__result-row">
                    <dt>Registro</dt>
                    <dd>CRO-${data.uf || ''} ${data.numero_registro || ''}</dd>
                </div>`;
        } else if (state === 'notfound') {
            croResultIcon.innerHTML = '<i class="fa-solid fa-circle-xmark" aria-hidden="true"></i>';
            croResultTitle.textContent = 'Registro não encontrado';
            croResultDetails.innerHTML = '<div class="payment-cro__result-row"><dd>Verifique os dados informados e tente novamente.</dd></div>';
        } else {
            croResultIcon.innerHTML = '<i class="fa-solid fa-triangle-exclamation" aria-hidden="true"></i>';
            croResultTitle.textContent = 'Não foi possível verificar';
            croResultDetails.innerHTML = `<div class="payment-cro__result-row"><dd>${data.message || 'Tente novamente em alguns instantes.'}</dd></div>`;
        }
    }

    function croResultClear() {
        if (croResult) {
            croResult.hidden = true;
            croResult.className = 'payment-cro__result';
        }
    }

    async function searchCRO() {
        if (!croFieldsReady()) return;
        const uf = croUFSelect.value;
        const numero = croNumeroInput.value.trim();
        const categoria = croCategoriaSelect.value;

        searchCroBtn.disabled = true;
        croLoading.hidden = false;
        croResultClear();

        try {
            const params = new URLSearchParams({ uf, numero_registro: numero, categoria });
            const response = await fetch(`/api/cro/consultar?${params}`, {
                credentials: 'same-origin',
                headers: { Accept: 'application/json' },
            });
            const data = await response.json();

            if (response.ok) {
                croResultState('success', data);
            } else if (response.status === 404) {
                croResultState('notfound', data);
            } else {
                croResultState('error', data);
            }
        } catch (err) {
            croResultState('error', { message: 'Erro de rede. Verifique a conexão e tente novamente.' });
        } finally {
            croLoading.hidden = true;
            syncCroBtn();
        }
    }

    if (searchCroBtn) {
        searchCroBtn.addEventListener('click', searchCRO);
    }

    // Limpa resultado e re-sincroniza botão quando qualquer campo CRO muda
    [croUFSelect, croNumeroInput, croCategoriaSelect].forEach(el => {
        if (!el) return;
        el.addEventListener('change', () => { croResultClear(); syncCroBtn(); });
        el.addEventListener('input', () => { croResultClear(); syncCroBtn(); });
    });

    /* -------------------------------------------------------------------- */
    /* ViaCEP                                                               */
    /* -------------------------------------------------------------------- */
    async function searchCEP(cep) {
        const digits = cep.replace(/\D/g, '');
        if (digits.length !== 8) throw new Error('CEP incompleto');

        const response = await fetch(`https://viacep.com.br/ws/${digits}/json/`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (data.erro) throw new Error('CEP não encontrado');
        return data;
    }

    function fillAddressFields(data) {
        if (data.logradouro) addressInput.value = data.logradouro;
        if (data.localidade) cityInput.value = data.localidade;
        if (data.uf) stateSelect.value = data.uf.toUpperCase();
    }

    /* -------------------------------------------------------------------- */
    /* Listeners                                                            */
    /* -------------------------------------------------------------------- */
    form.addEventListener('input', event => {
        const input = event.target;
        if (input.name === 'cpf') {
            input.value = maskCPF(input.value);
            const valid = validateCPF(input.value);
            input.setCustomValidity(valid ? '' : 'CPF inválido');
        } else if (input.name === 'cro_numero') {
            input.value = maskCRO(input.value);
        } else if (input.name === 'zipcode') {
            input.value = maskCEP(input.value);
            const digits = input.value.replace(/\D/g, '');
            if (searchCepBtn) searchCepBtn.disabled = digits.length !== 8;
        }
    });

    if (searchCepBtn && zipInput && cepLoading) {
        searchCepBtn.addEventListener('click', async () => {
            searchCepBtn.disabled = true;
            cepLoading.hidden = false;
            try {
                const data = await searchCEP(zipInput.value);
                fillAddressFields(data);
            } catch (err) {
                console.warn('Erro ao buscar CEP:', err);
                alert(`Não foi possível buscar o CEP: ${err.message || 'erro desconhecido'}`);
            } finally {
                cepLoading.hidden = true;
                const digits = zipInput.value.replace(/\D/g, '');
                searchCepBtn.disabled = digits.length !== 8;
            }
        });

        zipInput.addEventListener('keydown', event => {
            if (event.key === 'Enter') {
                event.preventDefault();
                const digits = zipInput.value.replace(/\D/g, '');
                if (digits.length === 8 && !searchCepBtn.disabled) {
                    searchCepBtn.click();
                }
            }
        });
    }

    window.PaymentForm.isValid = function isValid() {
        return form.checkValidity();
    };

    window.PaymentForm.getData = function getData() {
        if (!form.checkValidity()) {
            form.reportValidity();
            return null;
        }
        const data = new FormData(form);
        return {
            name: (data.get('name') || '').trim(),
            cpf: (data.get('cpf') || '').trim(),
            cro_uf: (data.get('cro_uf') || '').trim(),
            cro_numero: (data.get('cro_numero') || '').trim(),
            cro_categoria: (data.get('cro_categoria') || '').trim(),
            zipcode: (data.get('zipcode') || '').trim(),
            address: (data.get('address') || '').trim(),
            number: (data.get('number') || '').trim(),
            complement: (data.get('complement') || '').trim(),
            city: (data.get('city') || '').trim(),
            state: (data.get('state') || '').trim(),
            payment_method: (data.get('payment_method') || 'cartao').trim().toLowerCase(),
        };
    };

    window.PaymentForm.save = function save() {
        const data = this.getData();
        if (!data) return false;
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
        } catch (_) {}
        return true;
    };

    const stored = load();
    if (stored) {
        if (stored.name) form.name.value = stored.name;
        if (stored.cpf) form.cpf.value = stored.cpf;
        if (stored.cro_uf) form.cro_uf.value = stored.cro_uf;
        if (stored.cro_numero) form.cro_numero.value = stored.cro_numero;
        if (stored.cro_categoria) form.cro_categoria.value = stored.cro_categoria;
        if (stored.zipcode) form.zipcode.value = stored.zipcode;
        if (stored.address) form.address.value = stored.address;
        if (stored.number) form.number.value = stored.number;
        if (stored.complement) form.complement.value = stored.complement;
        if (stored.city) form.city.value = stored.city;
        if (stored.state) form.state.value = stored.state;
        const pm = (stored.payment_method || 'cartao').toLowerCase();
        const pmRadio = form.querySelector(`input[name="payment_method"][value="${pm === 'pix' ? 'pix' : 'cartao'}"]`);
        if (pmRadio) pmRadio.checked = true;
    }

    syncCroBtn();
})();
