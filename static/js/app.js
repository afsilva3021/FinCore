document.addEventListener('click', (event) => {
  const toggle = event.target.closest('[data-password-toggle]');
  if (toggle) {
    const input = toggle.parentElement.querySelector('input');
    const visible = input.type === 'password';
    input.type = visible ? 'text' : 'password';
    toggle.setAttribute('aria-label', visible ? 'Ocultar senha' : 'Mostrar senha');
    toggle.setAttribute('aria-pressed', String(visible));
    const icon = toggle.querySelector('.fa-solid');
    if (icon) {
      icon.classList.toggle('fa-eye', visible);
      icon.classList.toggle('fa-eye-slash', !visible);
    }
  }

  if (event.target.closest('[data-sidebar-toggle]')) {
    document.querySelector('#sidebar')?.classList.toggle('open');
  }
});

const navAccordionButtons = [...document.querySelectorAll('[data-nav-accordion]')];
const setOpenNavAccordion = (activeButton, shouldOpen, revealEnd = false) => {
  navAccordionButtons.forEach((button) => {
    const panel = document.getElementById(button.getAttribute('aria-controls'));
    const isOpen = shouldOpen && button === activeButton;
    button.setAttribute('aria-expanded', String(isOpen));
    if (panel) panel.hidden = !isOpen;
    if (isOpen && revealEnd && panel) {
      requestAnimationFrame(() => {
        panel.lastElementChild?.scrollIntoView({ block: 'nearest' });
      });
    }
  });
};

navAccordionButtons.forEach((button) => {
  button.addEventListener('click', () => {
    const shouldOpen = button.getAttribute('aria-expanded') !== 'true';
    setOpenNavAccordion(button, shouldOpen, shouldOpen);
  });
});

const syncCurrentNavigation = () => {
  const currentUrl = new URL(window.location.href);
  const navLinks = [...document.querySelectorAll('.nav-group .nav-link[href]')];
  let activeLink = navLinks.find((link) => {
    const url = new URL(link.href, window.location.origin);
    return url.pathname === currentUrl.pathname && url.hash === currentUrl.hash;
  });
  if (!activeLink && !currentUrl.hash) {
    activeLink = navLinks.find((link) => {
      const url = new URL(link.href, window.location.origin);
      return url.pathname === currentUrl.pathname && !url.hash;
    });
  }

  navLinks.forEach((link) => {
    const isCurrent = link === activeLink;
    link.classList.toggle('active', isCurrent);
    if (isCurrent) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });

  const activeGroup = activeLink?.closest('.nav-group');
  const currentButton = activeGroup
    ? navAccordionButtons.find((button) => button.getAttribute('aria-controls') === activeGroup.id)
    : null;
  navAccordionButtons.forEach((button) => {
    const isCurrentGroup = button === currentButton;
    button.classList.toggle('is-current', isCurrentGroup);
    if (isCurrentGroup) button.setAttribute('aria-current', 'true');
    else button.removeAttribute('aria-current');
  });
  if (currentButton) setOpenNavAccordion(currentButton, true);
};

syncCurrentNavigation();
window.addEventListener('hashchange', syncCurrentNavigation);
document.querySelectorAll('.main-nav .nav-group .nav-link').forEach((link) => {
  link.addEventListener('click', () => {
    const activeGroup = link.closest('.nav-group');
    const activeButton = navAccordionButtons.find(
      (button) => button.getAttribute('aria-controls') === activeGroup?.id,
    );
    if (activeButton) setOpenNavAccordion(activeButton, true);
    if (window.matchMedia('(max-width: 760px)').matches) {
      document.querySelector('#sidebar')?.classList.remove('open');
    }
  });
});

document.querySelectorAll('.needs-validation').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (!form.checkValidity()) {
      event.preventDefault();
      event.stopPropagation();
    }
    form.classList.add('was-validated');
  });
});

const categoryTypeSelect = document.querySelector('[data-category-type]');
const categoryParentSelect = document.querySelector('[data-category-parent]');
const categoryKindSelect = document.querySelector('[data-category-kind]');
if (categoryTypeSelect && categoryParentSelect && categoryKindSelect) {
  const syncCategoryParents = () => {
    const isSynthetic = categoryKindSelect.value === 'synthetic';
    categoryParentSelect.disabled = isSynthetic;
    if (isSynthetic) categoryParentSelect.value = '';
    [...categoryParentSelect.options].forEach((option) => {
      if (!option.dataset.categoryParentType) return;
      option.hidden = isSynthetic || option.dataset.categoryParentType !== categoryTypeSelect.value;
      if (option.hidden && option.selected) categoryParentSelect.value = '';
    });
  };
  categoryTypeSelect.addEventListener('change', syncCategoryParents);
  categoryKindSelect.addEventListener('change', syncCategoryParents);
  syncCategoryParents();
}

const ledgerTypeSelect = document.querySelector('[data-ledger-type]');
const ledgerParentSelect = document.querySelector('[data-ledger-parent]');
if (ledgerTypeSelect && ledgerParentSelect) {
  const syncLedgerParents = () => {
    [...ledgerParentSelect.options].forEach((option) => {
      if (!option.dataset.ledgerParentType) return;
      option.hidden = option.dataset.ledgerParentType !== ledgerTypeSelect.value;
      if (option.hidden && option.selected) ledgerParentSelect.value = '';
    });
  };
  ledgerTypeSelect.addEventListener('change', syncLedgerParents);
  syncLedgerParents();
}

document.querySelectorAll('[data-title-terms]').forEach((termsSelect) => {
  const form = termsSelect.closest('form');
  const installmentFields = form?.querySelector('[data-title-installments]');
  const countInput = installmentFields?.querySelector('input[name="installment_count"]');
  const syncInstallments = () => {
    const isInstallment = termsSelect.value === 'installment';
    if (installmentFields) installmentFields.hidden = !isInstallment;
    if (countInput) countInput.required = isInstallment;
  };
  termsSelect.addEventListener('change', syncInstallments);
  syncInstallments();
});

const confirmationModalElement = document.querySelector('#fincoreConfirmModal');
let resolveConfirmation = null;
const showConfirmation = ({
  title = 'Confirmar ação',
  message,
  confirmText = 'Confirmar',
  variant = 'primary',
}) => new Promise((resolve) => {
  if (!confirmationModalElement || !window.bootstrap?.Modal) {
    resolve(false);
    return;
  }

  const modal = window.bootstrap.Modal.getOrCreateInstance(confirmationModalElement);
  const titleElement = confirmationModalElement.querySelector('#fincoreConfirmTitle');
  const messageElement = confirmationModalElement.querySelector('#fincoreConfirmMessage');
  const iconElement = confirmationModalElement.querySelector('[data-confirm-icon]');
  const acceptButton = confirmationModalElement.querySelector('[data-confirm-accept]');
  const danger = variant === 'danger';

  if (titleElement) titleElement.textContent = title;
  if (messageElement) messageElement.textContent = message || '';
  if (iconElement) iconElement.textContent = danger ? '!' : '?';
  if (acceptButton) {
    acceptButton.textContent = confirmText;
    acceptButton.className = `btn ${danger ? 'btn-danger' : 'btn-primary'}`;
  }
  confirmationModalElement.classList.toggle('is-danger', danger);

  resolveConfirmation = resolve;
  const finish = (accepted) => {
    if (!resolveConfirmation) return;
    const complete = resolveConfirmation;
    resolveConfirmation = null;
    complete(accepted);
  };
  if (acceptButton) acceptButton.onclick = () => {
    finish(true);
    modal.hide();
  };
  confirmationModalElement.addEventListener('hidden.bs.modal', () => finish(false), { once: true });
  modal.show();
});

const movementType = document.querySelector('[data-movement-type]');
if (movementType) {
  const syncMovementFields = () => {
    const transfer = movementType.value === 'transfer';
    document.querySelector('[data-target-account]')?.toggleAttribute('hidden', !transfer);
    document.querySelector('[data-category-field]')?.toggleAttribute('hidden', transfer);
    document.querySelector('[data-due-date]')?.toggleAttribute('hidden', transfer);
    document.querySelector('[data-settlement-field]')?.toggleAttribute('hidden', transfer);
    document.querySelectorAll('[data-category-type]').forEach((option) => {
      option.hidden = option.dataset.categoryType !== movementType.value;
    });
  };
  movementType.addEventListener('change', syncMovementFields);
  syncMovementFields();
}

const movementDrawers = [...document.querySelectorAll('[data-movement-drawer]')];
const drawerBackdrop = document.querySelector('[data-drawer-backdrop]');
const drawerStates = new Map();
const drawerOpenButton = (drawer) => document.querySelector(`[data-drawer-open][aria-controls="${drawer.id}"]`)
  || document.querySelector('[data-drawer-open]');
const closeDrawerImmediately = (drawer) => {
  drawer.classList.remove('is-open');
  drawer.setAttribute('aria-hidden', 'true');
  drawer.hidden = true;
  drawerOpenButton(drawer)?.setAttribute('aria-expanded', 'false');
  drawerStates.set(drawer, { ...drawerStates.get(drawer), changed: false, closing: false });
};

const openMovementDrawer = (drawer, trigger = null) => {
  if (!drawer) return;
  movementDrawers.filter((item) => item !== drawer && !item.hidden).forEach(closeDrawerImmediately);
  const openButton = drawerOpenButton(drawer);
  const state = drawerStates.get(drawer) || {};
  state.returnFocusTo = trigger || openButton;
  state.changed = false;
  state.closing = false;
  drawerStates.set(drawer, state);
  drawer.hidden = false;
  drawer.setAttribute('aria-hidden', 'false');
  openButton?.setAttribute('aria-expanded', 'true');
  drawerBackdrop && (drawerBackdrop.hidden = false);
  document.body.classList.add('drawer-open');
  requestAnimationFrame(() => {
    drawer.classList.add('is-open');
    drawerBackdrop?.classList.add('is-open');
  });
  window.setTimeout(() => {
    drawer.querySelector('input:not([type="hidden"]), select, textarea, button')?.focus();
  }, 40);
};

const closeMovementDrawer = async (drawer, force = false) => {
  const state = drawerStates.get(drawer) || {};
  if (drawer.hidden || state.closing) return;
  if (state.changed && !force) {
    const discard = await showConfirmation({
      title: 'Alterações não salvas',
      message: 'Você tem alterações não salvas. Deseja mesmo sair?',
      confirmText: 'Descartar alterações',
      variant: 'danger',
    });
    if (!discard) return;
  }
  if (state.changed) {
    drawer.querySelector('[data-movement-form]')?.reset();
    movementType?.dispatchEvent(new Event('change'));
  }
  state.changed = false;
  state.closing = true;
  drawerStates.set(drawer, state);
  drawer.classList.remove('is-open');
  drawer.setAttribute('aria-hidden', 'true');
  drawerOpenButton(drawer)?.setAttribute('aria-expanded', 'false');
  const anotherOpen = movementDrawers.some((item) => item !== drawer && !item.hidden);
  if (!anotherOpen) {
    drawerBackdrop?.classList.remove('is-open');
    document.body.classList.remove('drawer-open');
  }
  window.setTimeout(() => {
    drawer.hidden = true;
    if (!movementDrawers.some((item) => !item.hidden)) drawerBackdrop && (drawerBackdrop.hidden = true);
    state.closing = false;
    state.returnFocusTo?.focus();
  }, 240);
};

movementDrawers.forEach((drawer) => {
  drawerStates.set(drawer, { changed: false, closing: false, returnFocusTo: null });
  drawer.querySelector('[data-movement-form]')?.addEventListener('input', () => {
    drawerStates.get(drawer).changed = true;
  });
  drawer.querySelector('[data-movement-form]')?.addEventListener('change', () => {
    drawerStates.get(drawer).changed = true;
  });
  drawer.querySelectorAll('[data-drawer-close]').forEach((button) => {
    button.addEventListener('click', () => closeMovementDrawer(drawer));
  });
  if (drawer.hasAttribute('data-drawer-initially-open')) openMovementDrawer(drawer);
});

document.querySelectorAll('[data-drawer-open]').forEach((button) => {
  button.addEventListener('click', () => {
    const target = button.getAttribute('aria-controls');
    const drawer = movementDrawers.find((item) => item.id === target) || movementDrawers[0];
    openMovementDrawer(drawer, button);
  });
});

document.querySelectorAll('[data-category-subcategory]').forEach((button) => {
  button.addEventListener('click', () => {
    const drawer = document.querySelector('#category-drawer');
    const typeSelect = drawer?.querySelector('[data-category-type]');
    const kindSelect = drawer?.querySelector('[data-category-kind]');
    const parentSelect = drawer?.querySelector('[data-category-parent]');
    if (!drawer || !typeSelect || !kindSelect || !parentSelect) return;
    typeSelect.value = button.dataset.categorySubcategoryType;
    kindSelect.value = 'analytic';
    typeSelect.dispatchEvent(new Event('change'));
    kindSelect.dispatchEvent(new Event('change'));
    parentSelect.value = button.dataset.categorySubcategory;
    openMovementDrawer(drawer, button);
  });
});

drawerBackdrop?.addEventListener('click', () => {
  const currentDrawer = movementDrawers.find((item) => !item.hidden);
  if (currentDrawer) closeMovementDrawer(currentDrawer);
});

document.addEventListener('keydown', (event) => {
  const drawer = movementDrawers.find((item) => !item.hidden);
  if (!drawer || confirmationModalElement?.classList.contains('show')) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeMovementDrawer(drawer);
    return;
  }
  if (event.key !== 'Tab') return;
  const focusable = [...drawer.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')]
    .filter((element) => element.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

document.querySelectorAll('[data-postal-code]').forEach((input) => {
  const form = input.closest('form');
  const status = form?.querySelector('[data-postal-code-status]');
  input.addEventListener('blur', async () => {
    const cep = input.value.replace(/\D/g, '');
    if (cep.length !== 8) return;
    if (status) status.textContent = 'Consultando CEP…';
    input.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(`/contacts/postal-code?cep=${encodeURIComponent(cep)}`, {
        headers: { Accept: 'application/json' },
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || 'Não foi possível consultar o CEP.');
      input.value = result.postal_code || input.value;
      for (const field of ['street', 'district', 'city', 'state']) {
        const target = form.querySelector(`[data-postal-target="${field}"]`);
        if (target && result[field]) target.value = result[field];
      }
      if (status) status.textContent = 'Endereço preenchido pelo CEP. Você pode revisar os dados.';
    } catch (error) {
      if (status) status.textContent = error.message || 'Consulta indisponível; preencha o endereço manualmente.';
    } finally {
      input.removeAttribute('aria-busy');
    }
  });
});

document.querySelectorAll('[name="tax_id"]').forEach((input) => {
  input.addEventListener('input', () => {
    const digits = input.value.replace(/\D/g, '').slice(0, 14);
    input.value = digits.length <= 11
      ? digits.replace(/(\d{3})(\d)/, '$1.$2').replace(/(\d{3})(\d)/, '$1.$2').replace(/(\d{3})(\d{1,2})$/, '$1-$2')
      : digits.replace(/^(\d{2})(\d)/, '$1.$2').replace(/^(\d{2})\.(\d{3})(\d)/, '$1.$2.$3').replace(/\.(\d{3})(\d)/, '.$1/$2').replace(/(\d{4})(\d{1,2})$/, '$1-$2');
  });
});

document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
  button.addEventListener('click', () => {
    const root = document.documentElement;
    const nextTheme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = nextTheme;
    try { localStorage.setItem('fincore-theme', nextTheme); } catch { /* preferência mantém-se nesta página */ }
    const darkTheme = nextTheme === 'dark';
    button.setAttribute('aria-label', darkTheme ? 'Ativar tema claro' : 'Ativar tema escuro');
    button.setAttribute('title', darkTheme ? 'Ativar tema claro' : 'Ativar tema escuro');
    const icon = button.querySelector('[data-theme-icon]');
    if (icon) icon.textContent = darkTheme ? '☀' : '☾';
  });
});

document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
  const darkTheme = document.documentElement.dataset.theme === 'dark';
  button.setAttribute('aria-label', darkTheme ? 'Ativar tema claro' : 'Ativar tema escuro');
  button.setAttribute('title', darkTheme ? 'Ativar tema claro' : 'Ativar tema escuro');
  const icon = button.querySelector('[data-theme-icon]');
  if (icon) icon.textContent = darkTheme ? '☀' : '☾';
});

document.querySelectorAll('[data-add-journal-line]').forEach((button) => {
  button.addEventListener('click', () => {
    const form = button.closest('form');
    const container = form?.querySelector('[data-journal-lines]');
    const rows = container?.querySelectorAll('[data-journal-line]');
    if (!container || !rows || rows.length >= 12) return;
    const next = rows.length + 1;
    const row = rows[rows.length - 1].cloneNode(true);
    row.querySelectorAll('input, select').forEach((field) => {
      field.value = '';
      field.name = field.name.replace(/_\d+$/, `_${next}`);
    });
    row.querySelectorAll('[aria-label]').forEach((field) => {
      field.setAttribute('aria-label', field.getAttribute('aria-label').replace(/\d+$/, String(next)));
    });
    container.append(row);
  });
});

document.querySelectorAll('form[data-confirm]').forEach((form) => {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const accepted = await showConfirmation({
      title: 'Confirmar estorno',
      message: form.dataset.confirm,
      confirmText: 'Confirmar estorno',
      variant: 'danger',
    });
    if (accepted) HTMLFormElement.prototype.submit.call(form);
  });
});

const adapterSelect = document.querySelector('#adapter');
if (adapterSelect) {
  const amountField = document.querySelector('[data-amount-field]');
  const amountInput = document.querySelector('#amount');
  const syncAdapterFields = () => {
    const needsAmount = adapterSelect.value !== 'open_finance';
    amountField?.toggleAttribute('hidden', !needsAmount);
    amountInput?.toggleAttribute('required', needsAmount);
  };
  adapterSelect.addEventListener('change', syncAdapterFields);
  syncAdapterFields();
}

const accountTypeSelect = document.querySelector('[data-account-type]');
if (accountTypeSelect) {
  const bankFields = [...document.querySelectorAll('[data-bank-account-fields]')];
  const syncBankFields = () => {
    const isBankAccount = accountTypeSelect.value === 'bank';
    bankFields.forEach((group) => {
      group.hidden = !isBankAccount;
      const input = group.querySelector('input');
      if (input) input.required = isBankAccount;
    });
  };
  accountTypeSelect.addEventListener('change', syncBankFields);
  syncBankFields();
}

document.querySelectorAll('[data-branch-mask]').forEach((input) => {
  input.addEventListener('input', () => {
    const digits = input.value.replace(/\D/g, '').slice(0, 5);
    input.value = digits.length > 4 ? `${digits.slice(0, 4)}-${digits.slice(4)}` : digits;
  });
});
