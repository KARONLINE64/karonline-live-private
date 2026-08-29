const menuToggle = document.querySelector('.menu-toggle');
const mainNav = document.querySelector('.main-nav');

menuToggle?.addEventListener('click', () => {
  const isOpen = mainNav.classList.toggle('is-open');
  menuToggle.setAttribute('aria-expanded', String(isOpen));
});

document.querySelectorAll('.main-nav a').forEach((link) => {
  link.addEventListener('click', () => mainNav?.classList.remove('is-open'));
});

const songsContainer = document.querySelector('#songs');
const catalogueDialog = document.querySelector('#catalogue-dialog');
const catalogueTriggers = document.querySelectorAll('#catalogue-trigger, #catalogue-trigger-mobile');
const dialog = document.querySelector('#request-dialog');
const requestForm = document.querySelector('#request-form');
const resultCount = document.querySelector('#result-count');
const emptyState = document.querySelector('#empty-state');
const search = document.querySelector('#search');
const downloadTriggers = document.querySelectorAll('#download-trigger, #download-trigger-mobile');
const downloadStatuses = document.querySelectorAll('#download-status, #download-status-mobile');
const hostDialog = document.querySelector('#host-connect-dialog');
const hostForm = document.querySelector('#host-connect-form');
const hostInput = document.querySelector('#host-url-input');
const hostStatuses = document.querySelectorAll('.host-status');
const hostChangeButtons = document.querySelectorAll('[data-change-host]');
let songs = [];
let isSubmitting = false;
let isDownloading = false;
let isMobileParticipant = false; // Mode mobile: participant dans une session KJ, pas compte KJ
let isDesktopCatalogueRequested = false; // Mode desktop: vrai seulement après clic explicite sur Catalogue

function refreshHostStatus() {
  const relayName = getRelaySessionName();
  const url = getHostServerUrl();
  hostStatuses.forEach((el) => {
    if (relayName) {
      el.textContent = `Connecté à la session : ${relayName}`;
    } else if (url) {
      el.textContent = `Connecté à : ${url.replace(/^https?:\/\//, '')}`;
    } else {
      el.textContent = 'Non connecté à un hôte';
    }
  });
}

// Renvoie true si un hote est deja configure, sinon ouvre le dialog de connexion et renvoie false
function ensureHostConnected() {
  if (isHostConnected()) return true;
  hostDialog?.showModal();
  return false;
}

// Renvoie true si un compte KJ est connecte, sinon ouvre le dialog de connexion
function ensureAuthenticated() {
  const token = localStorage.getItem('kl_auth_token');
  const email = localStorage.getItem('kl_auth_email');

  if (token && email) return true;
  document.querySelector('#login-dialog')?.showModal();
  return false;
}

hostForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const submitBtn = hostForm.querySelector('button[type="submit"]');
  const errorEl = hostForm.querySelector('.host-connect-error');
  if (errorEl) errorEl.textContent = '';
  submitBtn.disabled = true;
  submitBtn.textContent = 'Connexion...';

  const result = await connectToHost(hostInput.value);

  submitBtn.disabled = false;
  submitBtn.textContent = 'Se connecter';

  if (!result.ok) {
    if (errorEl) {
      errorEl.textContent = result.error === 'SESSION NOT FOUND'
        ? '❌ Nom de session introuvable. Vérifiez avec votre animateur.'
        : '❌ Connexion impossible. Réessayez.';
    }
    return;
  }

  hostInput.value = '';
  refreshHostStatus();
  hostDialog?.close();
  catalogueDialog?.showModal();
  search?.focus();
  loadCatalogue();
});

hostChangeButtons.forEach((button) => button.addEventListener('click', () => {
  hostDialog?.showModal();
}));

refreshHostStatus();

// Desktop: catalogue requiert SEULEMENT l'authentification KJ (pas de nom de session hôte)
const catalogueTriggerDesktop = document.querySelector('#catalogue-trigger');
if (catalogueTriggerDesktop) {
  catalogueTriggerDesktop.addEventListener('click', () => {
    if (!ensureAuthenticated()) return;
    isDesktopCatalogueRequested = true;
    catalogueDialog?.showModal();
    search?.focus();
    loadCatalogue();
  });
}

// Mobile: "Participer" demande TOUJOURS le nom de session KJ/hôte, à chaque
// clic, jamais d'email/password. Aucun raccourci meme si deja connecte avant.
const catalogueTriggerMobile = document.querySelector('#catalogue-trigger-mobile');
if (catalogueTriggerMobile) {
  catalogueTriggerMobile.addEventListener('click', () => {
    isMobileParticipant = true;
    hostDialog?.showModal();
  });
}

function setDownloadStatus(text, state) {
  downloadStatuses.forEach((el) => {
    el.textContent = text;
    el.classList.toggle('visible', Boolean(text));
    el.classList.remove('error', 'success');
    if (state) el.classList.add(state);
  });
}

downloadTriggers.forEach((trigger) => trigger.addEventListener('click', async () => {
  if (isDownloading) return;
  
  isDownloading = true;
  downloadTriggers.forEach((t) => t.disabled = true);
  setDownloadStatus('⏳ Téléchargement en cours...');
  
  try {
    const downloadUrl = getKaronlineBoxDownloadUrl();
    
    // Créer un lien invisible et cliquer dessus pour télécharger
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = 'KaronlineBox_Setup.exe';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    
    setDownloadStatus('✅ Téléchargement lancé! Vérifiez votre dossier Téléchargements.', 'success');
    
    setTimeout(() => {
      isDownloading = false;
      downloadTriggers.forEach((t) => t.disabled = false);
      downloadStatuses.forEach((el) => el.classList.remove('visible', 'success'));
    }, 3000);
  } catch (error) {
    console.error('Erreur téléchargement:', error);
    setDownloadStatus('❌ Erreur lors du téléchargement. Réessayez.', 'error');
    isDownloading = false;
    downloadTriggers.forEach((t) => t.disabled = false);
  }
}));

document.querySelectorAll('[data-close-dialog]').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelector(`#${button.dataset.closeDialog}`)?.close();
  });
});

async function loadCatalogue() {
  if (!songsContainer) return;

  if (!isMobileParticipant && !localStorage.getItem('kl_auth_token')) {
    songsContainer.innerHTML = '<p class="empty-state">🔒 Connectez-vous à votre compte KJ pour accéder au catalogue.</p>';
    return;
  }

  // Desktop KJ : le catalogue est celui de sa propre KaronlineBox locale,
  // jamais une session d'un autre hôte a rejoindre. Ne s'applique qu'après
  // un clic explicite sur le bouton Catalogue desktop (jamais au chargement
  // initial de la page, pour ne pas polluer le flux mobile "Participer").
  if (isDesktopCatalogueRequested && !isMobileParticipant && !isHostConnected()) {
    setHostServerUrl('http://localhost:8765');
    refreshHostStatus();
  }

  if (!isHostConnected()) {
    songsContainer.innerHTML = '<p class="empty-state">🔌 Connectez-vous à un hôte KaronlineBox pour voir son catalogue.</p>';
    return;
  }
  try {
    const isAvailable = await checkLanServerAvailability();
    if (!isAvailable) {
      songsContainer.innerHTML = '<p class="empty-state">⚠️ Serveur indisponible. Vérifiez que KaronlineBox et le tunnel de votre hôte sont actifs.</p>';
      return;
    }
    const response = await fetch(getCatalogueUrl());
    if (!response.ok) throw new Error('Catalogue indisponible');
    songs = await response.json();
    renderSongs(songs);
  } catch (error) {
    console.error('Erreur chargement catalogue:', error);
    songsContainer.innerHTML = '<p class="empty-state">⚠️ Serveur LAN indisponible. Assurez-vous que le serveur est lancé: python lan_server.py --port 8765</p>';
  }
}

function renderSongs(items) {
  songsContainer.innerHTML = items.map((song, index) => `
    <button class="song-row" type="button" data-index="${songs.indexOf(song)}">
      <span class="song-number">${String(index + 1).padStart(2, '0')}</span>
      <span class="song-meta"><strong>${escapeHtml(song.title)}</strong><small>${escapeHtml(song.artist)}</small></span>
      <span class="song-arrow" aria-hidden="true">→</span>
    </button>`).join('');
  resultCount.textContent = `${items.length} titre${items.length > 1 ? 's' : ''}`;
  emptyState.hidden = items.length > 0;
  songsContainer.querySelectorAll('.song-row').forEach((row) => {
    row.addEventListener('click', () => openRequest(songs[Number(row.dataset.index)]));
  });
}

function openRequest(song) {
  requestForm.reset();
  requestForm.artist.value = song.artist;
  requestForm.title.value = song.title;
  requestForm.querySelector('#request-result').textContent = '';
  catalogueDialog?.close();
  dialog.showModal();
  requestForm.singer.focus();
}

requestForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  
  // Empêcher les doubles clics
  if (isSubmitting) return;
  
  const output = requestForm.querySelector('#request-result');
  const submitButton = requestForm.querySelector('.submit-button');
  
  // Récupérer les données du formulaire
  const singer = requestForm.singer.value.trim();
  const artist = requestForm.artist.value.trim();
  const title = requestForm.title.value.trim();
  const key = Number(requestForm.key.value);
  
  // Validation
  if (!singer || !artist || !title || !Number.isInteger(key) || key < -6 || key > 6) {
    output.textContent = 'Erreur : renseignez le chanteur, l’artiste et une tonalite de -6 a +6.';
    output.classList.add('is-visible');
    return;
  }
  
  // Construire la demande
  const request = {
    singer: singer,
    artist: artist,
    title: title,
    key: key
  };
  
  isSubmitting = true;
  submitButton.disabled = true;
  output.textContent = 'Envoi de votre demande...';
  output.classList.add('is-visible');
  
  try {
    const lanAvailable = await checkLanServerAvailability();
    if (!lanAvailable) {
      output.textContent = '❌ Serveur LAN indisponible. Vérifiez que le serveur est lancé.';
      output.classList.add('is-visible');
      submitButton.disabled = false;
      isSubmitting = false;
      return;
    }
    
    const response = await fetch(getRequestDemandUrl(), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(request)
    });
    
    if (response.ok) {
      output.textContent = 'Demande reçue ! Merci, vous êtes enregistré.';
      output.classList.add('is-visible');
      // Permettre à l'utilisateur de fermer le popup après succès
      setTimeout(() => {
        submitButton.disabled = false;
        isSubmitting = false;
      }, 2000);
    } else if (response.status === 409) {
      output.textContent = '❌ KaronlineBox non actif. Lancez l\'application d\'abord.';
      output.classList.add('is-visible');
      submitButton.disabled = false;
      isSubmitting = false;
    } else {
      output.textContent = `Erreur serveur (${response.status}). Veuillez réessayer.`;
      output.classList.add('is-visible');
      submitButton.disabled = false;
      isSubmitting = false;
    }
  } catch (error) {
    console.error('Erreur lors de l\'envoi :', error);
    output.textContent = 'Le serveur est inaccessible. Vérifiez votre connexion réseau.';
    output.classList.add('is-visible');
    submitButton.disabled = false;
    isSubmitting = false;
  }
});

search?.addEventListener('input', () => {
  const query = search.value.trim().toLocaleLowerCase();
  renderSongs(songs.filter((song) => `${song.title} ${song.artist}`.toLocaleLowerCase().includes(query)));
});

function escapeHtml(value) {
  return value.replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#039;', '"': '&quot;' })[character]);
}

// Page catalogue.html autonome : auth d'abord, puis hôte si nécessaire.
// Sur index.html (catalogueDialog présent), rien à charger au chargement de
// la page : le catalogue ne se charge que sur clic explicite (desktop
// "Catalogue" ou mobile "Participer"), jamais automatiquement.
if (!catalogueDialog && songsContainer) {
  if (!isMobileParticipant && !localStorage.getItem('kl_auth_token')) {
    document.querySelector('#login-dialog')?.showModal();
  } else if (!isHostConnected()) {
    hostDialog?.showModal();
  } else {
    loadCatalogue();
  }
}
