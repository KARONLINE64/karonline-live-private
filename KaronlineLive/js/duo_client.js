// Client WebRTC & Session DUO pour KaronlineBox Duo (Mobile & Web Guest)

(function () {
  const urlParams = new URLSearchParams(window.location.search);
  const code = urlParams.get('code') || 'DUO-DEMO';

  const joinBtn = document.getElementById('join-btn');
  const permissionScreen = document.getElementById('permission-screen');
  const localVideo = document.getElementById('local-video');
  const remoteVideo = document.getElementById('remote-video');
  const hostPlaceholder = document.getElementById('host-placeholder');
  const micToggle = document.getElementById('mic-toggle');
  const camToggle = document.getElementById('cam-toggle');
  const leaveBtn = document.getElementById('leave-btn');

  const pipSinger = document.getElementById('pip-singer');
  const pipSongTitle = document.getElementById('pip-song-title');
  const pipLyrics = document.getElementById('pip-lyrics');
  const pipProgressBar = document.getElementById('pip-progress-bar');

  let localStream = null;
  let peerConnection = null;
  let isMicOn = true;
  let isCamOn = true;
  let syncInterval = null;
  let frameInterval = null;

  const CENTRAL_API = typeof CENTRAL_API_BASE !== 'undefined' ? CENTRAL_API_BASE : 'https://api.karonlinelive.com';

  joinBtn?.addEventListener('click', async () => {
    try {
      localStream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
        audio: true
      });

      if (localVideo) {
        localVideo.srcObject = localStream;
      }

      permissionScreen.style.display = 'none';
      startDuoSession();
    } catch (err) {
      console.error('Erreur accès caméra/micro:', err);
      alert('🔒 L’accès à la caméra et au microphone est nécessaire pour chanter en DUO.');
    }
  });

  function startDuoSession() {
    registerGuestSession();
    startSyncPolling();
    startFrameSending();
  }

  function startFrameSending() {
    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 240;
    const ctx = canvas.getContext('2d');

    frameInterval = setInterval(() => {
      if (!localStream || !localVideo || !isCamOn) return;
      try {
        ctx.drawImage(localVideo, 0, 0, 320, 240);
        const frameData = canvas.toDataURL('image/jpeg', 0.45);
        fetch(`${CENTRAL_API}/duo/frame`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: code, role: 'guest', frame: frameData })
        }).catch(() => {});
      } catch (e) {}
    }, 350);
  }

  async function registerGuestSession() {
    try {
      await fetch(`${CENTRAL_API}/duo/join`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code, guest_name: 'Invité Mobile' })
      });
    } catch (e) {
      console.warn('Signalement rejointure:', e);
    }
  }

  function startSyncPolling() {
    syncInterval = setInterval(async () => {
      try {
        const res = await fetch(`${CENTRAL_API}/duo/status?code=${code}`);
        if (res.ok) {
          const data = await res.json();
          if (data.sync) {
            updateSyncUI(data.sync);
          }
        }
      } catch (e) {
        /* polling silencieux */
      }
    }, 500);
  }

  function updateSyncUI(sync) {
    if (hostPlaceholder) hostPlaceholder.style.display = 'none';

    if (pipSinger && sync.singer) {
      pipSinger.textContent = `Chanteur : ${sync.singer}`;
    }
    if (pipSongTitle && sync.song) {
      pipSongTitle.textContent = sync.song;
    }
    if (pipProgressBar && sync.duration_ms > 0) {
      const pct = Math.min(100, Math.max(0, (sync.position_ms / sync.duration_ms) * 100));
      pipProgressBar.style.width = `${pct}%`;
    }
    if (pipLyrics) {
      pipLyrics.textContent = sync.is_playing ? '🎤 Chantez ! (Synchro Hôte)' : '⏸ Pause';
    }
  }

  micToggle?.addEventListener('click', () => {
    if (!localStream) return;
    const audioTrack = localStream.getAudioTracks()[0];
    if (audioTrack) {
      isMicOn = !isMicOn;
      audioTrack.enabled = isMicOn;
      micToggle.classList.toggle('off', !isMicOn);
      micToggle.textContent = isMicOn ? '🎤' : '🔇';
    }
  });

  camToggle?.addEventListener('click', () => {
    if (!localStream) return;
    const videoTrack = localStream.getVideoTracks()[0];
    if (videoTrack) {
      isCamOn = !isCamOn;
      videoTrack.enabled = isCamOn;
      camToggle.classList.toggle('off', !isCamOn);
      camToggle.textContent = isCamOn ? '📹' : '🚫';
    }
  });

  leaveBtn?.addEventListener('click', () => {
    if (confirm('Voulez-vous vraiment quitter la session DUO ?')) {
      if (localStream) {
        localStream.getTracks().forEach(t => t.stop());
      }
      if (syncInterval) clearInterval(syncInterval);
      window.location.href = 'index.html';
    }
  });
})();
