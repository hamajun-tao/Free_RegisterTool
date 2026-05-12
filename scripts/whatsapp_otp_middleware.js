const http = require('node:http');
const { URL } = require('node:url');
const path = require('node:path');

const DEFAULT_HOST = process.env.WA_OTP_HOST || '127.0.0.1';
const DEFAULT_PORT = Number.parseInt(process.env.WA_OTP_PORT || '8765', 10);
const DEFAULT_HISTORY_LIMIT = Number.parseInt(process.env.WA_OTP_HISTORY || '20', 10);
const OTP_REGEX = /\b\d{4,6}\b/;
const KEYWORD_REGEX = /(gopay|otp|kode|verification|code)/i;

function nowTs() {
  return Math.floor(Date.now() / 1000);
}

function safeJsonParse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function createState({ historyLimit = DEFAULT_HISTORY_LIMIT } = {}) {
  return {
    latest: null,
    history: [],
    historyLimit: Math.max(1, Number(historyLimit) || DEFAULT_HISTORY_LIMIT),
  };
}

function normalizeOtpRecord(otp, meta = {}, text = '') {
  return {
    otp,
    ts: Number(meta.ts) || nowTs(),
    from: meta.from || '',
    source: meta.source || 'unknown',
    text: String(text || '').slice(0, 500),
  };
}

function pushOtpRecord(state, record) {
  state.latest = record;
  state.history.push(record);
  if (state.history.length > state.historyLimit) {
    state.history = state.history.slice(-state.historyLimit);
  }
  return record;
}

function extractOtpFromText(text) {
  const normalized = String(text || '').trim();
  if (!normalized) {
    return '';
  }
  if (!KEYWORD_REGEX.test(normalized)) {
    return '';
  }
  const match = normalized.match(OTP_REGEX);
  return match ? match[0] : '';
}

function rememberOtpFromText(state, text, meta = {}) {
  const otp = extractOtpFromText(text);
  if (!otp) {
    return null;
  }
  return pushOtpRecord(state, normalizeOtpRecord(otp, meta, text));
}

function collectPayloadCandidates(payload) {
  if (payload == null) {
    return [];
  }
  if (typeof payload === 'string') {
    return [{ text: payload }];
  }
  if (Array.isArray(payload)) {
    return payload.flatMap((item) => collectPayloadCandidates(item));
  }
  if (typeof payload === 'object') {
    const directText =
      payload.text ??
      payload.body ??
      payload.message ??
      payload.content ??
      payload.code ??
      payload.otp;
    const meta = {
      from: payload.from || payload.sender || payload.wa_id || '',
      source: payload.source || 'ingest',
      ts: payload.ts || payload.timestamp || payload.time || payload.received_at || payload.created_at,
    };
    const candidates = [];
    if (directText != null) {
      if (typeof directText === 'object') {
        candidates.push(...collectPayloadCandidates({ ...directText, ...meta }));
      } else {
        candidates.push({ text: String(directText), ...meta });
      }
    }
    if (Array.isArray(payload.entry)) {
      for (const entry of payload.entry) {
        for (const change of (entry && entry.changes) || []) {
          const value = (change && change.value) || {};
          for (const msg of value.messages || []) {
            const text =
              (msg.text && msg.text.body) ||
              (msg.button && msg.button.text) ||
              JSON.stringify(msg.interactive || msg, null, 0);
            candidates.push({
              text,
              from: msg.from || meta.from,
              source: 'whatsapp_cloud_api',
              ts: msg.timestamp || meta.ts,
            });
          }
        }
      }
    }
    if (candidates.length > 0) {
      return candidates;
    }
  }
  return [];
}

function rememberOtpFromPayload(state, payload, meta = {}) {
  const candidates = collectPayloadCandidates(payload);
  let stored = 0;
  for (const candidate of candidates) {
    const item = rememberOtpFromText(state, candidate.text, {
      from: candidate.from || meta.from,
      source: candidate.source || meta.source || 'ingest',
      ts: candidate.ts || meta.ts,
    });
    if (item) {
      stored += 1;
    }
  }
  return stored;
}

function writeJson(res, statusCode, body) {
  const raw = Buffer.from(JSON.stringify(body), 'utf8');
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': raw.length,
  });
  res.end(raw);
}

function writeText(res, statusCode, body) {
  const raw = Buffer.from(String(body), 'utf8');
  res.writeHead(statusCode, {
    'Content-Type': 'text/plain; charset=utf-8',
    'Content-Length': raw.length,
  });
  res.end(raw);
}

function createServer(state = createState()) {
  return http.createServer((req, res) => {
    const url = new URL(req.url || '/', 'http://127.0.0.1');
    if (req.method === 'GET' && (url.pathname === '/healthz' || url.pathname === '/health')) {
      writeJson(res, 200, {
        ok: true,
        latestTs: state.latest ? state.latest.ts : null,
        hasOtp: Boolean(state.latest),
      });
      return;
    }

    if (req.method === 'GET' && url.pathname === '/latest') {
      const since = Number(url.searchParams.get('since') || '0');
      if (!state.latest || (since && Number(state.latest.ts || 0) < since)) {
        res.writeHead(204);
        res.end();
        return;
      }
      const format = (url.searchParams.get('format') || '').toLowerCase();
      if (format === 'text' || format === 'plain') {
        writeText(res, 200, state.latest.otp);
        return;
      }
      writeJson(res, 200, state.latest);
      return;
    }

    if (req.method === 'POST' && (url.pathname === '/ingest' || url.pathname === '/webhook' || url.pathname === '/')) {
      const chunks = [];
      req.on('data', (chunk) => chunks.push(chunk));
      req.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        const payload = safeJsonParse(text) ?? text;
        const stored = rememberOtpFromPayload(state, payload, { source: 'ingest' });
        writeJson(res, 200, { ok: true, stored });
      });
      return;
    }

    writeJson(res, 404, { ok: false, error: 'not found' });
  });
}

function startWhatsAppClient(state, options = {}) {
  let clientLib;
  let qrcode;
  try {
    clientLib = require('whatsapp-web.js');
    qrcode = require('qrcode-terminal');
  } catch (error) {
    console.warn(`[wa-otp] WhatsApp client dependencies unavailable, relay-only mode enabled: ${error.message}`);
    return null;
  }

  const { Client, LocalAuth } = clientLib;
  const authPath = options.authPath || path.join(__dirname, '.wwebjs_auth');
  const client = new Client({
    authStrategy: new LocalAuth({
      clientId: options.clientId || 'gopay-otp',
      dataPath: authPath,
    }),
    puppeteer: {
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    },
  });

  client.on('qr', (qrText) => {
    console.log('Please scan the QR code with WhatsApp to link the OTP relay:');
    qrcode.generate(qrText, { small: true });
  });

  client.on('ready', () => {
    console.log('WhatsApp client is ready and listening for GoPay OTP messages.');
  });

  client.on('message', async (msg) => {
    const text = msg && typeof msg.body === 'string' ? msg.body : '';
    const item = rememberOtpFromText(state, text, {
      from: msg && msg.from ? msg.from : '',
      source: 'whatsapp-web',
      ts: nowTs(),
    });
    if (item) {
      console.log(`[wa-otp] captured GoPay OTP ${item.otp} from ${item.from || 'unknown sender'}`);
    }
  });

  client.initialize().catch((error) => {
    console.error(`[wa-otp] failed to initialize WhatsApp client: ${error.message}`);
  });

  return client;
}

function parseArgs(argv) {
  const args = {
    host: DEFAULT_HOST,
    port: DEFAULT_PORT,
    noWhatsapp: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === '--host' && argv[index + 1]) {
      args.host = argv[index + 1];
      index += 1;
    } else if (value === '--port' && argv[index + 1]) {
      args.port = Number.parseInt(argv[index + 1], 10) || DEFAULT_PORT;
      index += 1;
    } else if (value === '--no-whatsapp') {
      args.noWhatsapp = true;
    }
  }
  return args;
}

function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const state = createState();
  const server = createServer(state);

  server.listen(args.port, args.host, () => {
    console.log(`[wa-otp] relay listening at http://${args.host}:${args.port}`);
    console.log(`[wa-otp] latest OTP endpoint: http://${args.host}:${args.port}/latest`);
  });

  if (!args.noWhatsapp) {
    startWhatsAppClient(state);
  } else {
    console.log('[wa-otp] running in relay-only mode; POST OTP payloads to /ingest');
  }

  return { state, server };
}

module.exports = {
  createState,
  extractOtpFromText,
  rememberOtpFromText,
  rememberOtpFromPayload,
  createServer,
  startWhatsAppClient,
  parseArgs,
  main,
};

if (require.main === module) {
  main();
}
