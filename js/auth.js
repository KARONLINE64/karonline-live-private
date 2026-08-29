// KaronlineLive — comptes KJ : enregistrement / connexion via l'API centrale.
// Phase de test amis/famille : aucun paiement réel ; carte optionnelle masquée.
const AUTH_API = (() => {
  const host = window.location.hostname;
  if (host === 'localhost' || host === '127.0.0.1' || host === '::1') {
    return 'http://localhost:8765';
  }
  return 'https://api.karonlinelive.com';
})();
const AUTH_TOKEN_KEY = 'kl_auth_token';
const AUTH_EMAIL_KEY = 'kl_auth_email';
const AUTH_CARD_KEY = 'kl_auth_card';

const AUTH_ERRORS = {
  'EMAIL TAKEN': 'Ce courriel possède déjà un compte.',
  'INVALID EMAIL': 'Adresse mail invalide.',
  'WEAK PASSWORD': 'Mot de passe trop court (8 caractères minimum).',
  'WRONG CREDENTIALS': 'Courriel ou mot de passe incorrect.',
  'EMAIL NOT VERIFIED': 'Veuillez valider votre adresse e-mail avant de vous connecter.',
  'INVALID CODE': 'Code de vérification invalide.',
  'CODE EXPIRED': 'Le code de vérification a expiré. Demandez un nouveau code.',
  'NO CODE SENT': 'Aucun code de vérification n’a été envoyé pour cet e-mail.',
  'CARD INVALID': 'Numéro de carte invalide.',
  'BAD REQUEST': 'Requête incomplète.',
  'TOKEN INVALID': 'Session expirée. Reconnectez-vous.',
  'ALREADY_CONNECTED': 'Ce compte est déjà connecté ailleurs.',
  'EMAIL NOT FOUND': 'Aucun compte avec cette adresse mail.',
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
    const verifyBox = document.querySelector('#register-verify-box');
    const verifyCodeInput = document.querySelector('#reg-verification-code');
    if (data.verification_required) {
      if (verifyBox) verifyBox.hidden = false;
      if (verifyCodeInput) verifyCodeInput.focus();
      authFeedback('register-result', 'Un code de vérification a été envoyé à votre adresse e-mail.', false);
      return;
    }
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

async function handleSubmitVerificationCode() {
  const email = document.querySelector('#reg-email')?.value.trim();
  const code = document.querySelector('#reg-verification-code')?.value.trim();
  if (!email || !code) {
    authFeedback('register-result', 'Saisissez votre e-mail et le code reçu.', true);
    return;
  }

  try {
    const data = await authFetch('/auth/verify', { email, code });
    authSaveSession(data.token, data.email, data.card_label || '');
    document.querySelector('#register-verify-box').hidden = true;
    document.querySelector('#register-dialog')?.close();
    authRenderUI();
    if (typeof loadCatalogue === 'function') loadCatalogue();
    authFeedback('register-result', 'Votre adresse e-mail a été validée.', false);
  } catch (error) {
    authFeedback('register-result', error.message, true);
  }
}

async function handleResendVerificationCode() {
  const email = document.querySelector('#reg-email')?.value.trim();
  if (!email) {
    authFeedback('register-result', 'Saisissez votre e-mail pour recevoir un nouveau code.', true);
    return;
  }
  try {
    await authFetch('/auth/resend', { email });
    authFeedback('register-result', 'Un nouveau code de vérification a été envoyé.', false);
  } catch (error) {
    authFeedback('register-result', error.message, true);
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
    if (error.code === 'ALREADY_CONNECTED') {
      const confirmed = window.confirm(
        'Ce compte est déjà connecté ailleurs. Se déconnecter de l’autre appareil et continuer ?'
      );
      if (confirmed) {
        try {
          const data = await authFetch('/auth/login', { email, password, force: true });
          authSaveSession(data.token, data.email, data.card_label || '');
          document.querySelector('#login-dialog')?.close();
          authRenderUI();
          if (typeof loadCatalogue === 'function') loadCatalogue();
        } catch (retryError) {
          authFeedback('login-result', retryError.message, true);
        }
      }
    } else {
      authFeedback('login-result', error.message, true);
    }
  } finally {
    authIdle(form, 'Connexion');
    form.querySelector('#login-password').value = '';
  }
}

async function handleSubmitForgotPassword(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const email = form.querySelector('#forgot-email').value.trim();

  authBusy(form, 'Envoi...');
  try {
    await authFetch('/auth/forgot', { email });
    document.querySelector('#forgot-request-box').hidden = true;
    document.querySelector('#forgot-reset-box').hidden = false;
    document.querySelector('#forgot-reset-email').value = email;
    document.querySelector('#forgot-code')?.focus();
    authFeedback('forgot-result', 'Si ce compte existe, un code a été envoyé par e-mail.', false);
  } catch (error) {
    authFeedback('forgot-result', error.message, true);
  } finally {
    authIdle(form, 'Envoyer le code');
  }
}

async function handleSubmitResetPassword(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const email = document.querySelector('#forgot-reset-email').value.trim();
  const code = form.querySelector('#forgot-code').value.trim();
  const password = form.querySelector('#forgot-new-password').value;

  authBusy(form, 'Validation...');
  try {
    await authFetch('/auth/reset', { email, code, password });
    document.querySelector('#forgot-password-dialog')?.close();
    document.querySelector('#forgot-request-box').hidden = false;
    document.querySelector('#forgot-reset-box').hidden = true;
    form.reset();
    authFeedback('login-result', 'Mot de passe modifié. Connectez-vous avec le nouveau.', false);
    document.querySelector('#login-dialog')?.showModal();
  } catch (error) {
    authFeedback('forgot-result', error.message, true);
  } finally {
    authIdle(form, 'Valider');
  }
}

async function handleLogout() {
  const confirmed = window.confirm('Vous voulez vraiment vous déconnecter ?');
  if (!confirmed) return;

  const state = authReadState();
  try {
    if (state.token) await authFetch('/auth/logout', {}, state.token);
  } catch {
    /* la session locale est purgée même si le serveur est injoignable */
  }
  authClearSession();
  authRenderUI();

  const reconnect = window.confirm('Se connecter avec un autre compte ?');
  if (reconnect) document.querySelector('#login-dialog')?.showModal();
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
  document.querySelector('#register-verify-button')
    ?.addEventListener('click', handleSubmitVerificationCode);
  document.querySelector('#register-resend-button')
    ?.addEventListener('click', handleResendVerificationCode);
  document.querySelector('#login-form')
    ?.addEventListener('submit', handleSubmitLogin);
  document.querySelector('#forgot-password-form')
    ?.addEventListener('submit', handleSubmitForgotPassword);
  document.querySelector('#forgot-reset-form')
    ?.addEventListener('submit', handleSubmitResetPassword);
  document.querySelector('#forgot-password-trigger')
    ?.addEventListener('click', () => {
      document.querySelector('#login-dialog')?.close();
      document.querySelector('#forgot-password-dialog')?.showModal();
    });
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
