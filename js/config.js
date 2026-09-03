// Configuration KaronlineLive - chaque visiteur se connecte a l'hote (KaronlineBox)
// de son choix : plusieurs hotes peuvent tourner en meme temps, chacun chez soi.
const HOST_URL_KEY = 'kl_host_url';
// Mode relais (recommande, sans Cloudflare) : le KJ n'expose aucune URL/tunnel,
// KaronlineBox tire ses jobs en sortant uniquement. On stocke alors le NOM de
// session, et le catalogue/les demandes passent par le serveur central.
const RELAY_SESSION_KEY = 'kl_relay_session';
const CENTRAL_API_BASE = (() => {
  const host = window.location.hostname;
  if (host === 'localhost' || host === '127.0.0.1' || host === '::1') {
    return 'http://localhost:8765';
  }
  return 'https://api.karonlinelive.com';
})();

function isLanHostname(hostname) {
  return hostname === 'localhost' ||
         hostname.startsWith('192.168.') ||
         hostname.startsWith('10.') ||
         hostname.startsWith('172.');
}

// URL du serveur de l'hote actuellement connecte (null si aucun configure,
// ou si la session en cours est en mode relais - voir getRelaySessionName()).
function getHostServerUrl() {
  if (isLanHostname(window.location.hostname)) {
    return `http://${window.location.hostname}:8765`;
  }
  return localStorage.getItem(HOST_URL_KEY);
}

// Nom de session en mode relais (sans host_url), ou null si non connecte /
// si on utilise le mode legacy (host_url direct).
function getRelaySessionName() {
  return localStorage.getItem(RELAY_SESSION_KEY);
}

// Vrai si un hote (legacy host_url ou session relais) est configure.
function isHostConnected() {
  return Boolean(getHostServerUrl() || getRelaySessionName());
}

// Enregistre l'hote choisi par ce visiteur (ex: xxxx.trycloudflare.com)
function setHostServerUrl(input) {
  const clean = input.trim().replace(/\/+$/, '');
  if (!clean) return;
  const withScheme = /^https?:\/\//i.test(clean) ? clean : `https://${clean}`;
  localStorage.removeItem(RELAY_SESSION_KEY);
  localStorage.setItem(HOST_URL_KEY, withScheme);
}

// Enregistre le nom de session en mode relais.
function setRelaySessionName(name) {
  localStorage.removeItem(HOST_URL_KEY);
  localStorage.setItem(RELAY_SESSION_KEY, name);
}

function clearHostServerUrl() {
  localStorage.removeItem(HOST_URL_KEY);
  localStorage.removeItem(RELAY_SESSION_KEY);
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
    const response = await fetch(`${CENTRAL_API_BASE}/session/${encodeURIComponent(clean.toLowerCase())}`);
    if (!response.ok) {
      return { ok: false, error: 'SESSION NOT FOUND' };
    }
    const data = await response.json();
    if (data.host_url) {
      setHostServerUrl(data.host_url);
      return { ok: true };
    }
    if (data.relay) {
      setRelaySessionName(clean.toLowerCase());
      return { ok: true };
    }
    return { ok: false, error: 'SESSION NOT FOUND' };
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
  const relayName = getRelaySessionName();
  if (relayName) {
    try {
      const response = await Promise.race([
        fetch(`${CENTRAL_API_BASE}/session/${encodeURIComponent(relayName)}`, { method: 'GET' }),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error('LAN timeout')), CONFIG.LAN_TIMEOUT)
        )
      ]);
      if (response.status === 404) {
        clearHostServerUrl();
      }
      return response.ok;
    } catch {
      return false;
    }
  }
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
  const relayName = getRelaySessionName();
  if (relayName) {
    return `${CENTRAL_API_BASE}/session/${encodeURIComponent(relayName)}/request-demand`;
  }
  return `${getHostServerUrl()}${CONFIG.ENDPOINTS.REQUEST_DEMAND}`;
}

function getCatalogueUrl() {
  const relayName = getRelaySessionName();
  if (relayName) {
    return `${CENTRAL_API_BASE}/session/${encodeURIComponent(relayName)}/catalogue`;
  }
  return `${getHostServerUrl()}${CONFIG.ENDPOINTS.CATALOGUE}`;
}

function getKaronlineBoxDownloadUrl() {
  // ZIP : les navigateurs bloquent le telechargement direct d'un .exe non signe,
  // mais laissent passer une archive .zip (ouvrable nativement par Windows).
  return 'downloads/KaronlineBox_Setup.zip';
}
