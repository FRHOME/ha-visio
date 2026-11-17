/*
 * HA Visio — dashboard integration.
 *
 * Loaded as a Lovelace resource (installed automatically by the add-on). It provides:
 *  - a call client living in the Home Assistant page itself (signalling through the
 *    add-on's ingress WebSocket, media peer-to-peer WebRTC);
 *  - a phone-like incoming-call banner on top of any dashboard (Answer / Decline),
 *    which never takes over the screen;
 *  - a call window that can be shrunk to a floating thumbnail;
 *  - <ha-visio-card>, a card listing the contacts to call.
 */
if (!window.__haVisio) {
window.__haVisio = true;

const VERSION = '0.3.0';
const SLUG = new URL(import.meta.url).searchParams.get('slug') || '';
const SENSOR = 'sensor.ha_visio';

// ---------------------------------------------------------------- i18n
const I18N = {
  en: {
    visio: 'Video call', everyone: 'Everyone', online: 'Online', offline: 'Offline', connecting: 'Connecting…',
    notRunning: 'HA Visio add-on not reachable', noContacts: 'No contacts yet — pair another Home Assistant in the HA Visio panel.',
    incoming: 'Video call', from: '{0}', calling: 'Calling {0}…', ringing: 'Ringing…', answer: 'Answer', decline: 'Decline',
    cancel: 'Cancel', hangup: 'Hang up', mic: 'Microphone', cam: 'Camera', shrink: 'Shrink', expand: 'Expand',
    endHangup: 'Call ended', endDeclined: 'Call declined', endMissed: 'No answer', endBusy: 'Busy', endUnavailable: 'Unavailable',
    endOffline: 'Not reachable', endDisconnected: 'Call dropped', endCancelled: 'Call ended', endTaken: 'Answered on another screen',
    unstable: 'Unstable connection…',
    needHttps: 'No camera/mic over plain HTTP — you will see and hear without being seen.',
    needPerm: 'Camera/mic blocked by the browser — you will see and hear without being seen.',
    noDevice: 'No camera or mic found — you will see and hear without being seen.', noCam: 'No camera found — audio only from this side.',
    busyDevice: 'Camera or mic used by another app — you will see and hear without being seen.',
    errBusyLocal: 'A call is already in progress', errOffline: 'Not reachable', errUnavailable: 'Not available', errUnknownContact: 'Unknown contact',
  },
  fr: {
    visio: 'Appel vidéo', everyone: 'Tout le monde', online: 'En ligne', offline: 'Hors ligne', connecting: 'Connexion…',
    notRunning: 'Module HA Visio injoignable', noContacts: 'Aucun contact — appaire un autre Home Assistant dans le panneau HA Visio.',
    incoming: 'Appel vidéo', from: '{0}', calling: 'Appel de {0}…', ringing: 'Ça sonne…', answer: 'Répondre', decline: 'Refuser',
    cancel: 'Annuler', hangup: 'Raccrocher', mic: 'Micro', cam: 'Caméra', shrink: 'Réduire', expand: 'Agrandir',
    endHangup: 'Appel terminé', endDeclined: 'Appel refusé', endMissed: 'Pas de réponse', endBusy: 'Occupé', endUnavailable: 'Indisponible',
    endOffline: 'Injoignable', endDisconnected: 'Appel coupé', endCancelled: 'Appel terminé', endTaken: 'Répondu sur un autre écran',
    unstable: 'Connexion instable…',
    needHttps: 'Pas de caméra/micro en HTTP — tu verras et entendras sans être vu.',
    needPerm: 'Caméra/micro bloqués par le navigateur — tu verras et entendras sans être vu.',
    noDevice: 'Aucune caméra ni micro — tu verras et entendras sans être vu.', noCam: 'Aucune caméra — son seulement de ce côté.',
    busyDevice: 'Caméra ou micro utilisés par une autre app — tu verras et entendras sans être vu.',
    errBusyLocal: 'Un appel est déjà en cours', errOffline: 'Injoignable', errUnavailable: 'Indisponible', errUnknownContact: 'Contact inconnu',
  },
};
let LANG = (navigator.language || 'en').toLowerCase().startsWith('fr') ? 'fr' : 'en';
const t = (k, ...a) => (I18N[LANG][k] ?? I18N.en[k] ?? k).replace(/\{(\d)\}/g, (_, i) => a[i] ?? '');
const REASON = { hangup: 'endHangup', declined: 'endDeclined', missed: 'endMissed', busy: 'endBusy', unavailable: 'endUnavailable',
  offline: 'endOffline', disconnected: 'endDisconnected', cancelled: 'endCancelled', taken: 'endTaken' };
const ERR = { busy_local: 'errBusyLocal', offline: 'errOffline', unavailable: 'errUnavailable', unknown_contact: 'errUnknownContact' };

// ---------------------------------------------------------------- icons (Material Design Icons, Apache 2.0)
const P = {
  phone: 'M6.62,10.79C8.06,13.62 10.38,15.94 13.21,17.38L15.41,15.18C15.69,14.9 16.08,14.82 16.43,14.93C17.55,15.3 18.75,15.5 20,15.5A1,1 0 0,1 21,16.5V20A1,1 0 0,1 20,21A17,17 0 0,1 3,4A1,1 0 0,1 4,3H7.5A1,1 0 0,1 8.5,4C8.5,5.25 8.7,6.45 9.07,7.57C9.18,7.92 9.1,8.31 8.82,8.59L6.62,10.79Z',
  hangup: 'M12,9C10.4,9 8.85,9.25 7.4,9.72V12.82C7.4,13.21 7.17,13.56 6.84,13.72C5.86,14.21 4.97,14.84 4.17,15.57C4,15.75 3.75,15.86 3.5,15.86C3.2,15.86 2.95,15.74 2.77,15.56L0.29,13.08C0.11,12.9 0,12.65 0,12.38C0,12.1 0.11,11.85 0.29,11.67C3.34,8.77 7.46,7 12,7C16.54,7 20.66,8.77 23.71,11.67C23.89,11.85 24,12.1 24,12.38C24,12.65 23.89,12.9 23.71,13.08L21.23,15.56C21.05,15.74 20.8,15.86 20.5,15.86C20.25,15.86 20,15.75 19.82,15.57C19.03,14.84 18.14,14.21 17.16,13.72C16.83,13.56 16.6,13.21 16.6,12.82V9.72C15.15,9.25 13.6,9 12,9Z',
  video: 'M17,10.5V7A1,1 0 0,0 16,6H4A1,1 0 0,0 3,7V17A1,1 0 0,0 4,18H16A1,1 0 0,0 17,17V13.5L21,17.5V6.5L17,10.5Z',
  videoOff: 'M3.27,2L2,3.27L4.73,6H4A1,1 0 0,0 3,7V17A1,1 0 0,0 4,18H16C16.2,18 16.39,17.92 16.54,17.82L19.73,21L21,19.73M21,6.5L17,10.5V7A1,1 0 0,0 16,6H9.82L21,17.18V6.5Z',
  mic: 'M12,2A3,3 0 0,1 15,5V11A3,3 0 0,1 12,14A3,3 0 0,1 9,11V5A3,3 0 0,1 12,2M19,11C19,14.53 16.39,17.44 13,17.93V21H11V17.93C7.61,17.44 5,14.53 5,11H7A5,5 0 0,0 12,16A5,5 0 0,0 17,11H19Z',
  micOff: 'M19,11C19,12.19 18.66,13.3 18.1,14.28L16.87,13.05C17.14,12.43 17.3,11.74 17.3,11H19M15,11.16L9,5.18V5A3,3 0 0,1 12,2A3,3 0 0,1 15,5V11L15,11.16M4.27,3L21,19.73L19.73,21L15.54,16.81C14.77,17.27 13.91,17.58 13,17.72V21H11V17.72C7.72,17.23 5,14.41 5,11H6.7C6.7,14 9.24,16.1 12,16.1C12.81,16.1 13.6,15.91 14.31,15.58L12.65,13.92L12,14A3,3 0 0,1 9,11V10.28L3,4.27L4.27,3Z',
  shrink: 'M19.5,3.09L15,7.59V4H13V11H20V9H16.41L20.91,4.5L19.5,3.09M4,13V15H7.59L3.09,19.5L4.5,20.91L9,16.41V20H11V13H4Z',
  expand: 'M10,21V19H6.41L10.91,14.5L9.5,13.09L5,17.59V14H3V21H10M14.5,10.91L19,6.41V10H21V3H14V5H17.59L13.09,9.5L14.5,10.91Z',
};
const svg = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const initial = (s) => (String(s || '?').trim()[0] || '?').toUpperCase();

// ---------------------------------------------------------------- call client (one per page)
class VisioClient extends EventTarget {
  constructor() {
    super();
    this._hass = null;
    this.status = 'connecting';    // connecting | ready | error
    this.contacts = [];
    this.ice = [];
    this.call = null;              // { id, dir, name, sub, state: calling|ringing|incoming|active, t0 }
    this.ended = null;             // { reason, dir, at, duration }
    this.notice = null;
    this.mini = false;
    this.local = null; this.remote = null;
    this.pc = null; this.pendingIce = []; this.orphan = false;
    this.retry = 0; this.connecting = false;
  }

  get hass() { return document.querySelector('home-assistant')?.hass || this._hass; }
  setHass(h) {
    if (!h) return;
    this._hass = h;
    const lang = (h.locale?.language || h.language || '').toLowerCase();
    if (lang) LANG = lang.startsWith('fr') ? 'fr' : 'en';
    if (!this.ws && !this.connecting) this.connect();
  }
  emit() { this.dispatchEvent(new Event('change')); }
  say(key) { this.notice = { text: t(key), at: Date.now() }; this.emit(); setTimeout(() => { if (this.notice && Date.now() - this.notice.at >= 4400) { this.notice = null; this.emit(); } }, 4500); }

  // --- ingress -----------------------------------------------------------------
  async ingressEntry() {
    const st = this.hass.states?.[SENSOR];
    if (st?.attributes?.ingress_entry) return st.attributes.ingress_entry;
    if (!SLUG) throw new Error('add-on slug unknown');
    const info = await this.hass.callWS({ type: 'supervisor/api', endpoint: `/addons/${SLUG}/info`, method: 'get' });
    if (!info?.ingress_entry) throw new Error('no ingress entry');
    return info.ingress_entry;
  }
  async newSession() {
    const r = await this.hass.callWS({ type: 'supervisor/api', endpoint: '/ingress/session', method: 'post' });
    this.session = r.session;
    document.cookie = `ingress_session=${r.session};path=/api/hassio_ingress/;SameSite=Strict${location.protocol === 'https:' ? ';Secure' : ''}`;
  }
  keepAlive() {
    clearInterval(this.ka);
    this.ka = setInterval(async () => {
      try { await this.hass.callWS({ type: 'supervisor/api', endpoint: '/ingress/validate_session', method: 'post', data: { session: this.session } }); }
      catch (e) { try { await this.newSession(); } catch (_) {} }
    }, 60000);
  }

  async connect() {
    if (this.connecting || !this.hass) return;
    this.connecting = true;
    try {
      this.entry = (await this.ingressEntry()).replace(/\/$/, '');
      await this.newSession();
      this.keepAlive();
      const r = await fetch(`${this.entry}/api/state`, { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const st = await r.json();
      this.ice = st.ice || []; this.contacts = st.contacts || []; this.me = st.me;
      const u = new URL(`${this.entry}/ws`, location.href);
      u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(u);
      this.ws = ws;
      ws.onmessage = (e) => { try { this.onMessage(JSON.parse(e.data)); } catch (err) { console.error('[HA Visio]', err); } };
      ws.onopen = () => { this.retry = 0; this.status = 'ready'; this.emit(); };
      ws.onclose = () => {
        if (this.ws !== ws) return;
        this.ws = null;
        if (this.call) this.finish('disconnected');
        this.status = 'connecting'; this.emit();
        setTimeout(() => this.connect(), Math.min(1000 * 2 ** this.retry++, 30000));
      };
    } catch (e) {
      console.warn('[HA Visio] cannot connect:', e);
      this.status = 'error'; this.emit();
      setTimeout(() => this.connect(), Math.min(2000 * 2 ** this.retry++, 60000));
    } finally {
      this.connecting = false;
    }
  }
  send(o) { if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(o)); }

  // --- media -------------------------------------------------------------------
  async getMedia() {
    if (this.local) return this.local;
    let note = null;
    if (!navigator.mediaDevices || !window.isSecureContext) note = 'needHttps';
    else {
      const audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
      const video = { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } };
      let err = null;
      for (const c of [{ audio, video }, { audio }, { video }]) {
        try { this.local = await navigator.mediaDevices.getUserMedia(c); break; }
        catch (e) { err = e; if (e.name === 'NotAllowedError' || e.name === 'SecurityError') break; }
      }
      if (!this.local) note = { NotAllowedError: 'needPerm', SecurityError: 'needPerm', NotReadableError: 'busyDevice', AbortError: 'busyDevice' }[err?.name] || 'noDevice';
      else if (!this.local.getVideoTracks().length) note = 'noCam';
    }
    if (!this.local) this.local = new MediaStream();
    if (note) this.say(note);
    this.emit();
    return this.local;
  }
  stopMedia() { this.local?.getTracks().forEach((tr) => tr.stop()); this.local = null; this.remote = null; }
  toggle(kind) {
    const tr = kind === 'mic' ? this.local?.getAudioTracks()[0] : this.local?.getVideoTracks()[0];
    if (tr) { tr.enabled = !tr.enabled; this.emit(); }
  }
  muted(kind) {
    const tr = kind === 'mic' ? this.local?.getAudioTracks()[0] : this.local?.getVideoTracks()[0];
    return !tr || !tr.enabled;
  }
  makePc(offerer) {
    const pc = new RTCPeerConnection({ iceServers: this.ice });
    this.pc = pc;
    this.local.getTracks().forEach((tr) => pc.addTrack(tr, this.local));
    if (offerer) {
      const kinds = new Set(this.local.getTracks().map((tr) => tr.kind));
      for (const k of ['audio', 'video']) if (!kinds.has(k)) pc.addTransceiver(k, { direction: 'recvonly' });
    }
    pc.onicecandidate = (e) => { if (e.candidate && this.call) this.send({ type: 'signal', call: this.call.id, data: { ice: e.candidate } }); };
    pc.ontrack = (e) => { this.remote = e.streams[0] || new MediaStream([e.track]); this.emit(); };
    pc.onconnectionstatechange = () => {
      if (this.pc !== pc) return;
      this.unstable = pc.connectionState === 'disconnected';
      if (pc.connectionState === 'failed') this.hangup(); else this.emit();
    };
  }
  async onSignal(d) {
    const pc = this.pc;
    if (!pc) return;
    if (d.sdp) {
      await pc.setRemoteDescription(d.sdp);
      for (const c of this.pendingIce) await pc.addIceCandidate(c).catch(() => {});
      this.pendingIce = [];
      if (d.sdp.type === 'offer') {
        await pc.setLocalDescription(await pc.createAnswer());
        this.send({ type: 'signal', call: this.call.id, data: { sdp: pc.localDescription } });
      }
    } else if (d.ice) {
      if (pc.remoteDescription) await pc.addIceCandidate(d.ice).catch(() => {});
      else this.pendingIce.push(d.ice);
    }
  }

  // --- ringtone ----------------------------------------------------------------
  ring(kind) {
    this.unring();
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.ringCtx = ctx;
      ctx.resume?.().catch(() => {});
      const beep = () => {
        const notes = kind === 'out' ? [[425, 0, 1.0]] : [[880, 0, .22], [660, .27, .22], [880, .54, .22], [660, .81, .22]];
        for (const [f, at, d] of notes) {
          const o = ctx.createOscillator(), g = ctx.createGain(), v = kind === 'out' ? .12 : .5, s = ctx.currentTime + at;
          o.frequency.value = f;
          g.gain.setValueAtTime(0, s); g.gain.linearRampToValueAtTime(v, s + .02);
          g.gain.setValueAtTime(v, s + d - .03); g.gain.linearRampToValueAtTime(0, s + d);
          o.connect(g).connect(ctx.destination); o.start(s); o.stop(s + d + .05);
        }
      };
      beep(); this.ringTimer = setInterval(beep, kind === 'out' ? 4000 : 2400);
    } catch (e) {}
    if (kind === 'in' && navigator.vibrate) navigator.vibrate([500, 300, 500]);
  }
  unring() {
    clearInterval(this.ringTimer); this.ringTimer = null;
    if (this.ringCtx) { this.ringCtx.close().catch(() => {}); this.ringCtx = null; }
    if (navigator.vibrate) navigator.vibrate(0);
  }

  // --- actions -----------------------------------------------------------------
  async place(contact, endpoint, label) {
    if (this.call || this.status !== 'ready') return;
    this.ended = null; this.mini = false;
    this.call = { id: null, dir: 'out', name: label, sub: contact.name, state: 'calling' };
    this.emit();
    this.ring('out');
    await this.getMedia();
    if (!this.call) return;
    this.send({ type: 'call', contact: contact.id, endpoint });
  }
  async answer() {
    const c = this.call;
    if (!c || c.state !== 'incoming') return;
    this.unring();
    c.state = 'connecting'; this.emit();
    await this.getMedia();
    if (this.call !== c) return;
    this.makePc(false);
    this.send({ type: 'accept', call: c.id });
    this.active();
  }
  decline() { if (this.call) this.send({ type: 'decline', call: this.call.id }); this.finish('declined'); }
  hangup() {
    if (!this.call) return;
    if (this.call.id) this.send({ type: 'hangup', call: this.call.id }); else this.orphan = true;
    this.finish(this.call.state === 'active' ? 'hangup' : 'cancelled');
  }
  active() {
    this.unring();
    this.call.state = 'active'; this.call.t0 = Date.now();
    this.emit();
  }
  finish(reason) {
    const c = this.call;
    this.unring();
    if (this.pc) { this.pc.close(); this.pc = null; }
    this.pendingIce = []; this.unstable = false;
    this.stopMedia();
    this.call = null; this.mini = false;
    this.ended = { reason, dir: c?.dir, at: Date.now(), name: c?.name, duration: c?.t0 ? Math.floor((Date.now() - c.t0) / 1000) : 0 };
    this.emit();
    setTimeout(() => { if (this.ended && Date.now() - this.ended.at >= 2900) { this.ended = null; this.emit(); } }, 3000);
  }

  async onMessage(m) {
    const mine = this.call && this.call.id === m.call;
    switch (m.type) {
      case 'welcome': case 'directory':
        this.contacts = m.contacts || []; this.emit(); break;
      case 'calling':
        if (this.orphan) { this.orphan = false; this.send({ type: 'hangup', call: m.call }); break; }
        if (this.call && this.call.dir === 'out' && !this.call.id) this.call.id = m.call;
        break;
      case 'ringing': if (mine) { this.call.ringing = true; this.emit(); } break;
      case 'error':
        this.orphan = false;
        if (this.call && this.call.dir === 'out' && !this.call.id) { this.unring(); this.stopMedia(); this.call = null; }
        this.say(ERR[m.code] || 'errUnavailable');
        break;
      case 'incoming':
        if (this.call) break;
        this.ended = null; this.mini = false;
        this.call = { id: m.call, dir: 'in', name: m.from || '?', sub: m.contact || '', state: 'incoming' };
        this.ring('in'); this.emit();
        break;
      case 'accepted':
        if (!mine) break;
        this.active(); this.makePc(true);
        await this.pc.setLocalDescription(await this.pc.createOffer());
        this.send({ type: 'signal', call: this.call.id, data: { sdp: this.pc.localDescription } });
        break;
      case 'signal': if (mine) await this.onSignal(m.data || {}); break;
      case 'taken': if (mine) this.finish('taken'); break;
      case 'ended': if (mine) this.finish(m.reason); break;
    }
  }
}

const client = new VisioClient();
window.haVisio = client;   // for automations / debugging: haVisio.call, haVisio.contacts

// ---------------------------------------------------------------- overlay (banner + call window)
const OVERLAY_CSS = `
  :host { all: initial; font-family: var(--ha-font-family-body, Roboto, system-ui, -apple-system, "Segoe UI", sans-serif); }
  * { box-sizing: border-box; }
  button { font: inherit; cursor: pointer; border: 0; padding: 0; background: none; color: inherit; -webkit-tap-highlight-color: transparent; }
  svg { width: 24px; height: 24px; fill: currentColor; display: block; }

  .banner { position: fixed; z-index: 2147483000; left: 50%; top: calc(12px + env(safe-area-inset-top));
    transform: translateX(-50%); width: min(440px, calc(100vw - 20px)); display: flex; align-items: center; gap: 14px;
    padding: 12px 12px 12px 14px; border-radius: 26px; color: #fff;
    background: rgba(28, 28, 30, .86); backdrop-filter: blur(24px) saturate(160%); -webkit-backdrop-filter: blur(24px) saturate(160%);
    box-shadow: 0 12px 40px rgba(0,0,0,.35), 0 0 0 .5px rgba(255,255,255,.08) inset; animation: drop .38s cubic-bezier(.2,.9,.3,1.2); }
  @keyframes drop { from { transform: translate(-50%, -140%); opacity: 0; } to { transform: translate(-50%, 0); opacity: 1; } }
  .avatar { flex: none; width: 46px; height: 46px; border-radius: 50%; display: grid; place-items: center; font-size: 20px; font-weight: 600;
    background: linear-gradient(135deg, #64d2ff, #0a84ff); color: #fff; position: relative; }
  .avatar.ring::after { content: ""; position: absolute; inset: -4px; border-radius: 50%; border: 2px solid rgba(48,209,88,.8); animation: halo 1.6s ease-out infinite; }
  @keyframes halo { from { transform: scale(.92); opacity: 1; } to { transform: scale(1.35); opacity: 0; } }
  .who { flex: 1; min-width: 0; }
  .who b { display: block; font-size: 17px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .who span { display: block; font-size: 13px; color: rgba(235,235,245,.62); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .round { width: 46px; height: 46px; border-radius: 50%; display: grid; place-items: center; flex: none; transition: transform .12s, filter .12s; }
  .round:active { transform: scale(.9); } .round:hover { filter: brightness(1.08); }
  .red { background: #ff3b30; } .green { background: #30d158; } .grey { background: rgba(255,255,255,.16); } .white { background: #fff; color: #111; }
  .round svg { width: 24px; height: 24px; }
  .acts { display: flex; gap: 12px; }
  .banner.small { width: min(360px, calc(100vw - 20px)); }
  .banner.note { gap: 10px; padding: 12px 18px; justify-content: center; font-size: 14px; }

  .backdrop { position: fixed; inset: 0; z-index: 2147482990; background: rgba(0,0,0,.45); animation: fade .25s; }
  @keyframes fade { from { opacity: 0; } }
  .win { position: fixed; z-index: 2147483001; background: #000; overflow: hidden; color: #fff; box-shadow: 0 20px 60px rgba(0,0,0,.5); }
  .win.big { left: 50%; top: 50%; transform: translate(-50%, -50%); width: min(1000px, 96vw); height: min(640px, 88vh); border-radius: 24px; animation: pop .3s cubic-bezier(.2,.9,.3,1.1); }
  @keyframes pop { from { transform: translate(-50%, -46%) scale(.96); opacity: 0; } }
  .win.mini { right: 16px; bottom: calc(16px + env(safe-area-inset-bottom)); width: min(300px, 46vw); aspect-ratio: 16 / 10; border-radius: 18px; touch-action: none; cursor: grab; }
  .remote { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; background: #111; }
  .placeholder { position: absolute; inset: 0; display: grid; place-items: center; background: radial-gradient(circle at 50% 40%, #2c2c2e, #000); }
  .placeholder .avatar { width: 96px; height: 96px; font-size: 40px; }
  .win.mini .placeholder .avatar { width: 52px; height: 52px; font-size: 22px; }
  .local { position: absolute; right: 14px; top: 14px; width: 22%; max-width: 200px; aspect-ratio: 3 / 4; object-fit: cover; border-radius: 14px;
    transform: scaleX(-1); background: #222; box-shadow: 0 4px 16px rgba(0,0,0,.4); }
  .win.mini .local { display: none; }
  .top { position: absolute; left: 0; right: 0; top: 0; padding: 16px 18px 30px; background: linear-gradient(rgba(0,0,0,.55), transparent); pointer-events: none; }
  .top b { font-size: 18px; font-weight: 600; } .top span { display: block; font-size: 13px; color: rgba(255,255,255,.75); margin-top: 2px; font-variant-numeric: tabular-nums; }
  .bar { position: absolute; left: 0; right: 0; bottom: 0; display: flex; justify-content: center; gap: 18px; padding: 34px 0 20px; background: linear-gradient(transparent, rgba(0,0,0,.6)); }
  .bar .round { width: 56px; height: 56px; }
  .win.mini .top, .win.mini .bar { display: none; }
  .mini-ctl { position: absolute; inset: auto 8px 8px 8px; display: none; justify-content: space-between; }
  .win.mini .mini-ctl { display: flex; }
  .mini-ctl .round { width: 38px; height: 38px; } .mini-ctl svg { width: 20px; height: 20px; }
  .warn { position: absolute; left: 50%; top: 70px; transform: translateX(-50%); background: rgba(0,0,0,.6); padding: 6px 12px; border-radius: 10px; font-size: 13px; color: #ffd60a; }
  @media (max-width: 600px) { .win.big { width: 100vw; height: 100dvh; border-radius: 0; } .local { width: 30%; } }
`;

class VisioOverlay extends HTMLElement {
  constructor() {
    super();
    this.root = this.attachShadow({ mode: 'open' });
    this.root.innerHTML = `<style>${OVERLAY_CSS}</style><div id="o"></div>`;
    this.o = this.root.getElementById('o');
    this.pos = null;
    client.addEventListener('change', () => this.render());
    setInterval(() => this.tick(), 1000);
  }
  tick() {
    const el = this.root.getElementById('clock');
    if (el && client.call?.t0) el.textContent = fmt(Math.floor((Date.now() - client.call.t0) / 1000));
  }
  render() {
    const c = client.call;
    const key = c ? `${c.id}|${c.state}|${client.mini}|${!!client.remote}|${!!client.local}|${client.local?.getVideoTracks().length}|${client.muted('mic')}|${client.muted('cam')}|${client.unstable}|${c.ringing}` :
      `none|${client.ended?.at}|${client.notice?.at}`;
    if (key === this.key) return;
    this.key = key;
    let html = '';
    if (c && (c.state === 'incoming' || c.state === 'connecting')) {
      html = `<div class="banner" role="alertdialog">
        <div class="avatar ring">${esc(initial(c.name))}</div>
        <div class="who"><b>${esc(c.name)}</b><span>${esc(t('incoming'))}${c.sub ? ' · ' + esc(c.sub) : ''}</span></div>
        <div class="acts">
          <button class="round red" id="decline" title="${esc(t('decline'))}" aria-label="${esc(t('decline'))}">${svg(P.hangup)}</button>
          <button class="round green" id="answer" title="${esc(t('answer'))}" aria-label="${esc(t('answer'))}">${svg(P.video)}</button>
        </div></div>`;
    } else if (c && c.state === 'calling') {
      html = `<div class="banner">
        <div class="avatar">${esc(initial(c.name))}</div>
        <div class="who"><b>${esc(t('calling', c.name))}</b><span>${esc(c.ringing ? t('ringing') : c.sub)}</span></div>
        <div class="acts"><button class="round red" id="hangup" title="${esc(t('cancel'))}" aria-label="${esc(t('cancel'))}">${svg(P.hangup)}</button></div></div>`;
    } else if (c && c.state === 'active') {
      const hasLocalVideo = client.local?.getVideoTracks().length > 0;
      const mic = client.muted('mic'), cam = client.muted('cam');
      html = `${client.mini ? '' : '<div class="backdrop" id="backdrop"></div>'}
        <div class="win ${client.mini ? 'mini' : 'big'}" id="win" style="${client.mini && this.pos ? `right:auto;bottom:auto;left:${this.pos.x}px;top:${this.pos.y}px` : ''}">
          <div class="placeholder"><div class="avatar">${esc(initial(c.name))}</div></div>
          <video class="remote" id="remote" autoplay playsinline ${client.remote ? '' : 'hidden'}></video>
          ${hasLocalVideo ? '<video class="local" id="local" autoplay playsinline muted></video>' : ''}
          <div class="top"><b>${esc(c.name)}</b><span>${esc(c.sub)} · <span id="clock" style="display:inline">${fmt(Math.floor((Date.now() - c.t0) / 1000))}</span></span></div>
          ${client.unstable ? `<div class="warn">${esc(t('unstable'))}</div>` : ''}
          <div class="bar">
            <button class="round ${mic ? 'white' : 'grey'}" id="mic" title="${esc(t('mic'))}">${svg(mic ? P.micOff : P.mic)}</button>
            <button class="round ${cam ? 'white' : 'grey'}" id="cam" title="${esc(t('cam'))}" ${hasLocalVideo ? '' : 'disabled style="opacity:.35"'}>${svg(cam ? P.videoOff : P.video)}</button>
            <button class="round grey" id="shrink" title="${esc(t('shrink'))}">${svg(P.shrink)}</button>
            <button class="round red" id="hangup" title="${esc(t('hangup'))}">${svg(P.hangup)}</button>
          </div>
          <div class="mini-ctl">
            <button class="round grey" id="expand" title="${esc(t('expand'))}">${svg(P.expand)}</button>
            <button class="round red" id="hangup2" title="${esc(t('hangup'))}">${svg(P.hangup)}</button>
          </div>
        </div>`;
    } else if (client.ended) {
      const e = client.ended;
      html = `<div class="banner small note"><b>${esc(t(REASON[e.reason] || 'endHangup'))}</b>${e.duration ? `<span style="color:rgba(235,235,245,.6)">· ${fmt(e.duration)}</span>` : ''}</div>`;
    }
    if (client.notice && !(c && c.state === 'active')) {
      html += `<div class="banner small note" style="top:calc(84px + env(safe-area-inset-top))">${esc(client.notice.text)}</div>`;
    } else if (client.notice) {
      html += `<div class="banner small note" style="z-index:2147483002">${esc(client.notice.text)}</div>`;
    }
    this.o.innerHTML = html;
    const $ = (id) => this.root.getElementById(id);
    $('answer')?.addEventListener('click', () => client.answer());
    $('decline')?.addEventListener('click', () => client.decline());
    $('hangup')?.addEventListener('click', () => client.hangup());
    $('hangup2')?.addEventListener('click', (e) => { e.stopPropagation(); client.hangup(); });
    $('mic')?.addEventListener('click', () => client.toggle('mic'));
    $('cam')?.addEventListener('click', () => client.toggle('cam'));
    $('shrink')?.addEventListener('click', () => { client.mini = true; client.emit(); });
    $('backdrop')?.addEventListener('click', () => { client.mini = true; client.emit(); });
    $('expand')?.addEventListener('click', (e) => { e.stopPropagation(); client.mini = false; client.emit(); });
    const remote = $('remote'), local = $('local');
    if (remote && client.remote) { remote.srcObject = client.remote; remote.play().catch(() => {}); }
    if (local && client.local) local.srcObject = client.local;
    if (client.mini) this.draggable($('win'));
  }
  draggable(win) {
    if (!win) return;
    let start = null, moved = false;
    win.addEventListener('pointerdown', (e) => {
      if (e.target.closest('button')) return;
      const r = win.getBoundingClientRect();
      start = { x: e.clientX - r.left, y: e.clientY - r.top }; moved = false;
      win.setPointerCapture(e.pointerId); win.style.cursor = 'grabbing';
    });
    win.addEventListener('pointermove', (e) => {
      if (!start) return;
      moved = true;
      const x = Math.max(4, Math.min(innerWidth - win.offsetWidth - 4, e.clientX - start.x));
      const y = Math.max(4, Math.min(innerHeight - win.offsetHeight - 4, e.clientY - start.y));
      this.pos = { x, y };
      Object.assign(win.style, { left: x + 'px', top: y + 'px', right: 'auto', bottom: 'auto' });
    });
    win.addEventListener('pointerup', () => {
      win.style.cursor = '';
      if (start && !moved) { client.mini = false; client.emit(); }   // a tap expands it
      start = null;
    });
  }
}
const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
customElements.define('ha-visio-overlay', VisioOverlay);

// ---------------------------------------------------------------- card
const CARD_CSS = `
  ha-card { padding: 16px; }
  .head { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
  .head ha-icon { color: var(--primary-color); }
  .head .title { font-size: 18px; font-weight: 500; color: var(--primary-text-color); flex: 1; }
  .state { font-size: 12px; color: var(--secondary-text-color); }
  .contact + .contact { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--divider-color, rgba(127,127,127,.2)); }
  .name { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; color: var(--primary-text-color); font-weight: 500; font-size: 15px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--disabled-text-color, #999); }
  .dot.on { background: #30d158; box-shadow: 0 0 0 3px rgba(48,209,88,.18); }
  .name small { font-weight: 400; color: var(--secondary-text-color); font-size: 12px; }
  .targets { display: flex; flex-wrap: wrap; gap: 8px; }
  button { font: inherit; border: 0; cursor: pointer; display: inline-flex; align-items: center; gap: 8px; height: 44px; padding: 0 16px 0 12px;
    border-radius: 22px; font-size: 14px; font-weight: 500; color: var(--primary-text-color);
    background: var(--secondary-background-color, rgba(127,127,127,.12)); transition: transform .1s, filter .1s; -webkit-tap-highlight-color: transparent; }
  button:hover { filter: brightness(1.06); } button:active { transform: scale(.96); }
  button.all { background: #30d158; color: #fff; }
  button[disabled] { opacity: .4; cursor: default; transform: none; }
  button ha-icon { --mdc-icon-size: 20px; }
  .msg { color: var(--secondary-text-color); font-size: 14px; line-height: 1.4; }
`;

class HaVisioCard extends HTMLElement {
  constructor() { super(); this.onChange = () => this.render(); }
  setConfig(config) { this.config = { ...(config || {}) }; this.render(); }
  set hass(h) { client.setHass(h); ensureOverlay(); if (!this.rendered) this.render(); }
  connectedCallback() { client.addEventListener('change', this.onChange); this.render(); }
  disconnectedCallback() { client.removeEventListener('change', this.onChange); }
  getCardSize() { return 1 + Math.max(1, client.contacts.length) * 2; }
  getGridOptions() { return { columns: 12, min_columns: 6, rows: 'auto' }; }
  static getStubConfig() { return {}; }

  render() {
    if (!this.config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    this.rendered = true;
    const cfg = this.config;
    let contacts = client.contacts;
    if (Array.isArray(cfg.contacts) && cfg.contacts.length) {
      const want = cfg.contacts.map((s) => String(s).toLowerCase());
      contacts = contacts.filter((c) => want.includes(c.name.toLowerCase()));
    }
    const busy = !!client.call;
    let body;
    if (client.status === 'error') body = `<div class="msg">${esc(t('notRunning'))}</div>`;
    else if (client.status !== 'ready' && !contacts.length) body = `<div class="msg">${esc(t('connecting'))}</div>`;
    else if (!contacts.length) body = `<div class="msg">${esc(t('noContacts'))}</div>`;
    else body = contacts.map((c) => {
      let eps = c.endpoints || [];
      if (Array.isArray(cfg.endpoints) && cfg.endpoints.length) {
        const want = cfg.endpoints.map((s) => String(s).toLowerCase());
        eps = eps.filter((e) => want.includes(e.name.toLowerCase()));
      }
      const off = !c.online || busy || client.status !== 'ready';
      const all = cfg.show_everyone !== false && eps.filter((e) => e.ring_all).length > 1
        ? `<button class="all" data-c="${esc(c.id)}" data-e="*" data-l="${esc(c.name)}" ${off ? 'disabled' : ''}><ha-icon icon="mdi:phone"></ha-icon>${esc(t('everyone'))}</button>` : '';
      const btns = eps.map((e) => `<button data-c="${esc(c.id)}" data-e="${esc(e.id)}" data-l="${esc(e.name)}" ${off ? 'disabled' : ''}>
        <ha-icon icon="${esc(e.icon || (e.kind === 'person' ? 'mdi:account' : 'mdi:tablet'))}"></ha-icon>${esc(e.name)}</button>`).join('');
      return `<div class="contact"><div class="name"><span class="dot ${c.online ? 'on' : ''}"></span>${esc(c.name)}
        ${c.online ? '' : `<small>${esc(t('offline'))}</small>`}</div><div class="targets">${all}${btns}</div></div>`;
    }).join('');
    const title = cfg.title === undefined ? t('visio') : cfg.title;
    this.shadowRoot.innerHTML = `<style>${CARD_CSS}</style><ha-card>
      ${title ? `<div class="head"><ha-icon icon="${esc(cfg.icon || 'mdi:video')}"></ha-icon><div class="title">${esc(title)}</div></div>` : ''}
      ${body}</ha-card>`;
    this.shadowRoot.querySelectorAll('button[data-c]').forEach((b) => b.addEventListener('click', () => {
      const contact = client.contacts.find((c) => c.id === b.dataset.c);
      if (contact) client.place(contact, b.dataset.e, b.dataset.l);
    }));
  }
}
customElements.define('ha-visio-card', HaVisioCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: 'ha-visio-card', name: 'HA Visio', description: 'Video calls with your other Home Assistant instances', preview: false,
  documentationURL: 'https://github.com/FRHOME/ha-visio' });

// ---------------------------------------------------------------- bootstrap on every page
function ensureOverlay() {
  if (!document.body || document.querySelector('ha-visio-overlay')) return;
  document.body.appendChild(document.createElement('ha-visio-overlay'));
}
(function boot() {
  const h = document.querySelector('home-assistant')?.hass;
  if (h) { ensureOverlay(); client.setHass(h); return; }
  setTimeout(boot, 1000);
})();
console.info(`%c HA VISIO %c ${VERSION} `, 'background:#30d158;color:#fff;font-weight:700;border-radius:4px 0 0 4px', 'background:#333;color:#fff;border-radius:0 4px 4px 0');
}
