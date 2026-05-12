const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');

const {
  createState,
  rememberOtpFromText,
  createServer,
} = require('./whatsapp_otp_middleware');

function requestJson(port, path, options = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        host: '127.0.0.1',
        port,
        path,
        method: options.method || 'GET',
        headers: options.headers || {},
      },
      (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          const body = Buffer.concat(chunks).toString('utf8');
          resolve({
            statusCode: res.statusCode,
            body,
            json: body ? JSON.parse(body) : null,
          });
        });
      },
    );
    req.on('error', reject);
    if (options.body) {
      req.write(options.body);
    }
    req.end();
  });
}

test('rememberOtpFromText stores OTP metadata and latest route returns JSON or text', async () => {
  const state = createState();
  const item = rememberOtpFromText(state, 'Kode verifikasi GoPay Anda 123456', {
    from: 'gopay',
    source: 'manual-test',
    ts: 1710000000,
  });

  assert.equal(item.otp, '123456');
  assert.equal(state.latest.otp, '123456');

  const server = createServer(state);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;

  try {
    const health = await requestJson(port, '/healthz');
    assert.equal(health.statusCode, 200);
    assert.equal(health.json.ok, true);

    const latest = await requestJson(port, '/latest');
    assert.equal(latest.statusCode, 200);
    assert.equal(latest.json.otp, '123456');
    assert.equal(latest.json.source, 'manual-test');
    assert.equal(latest.json.ts, 1710000000);

    const latestText = await requestJson(port, '/latest?format=text');
    assert.equal(latestText.statusCode, 200);
    assert.equal(latestText.body, '123456');

    const stale = await requestJson(port, '/latest?since=1710000001');
    assert.equal(stale.statusCode, 204);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test('ingest endpoint updates latest OTP without requiring WhatsApp client runtime', async () => {
  const state = createState();
  const server = createServer(state);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;

  try {
    const ingest = await requestJson(port, '/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: 'wa-webhook',
        text: 'OTP GoPay: 654321',
        ts: 1710000002,
      }),
    });
    assert.equal(ingest.statusCode, 200);
    assert.equal(ingest.json.ok, true);
    assert.equal(ingest.json.stored, 1);

    const latest = await requestJson(port, '/latest');
    assert.equal(latest.statusCode, 200);
    assert.equal(latest.json.otp, '654321');
    assert.equal(latest.json.from, 'wa-webhook');
    assert.equal(latest.json.ts, 1710000002);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
