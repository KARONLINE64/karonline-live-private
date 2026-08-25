// Configuration KaronlineLive - Support LAN local + tunnel public

// URL publique HTTPS du serveur LAN, exposee via Cloudflare Tunnel (cloudflared)
// depuis le PC fixe. Fonctionne depuis n'importe quel reseau, sans Tailscale.
const LAN_TUNNEL_URL = 'https://api.karonlinelive.com';

function isLanHostname(hostname) {
  return hostname === 'localhost' ||
         hostname.startsWith('192.168.') ||
         hostname.startsWith('10.') ||
         hostname.startsWith('172.');
}

const CONFIG = {
  // Serveur LAN direct en dev local, sinon le tunnel public (port 8765 en local uniquement)
  LAN_SERVER_URL: isLanHostname(window.location.hostname) ? `http://${window.location.hostname}:8765` : LAN_TUNNEL_URL,
  
  // Fallback: API cloud (a implementer future)
  CLOUD_API_URL: 'https://api.karonlinelive.com',
  
  ENDPOINTS: {
    CATALOGUE: '/catalogue',
    REQUEST_DEMAND: '/request-demand',
    DOWNLOAD: '/download/karonlinebox'
  },
  
  // Timeout pour détection serveur LAN (ms)
  LAN_TIMEOUT: 3000
};

// Détecte si on est sur réseau local privé
function isPrivateNetwork() {
  return true; // le tunnel public rend le serveur toujours joignable
}

// Ping le serveur (LAN direct ou tunnel public) pour vérifier sa disponibilité.
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
