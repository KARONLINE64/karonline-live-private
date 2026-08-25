// Configuration KaronlineLive - Support LAN local + API cloud

// IP Tailscale du PC fixe hebergeant lan_server.py (utilisee quand le site est
// charge depuis le domaine public karonlinelive.com, ou window.location.hostname
// ne correspond pas a la machine LAN).
const LAN_TAILSCALE_IP = '100.87.153.104';

function isLanHostname(hostname) {
  return hostname === 'localhost' ||
         hostname.startsWith('192.168.') ||
         hostname.startsWith('10.') ||
         hostname.startsWith('172.') ||
         /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(hostname);
}

const CONFIG = {
  // Serveur LAN sur le réseau local ou via Tailscale (port 8765)
  LAN_SERVER_URL: `http://${isLanHostname(window.location.hostname) ? window.location.hostname : LAN_TAILSCALE_IP}:8765`,
  
  // Fallback: API cloud (à implémenter future)
  CLOUD_API_URL: 'https://api.karonlinelive.com',
  
  ENDPOINTS: {
    CATALOGUE: '/catalogue',
    REQUEST_DEMAND: '/request-demand',
    DOWNLOAD: '/download/karonlinebox'
  },
  
  // Timeout pour détection serveur LAN (ms)
  LAN_TIMEOUT: 3000
};

// Détecte si on est sur réseau local privé (inclut le CGNAT Tailscale 100.64.0.0/10)
function isPrivateNetwork() {
  return isLanHostname(window.location.hostname);
}

// Ping le serveur LAN pour vérifier sa disponibilité.
// Toujours tenté (même depuis karonlinelive.com) : le visiteur peut joindre
// la machine via son IP Tailscale meme si la page vient d'un domaine public.
async function checkLanServerAvailability() {
  try {
    const response = await Promise.race([
      fetch(`${CONFIG.LAN_SERVER_URL}/catalogue`, { method: 'GET' }),
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
  if (await checkLanServerAvailability()) {
    return CONFIG.LAN_SERVER_URL;
  }
  // Fallback future vers cloud API
  return CONFIG.CLOUD_API_URL;
}

// Helpers pour construire URLs
function getRequestDemandUrl() {
  return `${CONFIG.LAN_SERVER_URL}${CONFIG.ENDPOINTS.REQUEST_DEMAND}`;
}

function getCatalogueUrl() {
  return `${CONFIG.LAN_SERVER_URL}${CONFIG.ENDPOINTS.CATALOGUE}`;
}

function getKaronlineBoxDownloadUrl() {
  return `${CONFIG.LAN_SERVER_URL}${CONFIG.ENDPOINTS.DOWNLOAD}`;
}
