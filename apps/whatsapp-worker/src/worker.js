/**
 * MARAWA WhatsApp worker — Baileys adapter.
 *
 * Division of labour (docs/07): this worker NEVER holds business logic.
 * It only: pairs (QR), normalizes inbound, pushes to the API webhook, claims
 * outbox, sends, reports status. All decisions live in the API/store.
 *
 * Env:
 *   MARAWA_API_URL        base URL of the API (default http://127.0.0.1:8130)
 *   MARAWA_WEBHOOK_SECRET HMAC secret for /webhook/whatsapp
 *   MARAWA_INTERNAL_KEY   key for /internal/outbox/*
 *   MARAWA_CRED_DIR       where Baileys auth state is stored (default ./auth)
 */
import { createHmac } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import makeWASocket, { useMultiFileAuthState, DisconnectReason } from '@whiskeysockets/baileys';
import qrcode from 'qrcode-terminal';

import { normalizeMessage, signWebhook } from './normalize.js';

const API = process.env.MARAWA_API_URL || 'http://127.0.0.1:8130';
const CRED_DIR = process.env.MARAWA_CRED_DIR || path.join(process.cwd(), 'auth');

async function apiPost(route, body, extra = {}) {
  const raw = JSON.stringify(body);
  const res = await fetch(`${API}${route}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Marawa-Signature': signWebhook(process.env.MARAWA_WEBHOOK_SECRET || '', raw),
      'X-Internal-Key': process.env.MARAWA_INTERNAL_KEY || '',
      ...extra,
    },
    body: raw,
  });
  if (!res.ok) throw new Error(`API ${route} -> ${res.status}`);
  return res.json();
}

async function claimAndSend(sock) {
  let payload;
  try {
    payload = await apiPost('/internal/outbox/claim?worker_id=whatsapp-1&limit=10', {});
  } catch (err) {
    return; // API offline; next tick
  }
  for (const record of payload.records || []) {
    // Authorization is control-plane I/O. If it is temporarily unavailable,
    // leave the row claimed so the lease can expire and retry; never turn an
    // API outage into a permanent delivery failure.
    let gate;
    try {
      gate = await apiPost('/internal/outbox/authorize', {
        outbox_id: record.outbox_id,
      });
    } catch (err) {
      continue;
    }
    if (!gate.allowed) continue;
    try {
      const jid = record.conversation_id.endsWith('@s.whatsapp.net')
        ? record.conversation_id
        : `${record.conversation_id}@s.whatsapp.net`;
      const sent = await sock.sendMessage(jid, { text: record.body });
      const waId = sent?.key?.id || null;
      await apiPost('/internal/outbox/update', {
        outbox_id: record.outbox_id,
        status: 'sent',
        wa_message_id: waId,
      });
    } catch (err) {
      await apiPost('/internal/outbox/update', {
        outbox_id: record.outbox_id,
        status: 'failed',
        error: String(err?.message || err).slice(0, 300),
      }).catch(() => {});
    }
  }
}

// Poll timer lives at module scope so reconnects (main() re-invocation via
// setTimeout) clear the previous interval instead of stacking duplicate
// polls. Without this guard, every WhatsApp reconnect adds one more
// claimAndSend loop → outbox claim hammering grows unbounded (observed ~50/s).
let pollTimer = null;

async function main() {
  mkdirSync(CRED_DIR, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(CRED_DIR);
  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
    markOnlineOnConnect: false,
  });

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      console.log('[marawa-worker] Pairing: scan QR ini dengan WhatsApp Anda:');
      qrcode.generate(qr, { small: true });
      const expires = new Date(Date.now() + 60_000).toISOString();
      apiPost('/internal/whatsapp-qr', { qr, expires_at: expires }).catch(() => {});
    }
    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = code !== DisconnectReason.loggedOut;
      console.log(`[marawa-worker] koneksi tertutup (${code}); reconnect=${shouldReconnect}`);
      apiPost('/internal/whatsapp-connection', { state: 'closed' }).catch(() => {});
      if (shouldReconnect) setTimeout(main, 3000);
      else process.exit(0);
    }
    if (connection === 'open') {
      console.log('[marawa-worker] WhatsApp terhubung.');
      apiPost('/internal/whatsapp-connection', { state: 'open' }).catch(() => {});
    }
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('messages.upsert', async (event) => {
    const normalized = normalizeMessage(event);
    if (!normalized) return;
    try {
      await apiPost('/webhook/whatsapp', normalized);
    } catch (err) {
      console.error('[marawa-worker] webhook gagal:', String(err?.message || err).slice(0, 150));
    }
  });

  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => claimAndSend(sock), 5000);
  console.log('[marawa-worker] start. PID', process.pid);
}

main().catch((err) => {
  console.error('fatal:', err);
  process.exit(1);
});
