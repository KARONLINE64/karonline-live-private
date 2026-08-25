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
const catalogueTrigger = document.querySelector('#catalogue-trigger');
const dialog = document.querySelector('#request-dialog');
const requestForm = document.querySelector('#request-form');
const resultCount = document.querySelector('#result-count');
const emptyState = document.querySelector('#empty-state');
const search = document.querySelector('#search');
let songs = [];
let isSubmitting = false;

catalogueTrigger?.addEventListener('click', () => {
  catalogueDialog?.showModal();
  search?.focus();
});

document.querySelectorAll('[data-close-dialog]').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelector(`#${button.dataset.closeDialog}`)?.close();
  });
});

async function loadCatalogue() {
  if (!songsContainer) return;
  try {
    const response = await fetch(getCatalogueUrl());
    if (!response.ok) throw new Error('Catalogue indisponible');
    songs = await response.json();
    renderSongs(songs);
  } catch (error) {
    songsContainer.innerHTML = '<p class="empty-state">Le catalogue du serveur est momentanement indisponible.</p>';
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

loadCatalogue();
