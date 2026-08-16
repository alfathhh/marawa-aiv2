/**
 * Pure message normalization (Baileys event -> MARAWA webhook contract).
 * No I/O here on purpose so it can be unit-tested without a socket.
 */
import { createHmac } from 'node:crypto';

export function normalizeMessage(event) {
  // event: Baileys 'messages.upsert' payload: { type, messages: [...] }
  const msg = event?.messages?.[0];
  if (!msg) return null;
  const key = msg.key ?? {};

  // Out of scope: status updates (protocolMessage) and group traffic.
  if (msg.message?.protocolMessage) return null;
  if (key.remoteJid?.endsWith('@g.us')) return null;

  const text = extractText(msg.message);
  if (text === null) return null; // non-text content (images, audio, ...)

  return {
    conversation_id: key.remoteJid,
    wa_message_id: key.id || null,
    from_me: !!key.fromMe,
    body: collapse(text),
    timestamp: msg.messageTimestamp
      ? new Date(Number(msg.messageTimestamp) * 1000).toISOString()
      : new Date().toISOString(),
  };
}

export function extractText(message) {
  if (!message) return null;
  if (message.conversation) return message.conversation;
  if (message.extendedTextMessage?.text) return message.extendedTextMessage.text;
  if (message.imageMessage?.caption) return message.imageMessage.caption;
  return null;
}

/** Collapse whitespace/newlines but keep the message readable. */
export function collapse(text) {
  return (text || '').replace(/\s+/g, ' ').trim();
}

/** HMAC-SHA256 hex signature for the webhook (matches app.py /webhook/whatsapp). */
export function signWebhook(secret, rawBody) {
  return createHmac('sha256', secret).update(rawBody).digest('hex');
}
