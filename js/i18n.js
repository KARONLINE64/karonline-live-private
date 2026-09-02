const KL_LANGUAGE_KEY = 'kl_language';

const KL_TRANSLATIONS = {
  fr: {
    unsubscribeTitle: 'Se désabonner',
    unsubscribeText: 'Votre compte sera désactivé et toutes vos sessions seront fermées. Cette action est réversible uniquement en contactant KaronlineLive.',
    unsubscribeConfirm: 'Confirmer le désabonnement',
    cancel: 'Annuler',
    logout: 'Se déconnecter',
    unsubscribeDone: 'Votre abonnement a été résilié. Vous êtes maintenant déconnecté.',
    unsubscribeQuestion: 'Confirmer la résiliation de votre abonnement KaronlineLive ?',
    downloadTitle: 'Espace Téléchargements',
    accountTitle: 'Se connecter',
  },
  en: {
    unsubscribeTitle: 'Cancel subscription',
    unsubscribeText: 'Your account will be deactivated and all of your sessions will be closed. This action can only be reversed by contacting KaronlineLive.',
    unsubscribeConfirm: 'Confirm cancellation',
    cancel: 'Cancel',
    logout: 'Sign out',
    unsubscribeDone: 'Your subscription has been cancelled. You are now signed out.',
    unsubscribeQuestion: 'Confirm cancellation of your KaronlineLive subscription?',
    downloadTitle: 'Downloads',
    accountTitle: 'Sign in',
  },
};

const KL_ENGLISH_TEXT = {
  'À propos': 'About',
  'Offre bundle': 'Bundle offer',
  'Essai gratuit': 'Free trial',
  'Espace Téléchargements': 'Downloads',
  'Choisissez votre chanson': 'Choose your song',
  'Rechercher un titre ou un artiste': 'Search for a song or artist',
  'Changer d’hôte': 'Change host',
  'Se déconnecter de la session': 'Leave session',
  'Demande de chanson': 'Song request',
  'Preparons votre passage': 'Let us prepare your performance',
  'Chanteur': 'Singer',
  'Artiste': 'Artist',
  'Titre': 'Title',
  'Tonalite': 'Key',
  'Envoyer': 'Send',
  'Annuler': 'Cancel',
  'Connexion': 'Sign in',
  'Se connecter': 'Sign in',
  'S’enregistrer': 'Create account',
  'Créer mon compte': 'Create my account',
  'Mot de passe': 'Password',
  'Adresse mail': 'Email address',
  'Adresse mail (identifiant)': 'Email address (username)',
  'Email et/ou mot de passe oublié ?': 'Forgot email and/or password?',
  'Récupérer mon compte': 'Recover my account',
  'Nouveau mot de passe': 'New password',
  'Valider': 'Confirm',
  'Fermer': 'Close',
  'Démarrer l’essai gratuit': 'Start free trial',
  'S’abonner maintenant': 'Subscribe now',
  'Rejoindre un hôte KaronlineBox': 'Join a KaronlineBox host',
  'Nom de session': 'Session name',
  'Participer': 'Join',
  'Karaoké pro': 'Professional karaoke',
  'Double écran': 'Dual screen',
  'Mode KJ auto': 'Auto KJ mode',
  'Solo ou groupe': 'Solo or group',
};

function klLanguage() {
  return localStorage.getItem(KL_LANGUAGE_KEY) === 'en' ? 'en' : 'fr';
}

function klText(key) {
  return KL_TRANSLATIONS[klLanguage()][key] || KL_TRANSLATIONS.fr[key] || key;
}

function applyKaronlineLanguage() {
  const language = klLanguage();
  document.documentElement.lang = language;
  document.querySelectorAll('[data-i18n]').forEach((element) => {
    const text = klText(element.dataset.i18n);
    if (text) element.textContent = text;
  });
  document.querySelectorAll('[data-language]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.language === language));
  });
  document.querySelectorAll('[data-logout]').forEach((button) => {
    button.textContent = klText('logout');
    button.setAttribute('aria-label', klText('logout'));
  });
  document.querySelectorAll('[data-kl-original]').forEach((element) => {
    element.textContent = language === 'en'
      ? (KL_ENGLISH_TEXT[element.dataset.klOriginal] || element.dataset.klOriginal)
      : element.dataset.klOriginal;
  });
  document.querySelectorAll('button, label, h1, h2, h3, strong').forEach((element) => {
    if (element.dataset.i18n || element.children.length || element.dataset.klOriginal) return;
    const text = element.textContent.trim();
    if (!text || !KL_ENGLISH_TEXT[text]) return;
    element.dataset.klOriginal = text;
    element.textContent = language === 'en' ? KL_ENGLISH_TEXT[text] : text;
  });
}

document.querySelectorAll('[data-language]').forEach((button) => {
  button.addEventListener('click', () => {
    localStorage.setItem(KL_LANGUAGE_KEY, button.dataset.language);
    applyKaronlineLanguage();
    window.dispatchEvent(new Event('karonline-language-changed'));
  });
});

applyKaronlineLanguage();
