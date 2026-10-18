/* Guest Web Player client. Guests watch, follow the score, stage a cart and read chat with no login; any write
   (chat, checkout) is attempted against the server, and the server's refusal opens the progressive login modal.
   The guest token lives in sessionStorage, so every tab is its own guest. */
(function () {
  'use strict';

  var boot = JSON.parse(document.getElementById('boot').textContent);
  var KEYS = { guest: 'nascon.guest_token', member: 'nascon.member_token', name: 'nascon.member_name',
               count: 'nascon.cart_count', local: 'nascon.local_cart', resume: 'nascon.resume' };

  function byId(id) { return document.getElementById(id); }
  var el = {
    video: byId('video'), streamState: byId('stream-state'), streamText: byId('stream-state-text'),
    play: byId('ctl-play'), mute: byId('ctl-mute'), live: byId('ctl-live'), full: byId('ctl-full'),
    shell: byId('video-shell'), score: byId('score-banner'), status: byId('match-status'),
    homeName: byId('home-name'), awayName: byId('away-name'),
    merch: byId('merch'), merchName: byId('merch-name'), merchVariant: byId('merch-variant'),
    merchPrice: byId('merch-price'), merchSku: byId('merch-sku'), add: byId('add-to-cart'),
    checkout: byId('checkout'), cartNote: byId('cart-note'), count: byId('cart-count'),
    chatList: byId('chat-list'), chatForm: byId('chat-form'), chatInput: byId('chat-input'),
    chatNote: byId('chat-note'), viewer: byId('viewer-chip'),
    modal: byId('auth-modal'), authForm: byId('auth-form'), email: byId('auth-email'),
    password: byId('auth-password'), authError: byId('auth-error'), authCancel: byId('auth-cancel'),
    authSubmit: byId('auth-submit'), hosts: byId('hosts-list'), layoutChip: byId('layout-chip')
  };

  var STATUS_LABELS = { in_progress: 'Live', upcoming: 'Upcoming', completed: 'Full time', postponed: 'Postponed',
                        cancelled: 'Cancelled', abandoned: 'Abandoned' };

  /* ---------- storage (private windows may refuse it) ---------- */
  var mem = {};
  var store = {
    get: function (k) { try { return window.sessionStorage.getItem(k); } catch (e) { return mem[k] || null; } },
    set: function (k, v) { try { window.sessionStorage.setItem(k, v); } catch (e) { mem[k] = v; } },
    del: function (k) { try { window.sessionStorage.removeItem(k); } catch (e) { delete mem[k]; } }
  };

  /* ---------- HTTP ---------- */
  function api(method, path, body, token) {
    var headers = { 'Accept': 'application/json' };
    if (body !== undefined) { headers['Content-Type'] = 'application/json'; }
    if (token) { headers['Authorization'] = 'Bearer ' + token; }
    return fetch(path, { method: method, headers: headers, cache: 'no-store', credentials: 'omit',
                         body: body === undefined ? undefined : JSON.stringify(body) })
      .then(function (res) {
        return res.text().then(function (txt) {
          var data = null;
          try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = null; }
          return { ok: res.ok, status: res.status, data: data };
        });
      })
      .catch(function () { return { ok: false, status: 0, data: null }; });
  }
  function errorCode(r) { return r && r.data && r.data.error ? r.data.error.code : null; }

  function wsUrl(path) {
    return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + path;
  }

  /* A socket that reconnects with backoff; onOpen runs on every (re)connect so callers can resync. */
  function liveSocket(path, flag, onMessage, onOpen) {
    var delay = 500;
    function connect() {
      var ws;
      try { ws = new WebSocket(wsUrl(path)); } catch (e) { setTimeout(connect, delay); return; }
      ws.onopen = function () {
        delay = 500;
        document.body.setAttribute('data-' + flag, 'open');
        if (onOpen) { onOpen(); }
      };
      ws.onmessage = function (ev) {
        var msg = null;
        try { msg = JSON.parse(ev.data); } catch (e) { return; }
        if (msg) { onMessage(msg); }
      };
      ws.onclose = function () {
        document.body.setAttribute('data-' + flag, 'closed');
        setTimeout(connect, delay);
        delay = Math.min(delay * 2, 5000);
      };
      ws.onerror = function () { try { ws.close(); } catch (e) { /* closing anyway */ } };
    }
    connect();
  }

  /* ---------- identity ---------- */
  function memberToken() { return store.get(KEYS.member); }

  var guestPending = null;
  function guestToken() {
    var saved = store.get(KEYS.guest);
    if (saved) { return Promise.resolve(saved); }
    if (!guestPending) {
      guestPending = api('POST', '/api/v1/guest/token', {}).then(function (r) {
        guestPending = null;
        var tok = r.ok && r.data && typeof r.data.guest_token === 'string' ? r.data.guest_token : null;
        if (tok) { store.set(KEYS.guest, tok); }
        return tok;
      });
    }
    return guestPending;
  }
  function dropGuestToken() { store.del(KEYS.guest); }

  function showViewer() {
    var name = store.get(KEYS.name);
    if (el.viewer) { el.viewer.textContent = memberToken() ? 'Signed in' + (name ? ' as ' + name : '') : 'Watching as guest'; }
  }

  /* ---------- score ---------- */
  function setStatus(status) {
    if (!el.status || !status) { return; }
    el.status.setAttribute('data-status', status);
    el.status.textContent = STATUS_LABELS[status] || status;
  }
  function applyMatch(match) {
    if (!match || !match.home || !match.away) { return; }
    var text = match.home.score + ' - ' + match.away.score;
    if (el.score.textContent !== text) {
      el.score.textContent = text;
      el.score.classList.add('bump');
      setTimeout(function () { el.score.classList.remove('bump'); }, 600);
    }
    setStatus(match.status);
  }

  function applyOverlay(o) {
    if (!o) { return; }
    var match = boot.kind === 'costream' ? o.match : o;
    if (match) {
      applyMatch(match);
      if (match.home && match.home.name && el.homeName) { el.homeName.textContent = match.home.name; }
      if (match.away && match.away.name && el.awayName) { el.awayName.textContent = match.away.name; }
    }
    if (o.pinned_product) { showProduct(o.pinned_product); }
    if (o.playback_url && o.playback_url !== currentUrl) { startPlayback(o.playback_url); }
    if (boot.kind === 'costream') { showHosts(o); }
  }

  function refreshOverlay() {
    return api('GET', boot.overlay_path).then(function (r) { if (r.ok) { applyOverlay(r.data); } });
  }

  function watchMatch(matchId) {
    if (!matchId) { return; }
    liveSocket('/ws/matches/' + encodeURIComponent(matchId), 'score-socket', function (msg) {
      if (msg.type === 'score_update') { applyMatch(msg.match); }
      else if (msg.type === 'pinned_product') {
        if (msg.product) { showProduct(normalizeProduct(msg.product)); } else { refreshOverlay(); }
      }
    }, refreshOverlay);
  }

  /* ---------- co-stream hosts ---------- */
  function showHosts(o) {
    if (!el.hosts) { return; }
    var names = o.participant_names || {};
    el.hosts.textContent = '';
    (o.active_participants || []).forEach(function (p) {
      var li = document.createElement('li');
      li.textContent = (names[p] || p) + (p === o.active_host ? ' (host)' : '');
      el.hosts.appendChild(li);
    });
    if (!el.hosts.children.length) {
      var none = document.createElement('li');
      none.className = 'muted';
      none.textContent = 'No hosts live yet';
      el.hosts.appendChild(none);
    }
    if (el.layoutChip && o.layout) { el.layoutChip.textContent = o.layout; }
  }

  /* ---------- video ---------- */
  var hls = null;
  var currentUrl = null;
  var hlsLoader = null;

  function setStream(text) {
    if (text) { el.streamText.textContent = text; el.streamState.hidden = false; }
    else { el.streamState.hidden = true; }
  }
  function loadHls() {
    if (window.Hls) { return Promise.resolve(window.Hls); }
    if (!hlsLoader) {
      hlsLoader = new Promise(function (resolve) {
        var s = document.createElement('script');
        s.src = boot.hls_src;
        s.async = true;
        s.onload = function () { resolve(window.Hls || null); };
        s.onerror = function () { hlsLoader = null; resolve(null); };
        document.head.appendChild(s);
      });
    }
    return hlsLoader;
  }
  /* Playback URLs name the media server as localhost. Any other viewer (a phone on the venue Wi-Fi, a Selenium
     Grid node) plays the same stream through this origin's /web/hls proxy. */
  function reachable(url) {
    try {
      var u = new URL(url, location.href);
      var here = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
      if (!here && /^\/(live|costream)\//.test(u.pathname)) { return '/web/hls' + u.pathname + u.search; }
      return u.toString();
    } catch (e) { return url; }
  }
  function startPlayback(url) {
    if (!url) { return; }
    currentUrl = url;
    var src = reachable(url);
    if (hls) { hls.destroy(); hls = null; }
    if (el.video.canPlayType('application/vnd.apple.mpegurl')) {
      el.video.src = src;
      el.video.play().catch(function () { /* autoplay may wait for a tap */ });
      return;
    }
    loadHls().then(function (Hls) {
      if (!Hls || !Hls.isSupported()) { setStream('This browser cannot play the stream'); return; }
      hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 2, manifestLoadingMaxRetry: 2 });
      hls.on(Hls.Events.MANIFEST_PARSED, function () {
        el.video.play().catch(function () { /* autoplay may wait for a tap */ });
      });
      hls.on(Hls.Events.ERROR, function (_, data) {
        if (data && data.fatal) {
          setStream(STAGE_TEXT);
          hls.destroy(); hls = null; currentUrl = null;
          setTimeout(refreshOverlay, 5000);
        }
      });
      hls.loadSource(src);
      hls.attachMedia(el.video);
    });
  }
  var STAGE_TEXT = el.streamText ? el.streamText.textContent : '';

  function syncControls() {
    var v = el.video;
    el.play.setAttribute('aria-label', v.paused ? 'Play video' : 'Pause video');
    el.play.innerHTML = v.paused ? ICON.play : ICON.pause;
    el.mute.setAttribute('aria-label', v.muted ? 'Unmute video' : 'Mute video');
    el.mute.innerHTML = v.muted ? ICON.muted : ICON.volume;
  }
  var ICON = {
    play: svg('M8 5v14l11-7z'),
    pause: svg('M6 5h4v14H6zM14 5h4v14h-4z'),
    muted: svg('M3 9v6h4l5 5V4L7 9H3zm13.6 3 2.7-2.7-1.4-1.4-2.7 2.7-2.7-2.7-1.4 1.4 2.7 2.7-2.7 2.7 1.4 1.4 2.7-2.7 2.7 2.7 1.4-1.4z'),
    volume: svg('M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1a7 7 0 0 1 0 13.4v2.1a9 9 0 0 0 0-17.6z')
  };
  function svg(d) {
    return '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="' + d + '"/></svg>';
  }

  function wireVideo() {
    var v = el.video;
    v.addEventListener('playing', function () { setStream(null); syncControls(); resumeIfSaved(); });
    v.addEventListener('pause', syncControls);
    v.addEventListener('play', syncControls);
    v.addEventListener('volumechange', syncControls);
    v.addEventListener('loadedmetadata', resumeIfSaved);
    el.play.addEventListener('click', function () {
      if (v.paused) { v.play().catch(function () { /* nothing loaded yet */ }); } else { v.pause(); }
      syncControls();
    });
    el.mute.addEventListener('click', function () { v.muted = !v.muted; syncControls(); });
    el.live.addEventListener('click', function () {
      if (hls && hls.liveSyncPosition) { v.currentTime = hls.liveSyncPosition; }
      else if (v.seekable && v.seekable.length) { v.currentTime = v.seekable.end(v.seekable.length - 1); }
      v.play().catch(function () { /* nothing loaded yet */ });
    });
    el.full.addEventListener('click', function () {
      var target = el.shell;
      if (document.fullscreenElement) { document.exitFullscreen(); }
      else if (target.requestFullscreen) { target.requestFullscreen().catch(function () { /* not allowed */ }); }
      else if (v.webkitEnterFullscreen) { v.webkitEnterFullscreen(); }
    });
    document.addEventListener('fullscreenchange', function () {
      el.full.setAttribute('aria-label', document.fullscreenElement ? 'Exit full screen' : 'Enter full screen');
    });
  }

  /* FR-4.4: after login the viewer lands back on the exact playback timestamp. */
  function rememberPosition() {
    store.set(KEYS.resume, JSON.stringify({ path: location.pathname, t: el.video.currentTime || 0 }));
  }
  function resumeIfSaved() {
    var raw = store.get(KEYS.resume);
    if (!raw || !memberToken()) { return; }
    var saved = null;
    try { saved = JSON.parse(raw); } catch (e) { saved = null; }
    if (!saved || saved.path !== location.pathname) { return; }
    if (saved.t > 0 && el.video.readyState > 0) {
      try { el.video.currentTime = saved.t; } catch (e) { /* not seekable yet */ }
      store.del(KEYS.resume);
    }
  }

  /* ---------- merch and cart ---------- */
  var product = boot.product || null;

  /* Same rule as the server (service.cart_variant): size L when the product has it, else its first variant. */
  function normalizeProduct(p) {
    if (p.cart_variant_id) { return p; }
    var variants = p.variants || [];
    var pick = variants.filter(function (v) { return String(v.size || '').toUpperCase() === 'L'; })[0] || variants[0];
    var out = {};
    Object.keys(p).forEach(function (k) { out[k] = p[k]; });
    out.cart_variant_id = pick ? pick.variant_id : null;
    out.cart_variant_size = pick ? pick.size : null;
    out.price_label = '';
    if (pick && typeof pick.price_minor === 'number') {
      var major = pick.price_minor / 100;
      out.price_label = (pick.currency || 'PKR') + ' ' + (pick.price_minor % 100 === 0 ?
        major.toLocaleString('en-US') : major.toLocaleString('en-US', { minimumFractionDigits: 2 }));
    }
    return out;
  }

  function showProduct(p) {
    if (!p) { return; }
    product = p;
    el.merch.setAttribute('data-product-id', p.product_id || '');
    el.merch.setAttribute('data-variant-id', p.cart_variant_id || '');
    el.merchName.textContent = p.name || p.product_id;
    el.merchVariant.textContent = p.cart_variant_size ? 'Size ' + p.cart_variant_size : '';
    el.merchPrice.textContent = p.price_label || '';
    el.merchSku.textContent = (p.product_id || '') + ' / ' + (p.cart_variant_id || '');
    el.add.disabled = !p.cart_variant_id;
  }

  function setCount(n) {
    var value = Math.max(0, parseInt(n, 10) || 0);
    el.count.textContent = String(value);
    store.set(KEYS.count, String(value));
  }
  function countOf(data) {
    if (!data) { return null; }
    if (typeof data.count === 'number') { return data.count; }
    if (typeof data.total_items === 'number') { return data.total_items; }
    var items = data.items || (data.cart && data.cart.items);
    if (Array.isArray(items)) {
      return items.reduce(function (n, it) { return n + (parseInt(it.quantity, 10) || 0); }, 0);
    }
    return null;
  }
  function localCart() {
    try { return JSON.parse(store.get(KEYS.local) || '{}') || {}; } catch (e) { return {}; }
  }
  /* Server cart unreachable: stage the item in this tab and hand it to the server later. */
  function stageLocally(variantId, qty) {
    var cart = localCart();
    cart[variantId] = (cart[variantId] || 0) + qty;
    store.set(KEYS.local, JSON.stringify(cart));
    return (parseInt(el.count.textContent, 10) || 0) + qty;
  }

  function addToGuestCart(variantId, qty, retried) {
    return guestToken().then(function (tok) {
      if (!tok) { return { count: stageLocally(variantId, qty) }; }
      return api('POST', '/api/v1/guest/cart/items', { variant_id: variantId, quantity: qty }, tok).then(function (r) {
        if (r.ok) {
          var c = countOf(r.data);
          return { count: c === null ? (parseInt(el.count.textContent, 10) || 0) + qty : c, token: tok };
        }
        if ((r.status === 401 || r.status === 403 || r.status === 409) && !retried && errorCode(r) !== 'OUT_OF_STOCK') {
          dropGuestToken();
          return addToGuestCart(variantId, qty, true);
        }
        if (r.status === 409 || r.status === 400 || r.status === 404 && errorCode(r) === 'VARIANT_NOT_FOUND') {
          return { error: errorCode(r) === 'OUT_OF_STOCK' ? 'That size is sold out.' : 'Could not add this item.' };
        }
        return { count: stageLocally(variantId, qty) };
      });
    });
  }

  /* Members keep using the guest staging cart; each add is merged straight into their account cart. */
  function addToCart(variantId) {
    return addToGuestCart(variantId, 1, false).then(function (res) {
      if (res.error || !memberToken() || !res.token) { return res; }
      return mergeInto(memberToken(), res.token).then(function (merged) {
        return merged === null ? res : { count: merged };
      });
    });
  }

  function wireCart() {
    var saved = store.get(KEYS.count);
    if (saved !== null) { setCount(saved); }
    var tok = store.get(KEYS.guest);
    if (memberToken()) {
      memberCartCount().then(function (c) { if (c !== null) { setCount(c); } });
    } else if (tok) {
      api('GET', '/api/v1/guest/cart', undefined, tok).then(function (r) {
        var c = r.ok ? countOf(r.data) : null;
        if (c !== null) { setCount(c + Object.keys(localCart()).reduce(function (n, k) { return n + localCart()[k]; }, 0)); }
      });
    }
    el.add.addEventListener('click', function () {
      var variantId = product && product.cart_variant_id;
      if (!variantId) { return; }
      el.add.disabled = true;
      el.cartNote.textContent = 'Adding to your cart...';
      addToCart(variantId).then(function (res) {
        if (res.error) { el.cartNote.textContent = res.error; return; }
        setCount(res.count);
        el.cartNote.textContent = 'Added to your cart.';
      }).then(function () { el.add.disabled = false; }, function () { el.add.disabled = false; });
    });
    el.checkout.addEventListener('click', function () {
      if (!memberToken()) { openModal({ kind: 'checkout' }); return; }
      el.cartNote.textContent = 'Your cart is saved to your account.';
    });
  }

  /* ---------- chat ---------- */
  var seen = {};
  function messageKey(m) {
    if (m.message_id) { return 'id:' + m.message_id; }
    if (m.sequence !== undefined && m.sequence !== null) { return 'seq:' + m.sequence; }
    return null;
  }
  function appendMessage(m) {
    if (!m || typeof m.text !== 'string') { return; }
    var key = messageKey(m);
    if (key && seen[key]) { return; }
    if (key) { seen[key] = true; }
    var li = document.createElement('li');
    if (m.sequence !== undefined && m.sequence !== null) { li.setAttribute('data-seq', String(m.sequence)); }
    var who = document.createElement('span');
    who.className = 'chat-sender';
    who.textContent = m.sender_name || m.sender || 'fan';
    var text = document.createElement('span');
    text.className = 'chat-text';
    text.textContent = m.text;
    li.appendChild(who);
    li.appendChild(text);
    if (m.flagged) {
      var flag = document.createElement('span');
      flag.className = 'chat-flag';
      flag.textContent = 'moderated';
      li.appendChild(flag);
    }
    var list = el.chatList;
    var seq = Number(m.sequence);
    var before = null;
    if (!isNaN(seq)) {
      for (var i = list.children.length - 1; i >= 0; i--) {
        var other = Number(list.children[i].getAttribute('data-seq'));
        if (!isNaN(other) && other > seq) { before = list.children[i]; } else { break; }
      }
    }
    var atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    list.insertBefore(li, before);
    while (list.children.length > 200) { list.removeChild(list.firstChild); }
    if (atBottom) { list.scrollTop = list.scrollHeight; }
  }
  function messagesOf(data) {
    if (!data) { return []; }
    if (Array.isArray(data)) { return data; }
    return Array.isArray(data.messages) ? data.messages : [];
  }

  function loadHistory() {
    var path = '/api/v1/chat/' + encodeURIComponent(boot.chat_room_id) + '/messages';
    var tok = memberToken();
    var first = tok ? Promise.resolve(tok) : guestToken();
    return first.then(function (t) { return api('GET', path, undefined, t); }).then(function (r) {
      if (!r.ok && r.status === 401) { return api('GET', path); }
      return r;
    }).then(function (r) {
      if (r.ok) { messagesOf(r.data).forEach(appendMessage); }
    });
  }

  function watchChat() {
    liveSocket('/api/v1/ws?topic=' + encodeURIComponent('chat:' + boot.chat_room_id), 'chat-socket', function (msg) {
      if (msg.type === 'chat_message') { appendMessage(msg.message); }
    }, loadHistory);
  }

  function sendChat(text) {
    var member = memberToken();
    var tokenFor = member ? Promise.resolve(member) : guestToken();
    el.chatNote.textContent = '';
    return tokenFor.then(function (tok) {
      return api('POST', '/api/v1/chat', { chat_room_id: boot.chat_room_id, text: text }, tok);
    }).then(function (r) {
      if (r.ok) {
        el.chatInput.value = '';
        var d = r.data || {};
        if (d.blocked) { el.chatNote.textContent = 'Message held by moderation.'; return; }
        if (d.message) { appendMessage(d.message); }
        return;
      }
      if (!member || r.status === 401) {
        /* Guest (or expired login): the server refused the write, so ask to log in and keep the draft. */
        if (member) { store.del(KEYS.member); showViewer(); }
        openModal({ kind: 'chat', text: text });
        return;
      }
      el.chatNote.textContent = errorCode(r) === 'GUEST_WRITE_FORBIDDEN' ? 'Log in to chat.' :
        (r.status === 429 || errorCode(r) === 'RATE_LIMITED' ? 'Slow down a little.' : 'Message not sent. Try again.');
    });
  }

  function wireChat() {
    el.chatForm.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var text = el.chatInput.value.trim();
      if (!text) { el.chatInput.focus(); return; }
      sendChat(text);
    });
  }

  /* ---------- progressive login ---------- */
  var pending = null;
  var lastFocus = null;

  function openModal(action) {
    pending = action || null;
    rememberPosition();
    lastFocus = document.activeElement;
    el.authError.textContent = '';
    el.modal.hidden = false;
    setTimeout(function () { el.email.focus(); }, 0);
  }
  function closeModal() {
    el.modal.hidden = true;
    if (lastFocus && lastFocus.focus) { lastFocus.focus(); }
  }
  function focusables() {
    return Array.prototype.filter.call(el.modal.querySelectorAll('input, button'), function (n) { return !n.disabled; });
  }

  function mergeInto(member, tok) {
    var local = localCart();
    var pushLocal = Object.keys(local).reduce(function (p, variantId) {
      return p.then(function () {
        return api('POST', '/api/v1/guest/cart/items', { variant_id: variantId, quantity: local[variantId] }, tok);
      });
    }, Promise.resolve());
    return pushLocal.then(function () {
      return api('POST', '/api/v1/guest/merge', { guest_token: tok }, member);
    }).then(function (r) {
      if (!r.ok) { return null; }
      store.del(KEYS.local);
      dropGuestToken();
      return countOf(r.data);
    });
  }

  function memberCartCount() {
    return api('GET', '/api/v1/cart', undefined, memberToken()).then(function (r) { return r.ok ? countOf(r.data) : null; });
  }

  function afterLogin() {
    var guest = store.get(KEYS.guest);
    var merged = (guest ? mergeInto(memberToken(), guest) : Promise.resolve(null)).then(function (count) {
      return count === null ? memberCartCount() : count;
    });
    return merged.then(function (count) {
      if (count !== null && count !== undefined) { setCount(count); }
      closeModal();
      showViewer();
      resumeIfSaved();
      var action = pending;
      pending = null;
      if (action && action.kind === 'chat' && action.text) { sendChat(action.text); }
      else if (action && action.kind === 'checkout') { el.cartNote.textContent = 'Your cart is saved to your account.'; }
    });
  }

  function wireModal() {
    el.authForm.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var email = el.email.value.trim();
      var password = el.password.value;
      if (!email || !password) { el.authError.textContent = 'Enter your email and password.'; return; }
      el.authSubmit.disabled = true;
      el.authError.textContent = '';
      api('POST', '/api/v1/auth/login', { email: email, password: password }).then(function (r) {
        if (!r.ok || !r.data || !r.data.token) {
          el.authError.textContent = r.status === 401 || r.status === 400 ? 'Email or password is incorrect.' :
            'Could not log in right now. Try again.';
          return null;
        }
        store.set(KEYS.member, r.data.token);
        var user = r.data.user || {};
        store.set(KEYS.name, user.display_name || user.email || email);
        el.password.value = '';
        return afterLogin();
      }).then(function () { el.authSubmit.disabled = false; }, function () { el.authSubmit.disabled = false; });
    });
    el.authCancel.addEventListener('click', function () { pending = null; closeModal(); });
    el.modal.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') { ev.preventDefault(); pending = null; closeModal(); return; }
      if (ev.key !== 'Tab') { return; }
      var nodes = focusables();
      if (!nodes.length) { return; }
      var first = nodes[0];
      var last = nodes[nodes.length - 1];
      if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
    });
    el.modal.addEventListener('click', function (ev) { if (ev.target === el.modal) { pending = null; closeModal(); } });
  }

  /* ---------- start ---------- */
  showViewer();
  wireVideo();
  syncControls();
  wireCart();
  wireChat();
  wireModal();
  if (product) { showProduct(product); }
  if (boot.playback_url) { startPlayback(boot.playback_url); }
  watchMatch(boot.match_id);
  watchChat();
  if (!memberToken()) { guestToken(); }
  /* No stream yet: look for one every few seconds without reloading the page. */
  setInterval(function () { if (!currentUrl || document.body.getAttribute('data-score-socket') !== 'open') { refreshOverlay(); } }, 5000);
})();
