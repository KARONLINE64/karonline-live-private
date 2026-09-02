const KL_LANGUAGE_KEY = 'kl_language';

const KL_TRANSLATIONS = {
  fr: {
    aboutTitle: 'À propos',
    aboutLead: 'KaronlineLive est la solution moderne de karaoké et animation en ligne pour soirées, événements, établissements et passionnés.',
    aboutBody: 'Profitez de notre catalogue interactif, gérez vos sessions en direct avec KaronlineBox et créez des moments d’animation inoubliables.',
    bundleTitle: 'Offre bundle',
    bundleLead: 'Découvrez nos formules combinées : système d’animation complet, gestion multi-écrans et accès illimité au catalogue de karaoké.',
    bundleBody: 'Idéal pour les animateurs pro et les établissements souhaitant une solution intégrée clé en main.',
    trialTitle: 'Essai gratuit',
    trialLead: 'Testez KaronlineLive gratuitement et explorez toutes les fonctionnalités de notre application karaoké !',
    trialBody: 'Création de compte instantanée sans engagement et sans carte bancaire requise.',
    startTrial: 'Démarrer l’essai gratuit',
    subscribeNow: 'S’abonner maintenant',
    close: 'Fermer',
    downloadsTitle: 'Espace Téléchargements',
    downloadsVersion: 'Version officielle Windows - Build du 01-09-2026',
    downloadsFile: 'Fichier : karonlinebox_setup.exe (~44,3 Mo)',
    downloadsUpcoming: 'Utilitaires audio et plugins d’animation à venir :',
    downloadsAudio: '🎧 Outils de traitement et effets audio (Bientôt)',
    downloadsSongbook: '📄 Générateur de Songbook et listes PDF (Bientôt)',
    downloadsAsio: '🔊 Drivers ASIO et configuration multi-sorties (Bientôt)',
    downloadsUtilities: '🎵 Convertisseurs et utilitaires karaoké (Bientôt)',
    subscribeTitle: 'S’enregistrer',
    emailUsername: 'Adresse mail (identifiant)',
    password: 'Mot de passe',
    cardOptional: 'Carte bancaire (optionnel)',
    subscribeNote: 'Vos futures sessions KaronlineBox seront rattachées à ce compte pour la facturation. Phase tests amis/famille : aucun prélèvement, seuls les 4 derniers chiffres sont mémorisés.',
    createAccount: 'Créer mon compte',
    licenseTitle: 'CONDITIONS D’UTILISATION & LICENCE KARONLINEBOX',
    licenseVersion: 'Version 1.0',
    termsRead: 'J’ai lu les Conditions d’utilisation.',
    licenseAccept: 'J’accepte la Licence d’utilisation de KaronlineBox.',
    downloadConfirm: 'Télécharger KaronlineBox',
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
    aboutTitle: 'About Us',
    aboutLead: 'KaronlineLive is the modern online karaoke and entertainment solution for parties, events, venues, and enthusiasts.',
    aboutBody: 'Enjoy our interactive catalogue, manage your live sessions with KaronlineBox, and create unforgettable entertainment moments.',
    bundleTitle: 'Bundle Offer',
    bundleLead: 'Discover our combined packages: a complete entertainment system, multi-screen management, and unlimited access to the karaoke catalogue.',
    bundleBody: 'Ideal for professional hosts and venues looking for an all-in-one integrated solution.',
    trialTitle: 'Free Trial',
    trialLead: 'Try KaronlineLive for free and explore every feature of our karaoke application!',
    trialBody: 'Create an account instantly with no commitment and no bank card required.',
    startTrial: 'Start free trial',
    subscribeNow: 'Subscribe now',
    close: 'Close',
    downloadsTitle: 'Downloads',
    downloadsVersion: 'Official Windows version - Build dated 01-09-2026',
    downloadsFile: 'File: karonlinebox_setup.exe (~44.3 MB)',
    downloadsUpcoming: 'Upcoming audio tools and entertainment plugins:',
    downloadsAudio: '🎧 Audio processing and effects tools (Coming soon)',
    downloadsSongbook: '📄 Songbook and PDF list generator (Coming soon)',
    downloadsAsio: '🔊 ASIO drivers and multi-output configuration (Coming soon)',
    downloadsUtilities: '🎵 Karaoke converters and utilities (Coming soon)',
    subscribeTitle: 'Create account',
    emailUsername: 'Email address (username)',
    password: 'Password',
    cardOptional: 'Bank card (optional)',
    subscribeNote: 'Your future KaronlineBox sessions will be linked to this account for billing. Friends and family test phase: no charge is made; only the last four digits are stored.',
    createAccount: 'Create my account',
    licenseTitle: 'TERMS OF USE & KARONLINEBOX LICENSE',
    licenseVersion: 'Version 1.0',
    termsRead: 'I have read the Terms of Use.',
    licenseAccept: 'I accept the KaronlineBox User License Agreement.',
    downloadConfirm: 'Download KaronlineBox',
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

const KL_ARIA_LABELS = {
  fr: {
    about: 'À propos de KaronlineLive',
    catalogue: 'Ouvrir le catalogue de chansons',
    bundle: 'Découvrir l’offre bundle',
    subscribe: 'S’abonner',
    signIn: 'Se connecter',
    downloads: 'Ouvrir les téléchargements',
    trial: 'Ouvrir l’essai gratuit',
    language: 'Passer le site en anglais',
  },
  en: {
    about: 'About KaronlineLive',
    catalogue: 'Open the song catalogue',
    bundle: 'Discover the bundle offer',
    subscribe: 'Subscribe',
    signIn: 'Sign in',
    downloads: 'Open downloads',
    trial: 'Open the free trial',
    language: 'Switch site to French',
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
  const savedLanguage = localStorage.getItem(KL_LANGUAGE_KEY);
  return savedLanguage === 'en' ? 'en' : 'fr';
}

function klText(key) {
  return KL_TRANSLATIONS[klLanguage()][key] || KL_TRANSLATIONS.fr[key] || key;
}

function applyKaronlineLanguage() {
  const language = klLanguage();
  document.documentElement.lang = language;
  document.title = language === 'en' ? 'KaronlineLive - Karaoke & Entertainment' : 'KaronlineLive';
  const homeVisual = document.querySelector('#home-visual');
  if (homeVisual) {
    homeVisual.src = `assets/ACCUEIL_${language === 'en' ? 'EN' : 'FR'}.png`;
    homeVisual.alt = language === 'en'
      ? 'KaronlineLive, karaoke and entertainment software'
      : 'KaronlineLive, logiciel de karaoké et animation';
  }
  document.querySelector('#terms-fr')?.toggleAttribute('hidden', language === 'en');
  document.querySelector('#terms-en')?.toggleAttribute('hidden', language !== 'en');
  document.querySelectorAll('[data-i18n]').forEach((element) => {
    const text = klText(element.dataset.i18n);
    if (text) element.textContent = text;
  });
  document.querySelectorAll('[data-i18n-aria]').forEach((element) => {
    element.setAttribute('aria-label', KL_ARIA_LABELS[language][element.dataset.i18nAria]);
  });
  const globeButton = document.querySelector('[data-language-toggle]');
  if (globeButton) {
    const label = KL_ARIA_LABELS[language].language;
    globeButton.setAttribute('aria-label', label);
    globeButton.title = label;
  }
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

document.querySelectorAll('[data-language-toggle]').forEach((button) => {
  button.addEventListener('click', () => {
    localStorage.setItem(KL_LANGUAGE_KEY, klLanguage() === 'fr' ? 'en' : 'fr');
    applyKaronlineLanguage();
    window.dispatchEvent(new Event('karonline-language-changed'));
  });
});

applyKaronlineLanguage();
