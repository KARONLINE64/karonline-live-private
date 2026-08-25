// Configuration KaronlineLive - Support LAN local + API cloud
const CONFIG = {
  // Serveur LAN sur le réseau local (port 8765)
  LAN_SERVER_URL: `http://${window.location.hostname === 'localhost' ? 'localhost' : window.location.hostname}:8765`,
  
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
  const hostname = window.location.hostname;
  return hostname === 'localhost' || 
         hostname.startsWith('192.168.') || 
         hostname.startsWith('10.') ||
         hostname.startsWith('172.') ||
         /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(hostname);
}

// Ping le serveur LAN pour vérifier sa disponibilité
async function checkLanServerAvailability() {
  if (!isPrivateNetwork()) return false;
  
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
  if (isPrivateNetwork() && await checkLanServerAvailability()) {
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
