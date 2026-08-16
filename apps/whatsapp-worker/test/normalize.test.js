import { test } from 'node:test';
import assert from 'node:assert/strict';

import { normalizeMessage, extractText, collapse, signWebhook } from '../src/normalize.js';

test('normalizes a plain text message', () => {
  const out = normalizeMessage({
    type: 'notify',
    messages: [{
      key: { remoteJid: '6281234567890@s.whatsapp.net', id: 'ABC123', fromMe: false },
      message: { conversation: 'berapa  jumlah  penduduk?' },
      messageTimestamp: 1700000000,
    }],
  });
  assert.equal(out.conversation_id, '6281234567890@s.whatsapp.net');
  assert.equal(out.wa_message_id, 'ABC123');
  assert.equal(out.from_me, false);
  assert.equal(out.body, 'berapa jumlah penduduk?'); // whitespace collapsed
  assert.equal(out.timestamp, new Date(1700000000 * 1000).toISOString());
});

test('skips non-text content (image without caption)', () => {
  const out = normalizeMessage({
    type: 'notify',
    messages: [{
      key: { remoteJid: 'x@s.whatsapp.net', id: 'I1', fromMe: false },
      message: { imageMessage: { url: 'http://x' } },
    }],
  });
  assert.equal(out, null);
});

test('skips group traffic', () => {
  const out = normalizeMessage({
    type: 'notify',
    messages: [{
      key: { remoteJid: '123-456@g.us', id: 'G1', fromMe: false },
      message: { conversation: 'hi' },
    }],
  });
  assert.equal(out, null);
});

test('skips protocol status messages', () => {
  const out = normalizeMessage({
    type: 'notify',
    messages: [{
      key: { remoteJid: 'x@s.whatsapp.net', id: 'P1', fromMe: false },
      message: { protocolMessage: { type: 0 } },
    }],
  });
  assert.equal(out, null);
});

test('extracts extended text and caption', () => {
  assert.equal(extractText({ extendedTextMessage: { text: '  halo  ' } }), '  halo  ');
  assert.equal(extractText({ imageMessage: { caption: 'foto: data' } }), 'foto: data');
  assert.equal(extractText({}), null);
});

test('signWebhook matches deterministic hex', () => {
  const sig = signWebhook('secret', '{"a":1}');
  assert.equal(sig.length, 64);
  assert.match(sig, /^[0-9a-f]{64}$/);
});

test('collapse trims and squeezes whitespace', () => {
  assert.equal(collapse(' a\n\n b\t c '), 'a b c');
  assert.equal(collapse(''), '');
});
