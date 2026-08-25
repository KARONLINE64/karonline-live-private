// Configuration centralisée pour le serveur LAN de test
// À adapter ultérieurement pour l'API réelle de KaronlineLive
const CONFIG = {
  SERVER_URL: 'http://192.168.129.0:8765',
  ENDPOINTS: {
    CATALOGUE: '/catalogue',
    REQUEST_DEMAND: '/request-demand'
  }
};

// Fonction helper pour construire l'URL complète
function getRequestDemandUrl() {
  return `${CONFIG.SERVER_URL}${CONFIG.ENDPOINTS.REQUEST_DEMAND}`;
}

function getCatalogueUrl() {
  return `${CONFIG.SERVER_URL}${CONFIG.ENDPOINTS.CATALOGUE}`;
}
