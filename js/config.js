// Configuration KaronlineLive - chaque visiteur se connecte a l'hote (KaronlineBox)
// de son choix : plusieurs hotes peuvent tourner en meme temps, chacun chez soi.
const HOST_URL_KEY = 'kl_host_url';

function isLanHostname(hostname) {
  return hostname === 'localhost' ||
         hostname.startsWith('192.168.') ||
         hostname.startsWith('10.') ||
         hostname.startsWith('172.');
}

// URL du serveur de l'hote actuellement connecte (null si aucun configure)
function getHostServerUrl() {
  if (isLanHostname(window.location.hostname)) {
    return `http://${window.location.hostname}:8765`;
  }
  return localStorage.getItem(HOST_URL_KEY);
}

// Enregistre l'hote choisi par ce visiteur (ex: xxxx.trycloudflare.com)
function setHostServerUrl(input) {
  const clean = input.trim().replace(/\/+$/, '');
  if (!clean) return;
  const withScheme = /^https?:\/\//i.test(clean) ? clean : `https://${clean}`;
  localStorage.setItem(HOST_URL_KEY, withScheme);
}

function clearHostServerUrl() {
  localStorage.removeItem(HOST_URL_KEY);
}

// Resout un nom de session simple (ex: soiree-marc) via l'annuaire central,
// ou accepte directement une URL/adresse si l'utilisateur en colle une.
async function connectToHost(input) {
  const clean = input.trim().replace(/\/+$/, '');
  if (!clean) return { ok: false, error: 'EMPTY' };

  // Une adresse contenant un point ou "://" est traitee comme une URL directe.
  if (/[:.]/.test(clean)) {
    setHostServerUrl(clean);
    return { ok: true };
  }

  // Sinon, c'est un nom de session a resoudre via l'annuaire central.
  try {
    const response = await fetch(`https://api.karonlinelive.com/session/${encodeURIComponent(clean.toLowerCase())}`);
    if (!response.ok) {
      return { ok: false, error: 'SESSION NOT FOUND' };
    }
    const data = await response.json();
    if (!data.host_url) {
      return { ok: false, error: 'SESSION NOT FOUND' };
    }
    setHostServerUrl(data.host_url);
    return { ok: true };
  } catch {
    return { ok: false, error: 'NETWORK' };
  }
}

const CONFIG = {
  ENDPOINTS: {
    CATALOGUE: '/catalogue',
    REQUEST_DEMAND: '/request-demand',
    DOWNLOAD: '/download/karonlinebox'
  },
  
  // Timeout pour détection serveur (ms)
  LAN_TIMEOUT: 3000
};

// Détecte si on est sur réseau local privé
function isPrivateNetwork() {
  return true; // un hote (LAN ou tunnel) est toujours joignable une fois configure
}

// Ping le serveur de l'hote connecte pour vérifier sa disponibilité.
async function checkLanServerAvailability() {
  const url = getHostServerUrl();
  if (!url) return false;
  try {
    const response = await Promise.race([
      fetch(`${url}/catalogue`, { method: 'GET' }),
      new Promise((_, reject) => 
        setTimeout(() => reject(new Error('LAN timeout')), CONFIG.LAN_TIMEOUT)
      )
    ]);
    return response.ok;
  } catch {
    return false;
  }
}

// Determine le serveur à utiliser
async function getActiveServerUrl() {
  return getHostServerUrl();
}

// Helpers pour construire URLs
function getRequestDemandUrl() {
  return `${getHostServerUrl()}${CONFIG.ENDPOINTS.REQUEST_DEMAND}`;
}

function getCatalogueUrl() {
  return `${getHostServerUrl()}${CONFIG.ENDPOINTS.CATALOGUE}`;
}

function getKaronlineBoxDownloadUrl() {
  // Le programme d'installation doit rester disponible meme si le tunnel API est coupe.
  return 'downloads/KaronlineBox_V90_Setup_20260826_LOGO.exe';
}
