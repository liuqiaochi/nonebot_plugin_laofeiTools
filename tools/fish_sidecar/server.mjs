#!/usr/bin/env node
// 鱼鱼找鸡 —— HTTP sidecar（零 npm 依赖，只用 node:http）
//
// 为什么要有这一层：bot 跑在 Ubuntu 虚拟机里（无 GUI、无浏览器），而
// 「浏览器直连 Google Lens」必须有一台带真实浏览器的机器（mac）。于是把
// 已经跑通的命令行能力包成极薄的 HTTP 服务，让 VM 里的 Python 插件把图片
// 字节 POST 过来，拿结构化 JSON 回去。这样：
//   - 不用把 2324 行 JS 移植成 Python，也不用给 VM 装 chromium
//   - mac 掉线时 Python 侧自动降级到 SerpApi，功能不会整体不可用
//
// 放置位置：把这个文件放到鱼鱼找鸡项目根目录（与 src/、bin/ 同级），
// 然后 `node server.mjs` 即可。
//
// 接口：
//   GET  /health            健康检查，返回引擎与浏览器配置
//   POST /find              请求体=图片字节，返回账号 JSON
//        鉴权：配置了 token 时必须带请求头 x-fish-token: <token>
//        可选：?max=3 覆盖最多返回账号数；?engines=lens,saucenao 覆盖引擎
//
// 环境变量（均可省略）：
//   FISH_HOST   监听地址，默认 0.0.0.0（VM 要能连进来，别改成 127.0.0.1）
//   FISH_PORT   监听端口，默认 8787
//   FISH_TOKEN  鉴权 token，默认取 config.json 的 token 字段，都为空则不鉴权

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  reverseSearch,
  extractAccounts,
  closeBrowser,
  engineLabels,
  ENGINE_IDS
} from './src/fish-find-chicken.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CONFIG_FILE = path.join(HERE, 'config.json');

const DEFAULTS = {
  enabled: true,
  engines: ['lens', 'saucenao', 'tracemoe'],
  timeoutMs: 45000,
  maxAccounts: 3,
  strictConsensus: true,
  proxy: '',
  browserExe: '',
  token: ''
};

const MAX_BYTES = 20 * 1024 * 1024; // 20MB，与 CLI 的 readImage 上限一致
const IDLE_CLOSE_MS = 90 * 1000;    // 空闲 90 秒关掉浏览器，别一直占着 mac 内存

function loadConfig() {
  let saved = {};
  if (fs.existsSync(CONFIG_FILE)) {
    try {
      saved = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
    } catch (error) {
      console.error(`[sidecar] config.json 读不了，用默认值：${error.message}`);
    }
  }
  return { ...DEFAULTS, ...saved };
}

/** 按文件头认格式，不信扩展名 —— 有人把 png 存成 .jpg，反查接口会直接拒。 */
function sniffMime(bytes) {
  if (bytes.length > 8 && bytes[0] === 0x89 && bytes[1] === 0x50) return 'image/png';
  if (bytes.length > 12 && bytes.slice(0, 4).toString('ascii') === 'RIFF' && bytes.slice(8, 12).toString('ascii') === 'WEBP') return 'image/webp';
  if (bytes.length > 3 && bytes[0] === 0x47 && bytes[1] === 0x49 && bytes[2] === 0x46) return 'image/gif';
  return 'image/jpeg';
}

// ── 串行化：浏览器是单例，并发请求会互相抢 ──────────────────────────────
let chain = Promise.resolve();
function serialize(job) {
  const run = chain.then(job, job);
  chain = run.then(() => {}, () => {});
  return run;
}

let idleTimer = null;
function touchIdle() {
  if (idleTimer) clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    closeBrowser().catch(() => {});
  }, IDLE_CLOSE_MS);
  if (idleTimer.unref) idleTimer.unref();
}

function send(res, status, payload) {
  const body = Buffer.from(JSON.stringify(payload), 'utf8');
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': body.length
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BYTES) {
        reject(new Error(`图片太大（上限 ${MAX_BYTES / 1024 / 1024}MB）`));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
    req.on('aborted', () => reject(new Error('请求中断')));
  });
}

async function doFind(bytes, cfg, query) {
  const engines = String(query.get('engines') ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter((id) => ENGINE_IDS.includes(id));
  const max = Number(query.get('max')) || cfg.maxAccounts || 3;
  const timeoutMs = Number(query.get('timeout')) || cfg.timeoutMs || 45000;
  const strict = query.get('strict') !== '0';

  const mimeType = sniffMime(bytes);
  const started = Date.now();

  const result = await reverseSearch(bytes, mimeType, {
    engines: engines.length ? engines : cfg.engines,
    timeoutMs,
    proxy: cfg.proxy || '',
    browserExe: cfg.browserExe || '',
    headed: false
  });

  const accounts = extractAccounts(result.candidates, {
    max,
    strictNames: strict && cfg.strictConsensus !== false
  });

  return {
    ok: true,
    elapsedMs: Date.now() - started,
    mimeType,
    bytes: bytes.length,
    enginesUsed: result.enginesUsed ?? [],
    failures: result.failures ?? [],
    candidates: result.candidates ?? [],
    accounts
  };
}

const cfg = loadConfig();
const TOKEN = String(process.env.FISH_TOKEN ?? cfg.token ?? '');
const HOST = String(process.env.FISH_HOST ?? '0.0.0.0');
const PORT = Number(process.env.FISH_PORT ?? 8787);

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);

  try {
    if (req.method === 'GET' && url.pathname === '/health') {
      send(res, 200, {
        ok: true,
        service: 'fish-find-sidecar',
        engines: cfg.engines ?? [],
        engineLabels: engineLabels(cfg.engines ?? []),
        maxAccounts: cfg.maxAccounts ?? 3,
        timeoutMs: cfg.timeoutMs ?? 45000,
        proxy: cfg.proxy || '直连',
        browserExe: cfg.browserExe || '自动查找',
        authRequired: Boolean(TOKEN)
      });
      return;
    }

    if (req.method === 'POST' && url.pathname === '/find') {
      if (TOKEN) {
        const got = String(req.headers['x-fish-token'] ?? '');
        if (got !== TOKEN) {
          send(res, 401, { ok: false, error: 'token 不对' });
          return;
        }
      }
      const bytes = await readBody(req);
      if (!bytes.length) {
        send(res, 400, { ok: false, error: '请求体是空的，把图片字节放进来' });
        return;
      }
      touchIdle();
      const payload = await serialize(() => doFind(bytes, cfg, url.searchParams));
      console.log(
        `[sidecar] ${bytes.length}B -> ${payload.accounts.length} 账号 ` +
        `(${payload.elapsedMs}ms, 引擎 ${payload.enginesUsed.join(',') || '无'})`
      );
      send(res, 200, payload);
      return;
    }

    send(res, 404, { ok: false, error: '只有 GET /health 和 POST /find' });
  } catch (error) {
    console.error(`[sidecar] 出错：${error?.message ?? error}`);
    if (!res.headersSent) {
      send(res, 502, { ok: false, error: String(error?.message ?? error) });
    }
  }
});

server.on('clientError', (_err, socket) => {
  if (socket.writable) socket.end('HTTP/1.1 400 Bad Request\r\n\r\n');
});

server.listen(PORT, HOST, () => {
  console.log(`[sidecar] 监听 http://${HOST}:${PORT}`);
  console.log(`[sidecar] 引擎 ${engineLabels(cfg.engines ?? []).join(' / ') || '（空）'}`);
  console.log(`[sidecar] 代理 ${cfg.proxy || '直连'} · 鉴权 ${TOKEN ? '开' : '关'}`);
});
