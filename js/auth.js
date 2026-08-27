// KaronlineLive — comptes KJ : enregistrement / connexion via l'API centrale.
// Phase de test amis/famille : aucun paiement réel ; carte optionnelle masquée.
const AUTH_API = 'https://api.karonlinelive.com';
const AUTH_TOKEN_KEY = 'kl_auth_token';
const AUTH_EMAIL_KEY = 'kl_auth_email';
const AUTH_CARD_KEY = 'kl_auth_card';

const AUTH_ERRORS = {
  'EMAIL TAKEN': 'Ce courriel possède déjà un compte.',
  'INVALID EMAIL': 'Adresse mail invalide.',
  'WEAK PASSWORD': 'Mot de passe trop court (8 caractères minimum).',
  'WRONG CREDENTIALS': 'Courriel ou mot de passe incorrect.',
  'CARD INVALID': 'Numéro de carte invalide.',
  'BAD REQUEST': 'Requête incomplète.',
  'TOKEN INVALID': 'Session expirée. Reconnectez-vous.',
};

function authReadState() {
  return {
    token: localStorage.getItem(AUTH_TOKEN_KEY) || '',
    email: localStorage.getItem(AUTH_EMAIL_KEY) || '',
    card: localStorage.getItem(AUTH_CARD_KEY) || '',
  };
}

function authSaveSession(token, email, card) {
  localStorage.setItem(AUTH_TOKEN_KEY, token || '');
  localStorage.setItem(AUTH_EMAIL_KEY, email || '');
  localStorage.setItem(AUTH_CARD_KEY, card || '');
}

function authClearSession() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem(AUTH_EMAIL_KEY);
  localStorage.removeItem(AUTH_CARD_KEY);
}

async function authFetch(path, payload, token) {
  const options = { method: payload ? 'POST' : 'GET' };
  const headers = {};
  if (payload !== undefined) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(payload);
  }
  if (token) headers.Authorization = `Bearer ${token}`;
  options.headers = headers;

  const response = await fetch(`${AUTH_API}${path}`, options);
  let data = {};
  try {
    data = await response.json();
  } catch {
    /* corps vide ou illisible */
  }
  if (!response.ok) {
    const code = String(data.error || `HTTP_${response.status}`);
    const error = new Error(AUTH_ERRORS[code] || `Erreur ${response.status} (${code})`);
    error.code = code;
    throw error;
  }
  return data;
}

function authMaskCard(raw) {
  const digits = String(raw || '').replace(/\D/g, '');
  if (digits.length < 12 || digits.length > 19) return '';
  const brand = digits.startsWith('4')
    ? 'Visa'
    : (/^5[1-5]/.test(digits) ? 'Mastercard' : '');
  return `${brand ? brand + ' ••••' : ''}${digits.slice(-4)}`;
}
// ---- Rendu de la zone compte ------------------------------------------
function authRenderUI() {
  const state = authReadState();
  const loggedIn = Boolean(state.token && state.email);
  document.querySelectorAll('[data-auth]').forEach((block) => {
    const member = block.dataset.auth === 'member';
    block.hidden = member ? !loggedIn : loggedIn;
  });
  document.querySelectorAll('[data-account-email]').forEach((el) => {
    el.textContent = state.email;
  });
  document.querySelectorAll('[data-account-card]').forEach((el) => {
    el.textContent = state.card ? ` · ${state.card}` : '';
  });
}

function authBusy(form, submitLabel) {
  const button = form.querySelector('.submit-button');
  if (button) {
    button.disabled = true;
    button.textContent = submitLabel;
  }
}

function authIdle(form, idleLabel) {
  const button = form.querySelector('.submit-button');
  if (button) {
    button.disabled = false;
    button.textContent = idleLabel;
  }
}

function authFeedback(id, message, isError) {
  const output = document.querySelector(`#${id}`);
  if (!output) return;
  output.textContent = message;
  output.style.color = isError ? '#ff6b6b' : '#4ade80';
  output.classList.add('is-visible');
}

function togglePasswordVisibility(button) {
  const inputId = button.dataset.passwordToggle;
  const input = document.getElementById(inputId);
  if (!input) return;

  const shouldShow = input.type === 'password';
  input.type = shouldShow ? 'text' : 'password';
  button.innerHTML = shouldShow ? '&#128065;&#x338;' : '&#128065;';
  button.setAttribute('aria-label', shouldShow ? 'Masquer le mot de passe' : 'Afficher le mot de passe');
  button.setAttribute('aria-pressed', String(shouldShow));
}

async function authValidateStoredSession() {
  const state = authReadState();
  if (!state.token || !state.email) return true;

  try {
    const data = await authFetch('/auth/me', undefined, state.token);
    const freshCard = data.card_label || state.card || '';
    authSaveSession(state.token, data.email || state.email, freshCard);
    authRenderUI();
    return true;
  } catch (error) {
    authClearSession();
    authRenderUI();
    if (typeof authFeedback === 'function') {
      authFeedback('login-result', AUTH_ERRORS[error.code] || error.message, true);
    }
    document.querySelector('#login-dialog')?.showModal();
    return false;
  }
}

async function handleSubmitRegister(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const email = form.querySelector('#reg-email').value.trim();
  const password = form.querySelector('#reg-password').value;
  const cardInput = form.querySelector('#reg-card');
  const card = cardInput ? cardInput.value.trim() : '';

  authBusy(form, 'Envoi...');
  try {
    const digits = String(card || '').replace(/\D/g, '');
    const payload = { email, password };
    if (digits.length >= 12) {
      payload.card_brand = digits.startsWith('4')
        ? 'Visa'
        : (/^5[1-5]/.test(digits) ? 'Mastercard' : '');
      payload.card_last4 = digits.slice(-4);
    }
    const data = await authFetch('/auth/register', payload);
    authSaveSession(data.token, data.email, data.card_label || '');
    document.querySelector('#register-dialog')?.close();
    authRenderUI();
    if (typeof loadCatalogue === 'function') loadCatalogue();
  } catch (error) {
    authFeedback('register-result', error.message, true);
  } finally {
    authIdle(form, 'Créer mon compte');
    form.querySelector('#reg-password').value = '';
  }
}

async function handleSubmitLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const email = form.querySelector('#login-email').value.trim();
  const password = form.querySelector('#login-password').value;

  authBusy(form, 'Connexion...');
  try {
    const data = await authFetch('/auth/login', { email, password });
    authSaveSession(data.token, data.email, data.card_label || '');
    document.querySelector('#login-dialog')?.close();
    authRenderUI();
    if (typeof loadCatalogue === 'function') loadCatalogue();
  } catch (error) {
    authFeedback('login-result', error.message, true);
  } finally {
    authIdle(form, 'Connexion');
    form.querySelector('#login-password').value = '';
  }
}

async function handleLogout() {
  const state = authReadState();
  try {
    if (state.token) await authFetch('/auth/logout', {}, state.token);
  } catch {
    /* la session locale est purgée même si le serveur est injoignable */
  }
  authClearSession();
  authRenderUI();
}

function initAuthPage() {
  document.querySelectorAll('[data-open-auth]').forEach((opener) => {
    opener.addEventListener('click', (event) => {
      event.preventDefault();
      const dialogId = opener.dataset.openAuth;
      if (dialogId === 'register-dialog' || dialogId === 'login-dialog') {
        const registerForm = document.querySelector('#register-form');
        if (dialogId === 'register-dialog' && registerForm &&
            !document.querySelector('#reg-email')) {
          /* page sans formulaire d'enregistrement : renvoyer vers l'accueil */
          window.location.href = `index.html#register`;
          return;
        }
      }
      document.querySelector(`#${dialogId}`)?.showModal();
    });
  });

  document.querySelector('#register-form')
    ?.addEventListener('submit', handleSubmitRegister);
  document.querySelector('#login-form')
    ?.addEventListener('submit', handleSubmitLogin);
  document.querySelectorAll('[data-logout]').forEach((button) => {
    button.addEventListener('click', handleLogout);
  });
  authRenderUI();
  authValidateStoredSession();
}

document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-password-toggle]');
  if (button) togglePasswordVisibility(button);
});

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAuthPage);
} else {
  initAuthPage();
}
