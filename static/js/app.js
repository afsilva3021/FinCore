document.addEventListener('click', (event) => {
  const toggle = event.target.closest('[data-password-toggle]');
  if (toggle) {
    const input = toggle.parentElement.querySelector('input');
    const visible = input.type === 'password';
    input.type = visible ? 'text' : 'password';
    toggle.setAttribute('aria-label', visible ? 'Ocultar senha' : 'Mostrar senha');
  }

  if (event.target.closest('[data-sidebar-toggle]')) {
    document.querySelector('#sidebar')?.classList.toggle('open');
  }
});

const navAccordionButtons = [...document.querySelectorAll('[data-nav-accordion]')];
const setOpenNavAccordion = (activeButton, shouldOpen) => {
  navAccordionButtons.forEach((button) => {
    const panel = document.getElementById(button.getAttribute('aria-controls'));
    const isOpen = shouldOpen && button === activeButton;
    button.setAttribute('aria-expanded', String(isOpen));
    if (panel) panel.hidden = !isOpen;
  });
};

navAccordionButtons.forEach((button) => {
  button.addEventListener('click', () => {
    const shouldOpen = button.getAttribute('aria-expanded') !== 'true';
    setOpenNavAccordion(button, shouldOpen);
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

const movementDrawer = document.querySelector('[data-movement-drawer]');
if (movementDrawer) {
  const backdrop = document.querySelector('[data-drawer-backdrop]');
  const openButton = document.querySelector('[data-drawer-open]');
  const movementForm = movementDrawer.querySelector('[data-movement-form]');
  let returnFocusTo = null;
  let hasUnsavedChanges = false;
  let isClosing = false;

  const openDrawer = (trigger = openButton) => {
    if (isClosing) return;
    returnFocusTo = trigger || openButton;
    movementDrawer.hidden = false;
    if (backdrop) backdrop.hidden = false;
    movementDrawer.setAttribute('aria-hidden', 'false');
    if (openButton) openButton.setAttribute('aria-expanded', 'true');
    document.body.classList.add('drawer-open');
    requestAnimationFrame(() => {
      movementDrawer.classList.add('is-open');
      backdrop?.classList.add('is-open');
    });
    window.setTimeout(() => {
      const firstField = movementDrawer.querySelector('input:not([type="hidden"]), select, textarea, button');
      firstField?.focus();
    }, 40);
  };

  const closeDrawer = async (force = false) => {
    if (movementDrawer.hidden || isClosing) return;
    if (hasUnsavedChanges && !force) {
      const discardChanges = await showConfirmation({
        title: 'Alterações não salvas',
        message: 'Você tem alterações não salvas. Deseja mesmo sair?',
        confirmText: 'Descartar alterações',
        variant: 'danger',
      });
      if (!discardChanges) return;
    }

    if (hasUnsavedChanges) {
      movementForm?.reset();
      movementType?.dispatchEvent(new Event('change'));
    }
    hasUnsavedChanges = false;
    movementDrawer.classList.remove('is-open');
    backdrop?.classList.remove('is-open');
    movementDrawer.setAttribute('aria-hidden', 'true');
    if (openButton) openButton.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('drawer-open');
    isClosing = true;
    window.setTimeout(() => {
      movementDrawer.hidden = true;
      if (backdrop) backdrop.hidden = true;
      isClosing = false;
      returnFocusTo?.focus();
    }, 240);
  };

  openButton?.addEventListener('click', () => openDrawer(openButton));
  movementDrawer.querySelectorAll('[data-drawer-close]').forEach((button) => {
    button.addEventListener('click', () => closeDrawer());
  });
  backdrop?.addEventListener('click', () => closeDrawer());
  movementForm?.addEventListener('input', () => { hasUnsavedChanges = true; });
  movementForm?.addEventListener('change', () => { hasUnsavedChanges = true; });

  document.addEventListener('keydown', (event) => {
    if (movementDrawer.hidden) return;
    if (confirmationModalElement?.classList.contains('show')) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeDrawer();
      return;
    }
    if (event.key !== 'Tab') return;

    const focusable = [...movementDrawer.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')]
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

  if (movementDrawer.hasAttribute('data-drawer-initially-open')) openDrawer(null);
}

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
