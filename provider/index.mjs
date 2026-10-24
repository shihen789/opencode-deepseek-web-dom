import { buildSystemPrompt, parseToolCalls as helperParse } from './format_helper.mjs';
import { renderTodos } from './todo_helper.mjs';
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve, relative, isAbsolute, sep } from "node:path";
import { mkdirSync, writeFileSync, readFileSync, existsSync, realpathSync, readdirSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import os from "node:os";

const MD_FENCE = "```";
const HERE = dirname(fileURLToPath(import.meta.url));
const BRIDGE = join(HERE, "call_dom.py");
const DEFAULT_PYTHON = "python";

const STATE_DIR = join(HERE, "state");
const LOGS_DIR = join(HERE, "logs");
const SESSION_MAP_PATH = join(STATE_DIR, "session_map.json");
const LOG_PATH = join(LOGS_DIR, "webdom.log");

const MAX_WALL_MS = 10 * 60 * 1000;
const READ_LIMIT = 8 * 1024;
const GLOB_LIMIT = 100;
const GLOB_DEPTH = 6;

const WEB_DOM_TOOLS = ["read", "write", "edit", "bash", "glob", "grep", "todowrite", "todoread"];
const SOFT_MSG_LIMIT = 50;
const MAX_PROMPT_CHARS = 50000;

const DS_TEXT_EXT = new Set([".txt", ".md", ".py", ".json", ".log", ".yaml", ".yml", ".js", ".ts", ".html", ".csv", ".sh", ".ps1", ".cfg", ".ini", ".xml", ".sql"]);
const EXT_BY_MIM = { "text/plain": ".txt", "text/markdown": ".md", "text/csv": ".csv", "text/javascript": ".js", "application/json": ".json", "application/xml": ".xml", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif" };

function log(...parts) {
  const line = `[${new Date().toISOString()}] ${parts.map((p) => (typeof p === "string" ? p : JSON.stringify(p))).join(" ")}\n`;
  try { mkdirSync(LOGS_DIR, { recursive: true }); writeFileSync(LOG_PATH, line, { flag: "a" }); } catch {}
}

function callPython(payload, signal) {
  if (process.env.WEB_DOM_STUB) {
    try { return Promise.resolve(JSON.parse(readFileSync(process.env.WEB_DOM_STUB, "utf8"))); } catch (e) { return Promise.reject(new Error("WEB_DOM_STUB read failed: " + String(e))); }
  }
  return new Promise((resolveP, reject) => {
    const py = spawn(payload.python || DEFAULT_PYTHON, [BRIDGE], { stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
    let out = ""; let err = ""; let settled = false; let killer = null;
    const done = (fn, v) => { if (settled) return; settled = true; if (killer) clearTimeout(killer); fn(v); };
    const budgetMs = Math.min(MAX_WALL_MS, ((Number(payload.timeout_s) || 300) + 30) * 1000);
    killer = setTimeout(() => { try { py.kill("SIGTERM"); } catch {} done(reject, new Error(`web-dom: DOM call timed out after ${Math.round(budgetMs / 1000)}s`)); }, budgetMs);
    py.stdout.on("data", (d) => (out += d.toString()));
    py.stderr.on("data", (d) => (err += d.toString()));
    py.on("error", (e) => done(reject, e));
    py.on("close", (code) => {
      if (signal?.aborted) { done(reject, new Error("web-dom: aborted")); return; }
      if (code !== 0) { done(reject, new Error(`call_dom.py exit ${code}: ${err.slice(0, 800)}`)); return; }
      try {
        const r = JSON.parse(out);
        if (!r.ok) { done(reject, new Error(r.error || "DOM call failed")); return; }
        done(resolveP, r);
      } catch { done(reject, new Error(`call_dom.py bad json: out=${out.slice(0, 300)} err=${err.slice(0, 300)}`)); }
    });
    signal?.addEventListener("abort", () => { try { py.kill("SIGTERM"); } catch {} }, { once: true });
    py.stdin.write(JSON.stringify(payload));
    py.stdin.end();
  });
}

let _domLock = Promise.resolve();
function withDomLock(fn) {
  const prev = _domLock;
  let release;
  _domLock = new Promise((r) => { release = r; });
  return prev.then(async () => { try { return await fn(); } finally { release(); } });
}

function extOf(mediaType, name) {
  if (name) { const m = /\.[A-Za-z0-9]+$/.exec(name); if (m) return m[0].toLowerCase(); }
  return EXT_BY_MIM[mediaType] || (mediaType && mediaType.startsWith("image/") ? ".png" : ".txt");
}

async function materializeFilePart(part) {
  let data = part.data;
  if (data instanceof URL) data = data.href;
  if (typeof data === "string" && /^https?:/i.test(data)) { return { skip: `[跳过不支持的远程附件URL: ${data}]` }; }
  const mediaType = part.mediaType || "application/octet-stream";
  const ext = extOf(mediaType, part.filename);
  let buf;
  if (typeof data === "string") {
    const b64 = data.startsWith("data:") ? data.slice(data.indexOf(",") + 1) : data;
    buf = Buffer.from(b64, "base64");
  } else { buf = Buffer.from(data); }
  const dir = join(os.tmpdir(), "opencode-web-dom");
  mkdirSync(dir, { recursive: true });
  const h = createHash("sha1").update(buf).digest("hex").slice(0, 10);
  const fp = join(dir, `${Date.now()}-${h}${ext}`);
  writeFileSync(fp, buf);
  return { path: fp, ext, mediaType, buf };
}

function loadSessions() { try { const parsed = JSON.parse(readFileSync(SESSION_MAP_PATH, "utf8")); return parsed && typeof parsed === "object" ? parsed : {}; } catch { return {}; } }
function saveSessions(entry) {
  try {
    mkdirSync(STATE_DIR, { recursive: true });
    let onDisk = {};
    try { onDisk = JSON.parse(readFileSync(SESSION_MAP_PATH, "utf8")) || {}; } catch {}
    Object.assign(onDisk, entry || {});
    writeFileSync(SESSION_MAP_PATH, JSON.stringify(onDisk, null, 2));
  } catch (e) { log("saveSessions failed:", String(e)); }
}
function sha1(s) { return "sha1:" + createHash("sha1").update(String(s)).digest("hex"); }

function repairJson(s) {
  let t = String(s).replace(/\\([\s\S])|\\$/g, (m, c) => c === undefined ? "\\\\" : '"\\/bfnrtu'.includes(c) ? m : "\\\\" + c);
  t = t.replace(/,\s*([}\]])/g, "$1");
  return t;
}
function safeJsonParse(s) { const raw = String(s).trim(); try { return JSON.parse(raw); } catch {} try { return JSON.parse(repairJson(raw)); } catch { return null; } }

function stripToolBlocks(s) {
  let t = String(s || "").replace(/json\s*复制\s*下载/gi, '');
  t = t.replace(/```(?:json|JSON)?\s*\n?([\s\S]*?)\n?\s*```/gi, (match, inner) => (inner.includes('"tool"') || inner.includes('"tool_calls"')) ? '' : match);
  t = t.replace(/\{\s*"tool":[\s\S]*?\}/g, '').replace(/\{\s*"tool_calls":[\s\S]*?\}/g, '');
  t = t.replace(/\*\*分析用户的输入\*\*[\s\S]*/gi, '');
  return t.replace(/<tool_call>\s*[\s\S]*?\s*<\/tool_call>/gi, "").replace(/<function=\w+>[\s\S]*?<\/function>/gi, "").replace(/<parameter(?:=\w+| name="[\w-]+")>[\s\S]*?<\/parameter>/gi, "").replace(/<\|?DSML\|?[^>]*>[\s\S]*?<\/\|?DSML\|?[^>]*>/gi, "").trim();
}

const ROLE_LABEL = { system: "（系统设定）", assistant: "（你上一轮回复）", user: "", tool: "" };
function textOf(content) { if (content == null) return ""; if (typeof content === "string") return content; if (Array.isArray(content)) return content.map((p) => (p && p.type === "text" ? p.text : "")).filter(Boolean).join(""); return String(content); }
function renderAssistantToolCall(part) {
  const name = part.toolName || part.name;
  let args = part.input ?? part.args ?? part.arguments ?? {};
  if (typeof args === "string") args = safeJsonParse(args) || {};
  const callId = part.toolCallId || part.id || ('call_' + Math.random().toString(16).slice(2, 10));
  return MD_FENCE + 'json\n' + JSON.stringify({ tool_calls: [{ id: callId, type: "function", function: { name, arguments: typeof args === "string" ? args : JSON.stringify(args) } }] }) + '\n' + MD_FENCE;
}
function resultText(part) {
  const r = part.output ?? part.result;
  if (r == null) return "（空结果）";
  if (typeof r === "string") return r;
  if (typeof r === "object") {
    if (typeof r.value === "string") return r.value;
    if (r.value !== undefined) return JSON.stringify(r.value);
    if (typeof r.text === "string") return r.text;
    if (typeof r.content === "string") return r.content;
    return JSON.stringify(r);
  }
  return String(r);
}

export function renderPrompt(prompt, opts = {}) {
  const includeAssistant = opts.includeAssistant !== false;
  const out = [];
  for (const msg of prompt || []) {
    const role = msg.role;
    const parts = Array.isArray(msg.content) ? msg.content : msg.content == null ? [] : [{ type: "text", text: String(msg.content) }];
    if (role === "system") { const t = textOf(msg.content); if (t) out.push(`${ROLE_LABEL.system}\n${t}`); continue; }
    if (role === "tool") {
      for (const part of parts) {
        if (part.type === "tool-result") {
          const name = part.toolName || part.name || "tool";
          if (name === "todowrite" || name === "todoread") {
            const raw = part.output ?? part.result;
            let parsed = raw;
            if (typeof raw === "string") { try { parsed = JSON.parse(raw); } catch {} }
            out.push(`[工具 ${name} 执行结果]\n${renderTodos(parsed)}`);
          } else {
            out.push(`[工具 ${name} 执行结果]\n${resultText(part)}`);
          }
        }
      }
      continue;
    }
    if (role === "assistant") {
      if (!includeAssistant) continue;
      const segs = [];
      for (const part of parts) {
        if (part.type === "text" && part.text) segs.push(part.text);
        else if (part.type === "tool-call") segs.push(renderAssistantToolCall(part));
      }
      if (segs.length) out.push(`${ROLE_LABEL.assistant}\n${segs.join("\n")}`);
      continue;
    }
    for (const part of parts) { if (part.type === "text") out.push(part.text); }
  }
  return out.join("\n\n").trim();
}

function enforceMaxLength(text, maxLen = MAX_PROMPT_CHARS) { if (text.length <= maxLen) return text; return text.slice(-maxLen); }

function collectFileParts(prompt) {
  const files = []; const skipped = []; const inline = [];
  return (async () => {
    for (const msg of prompt || []) {
      const parts = Array.isArray(msg.content) ? msg.content : [];
      for (const part of parts) {
        if (part.type !== "file" && part.type !== "image") continue;
        const r = await materializeFilePart(part);
        if (r.skip) { skipped.push(r.skip); continue; }
        const label = part.filename || r.ext;
        
        if ((r.mediaType || "").startsWith("image/") || DS_TEXT_EXT.has(r.ext)) { 
          files.push(r.path); 
        }
        else if ((r.mediaType || "").startsWith("text/") || [".json", ".xml", ".csv", ".log"].includes(r.ext)) { 
          inline.push(`附件内容(${label}):\n${r.buf.toString("utf8")}`); 
        }
        else { 
          skipped.push(`[跳过不支持的附件类型: ${label} mediaType=${r.mediaType}]`); 
        }
      }
    }
    return { files, skipped, inline };
  })();
}

function domError(msg, statusCode = 503) { const e = new Error(msg); e.statusCode = statusCode; e.name = "APICallError"; return e; }
function estTokens(s) { return Math.max(1, Math.ceil(String(s || "").length / 3)); }
function makeUsage(inputTokens, outputTokens) { return { inputTokens: { total: inputTokens, noCache: inputTokens, cacheRead: 0, cacheWrite: 0 }, outputTokens: { total: outputTokens, text: outputTokens, reasoning: 0 } }; }
function finishReasonOf(unified, raw) { return { unified, raw }; }

export function enabledTools(options) {
  const src = Array.isArray(options?.tools) ? options.tools : [];
  const list = []; const seen = new Set();
  for (const t of src) {
    if (!t || typeof t.name !== "string") continue;
    if (!WEB_DOM_TOOLS.includes(t.name)) continue;
    if (seen.has(t.name)) continue;
    seen.add(t.name); list.push(t);
  }
  return list;
}

class DomLanguageModel {
  constructor(cfg) {
    this.specificationVersion = "v3"; this.provider = cfg.provider; this.modelId = cfg.modelId; this.supportedUrls = {};
    this._backend = cfg.backend; this._python = cfg.python; this._timeout = cfg.timeout ?? 300;
    this._workdir = cfg.workdir || process.env.WEB_DOM_WORKDIR || process.cwd();
    this._virtual = cfg.virtualTools === true;
  }

  _sessionKey(options) {
    const h = options?.headers || {};
    const sid = h["x-session-affinity"] || h["X-Session-Id"] || h["session-id"] || h["x-session-id"] || options?.providerOptions?.sessionID || options?.providerOptions?.sessionId;
    if (sid) return String(sid);
    const firstUser = (options?.prompt || []).find((m) => m.role === "user");
    return "fallback:" + sha1(renderPrompt([firstUser || {}], { includeAssistant: false })).slice(7, 23);
  }

  async _callOnce(options, tools) {
    return withDomLock(async () => {
      const skey = this._sessionKey(options);
      const sessions = loadSessions();
      let sess = sessions[skey];
      const gcur = sessions['__current__'];
      const msgs = options.prompt || [];
      const toolsUsable = tools.length > 0;
      const allowed = new Set(tools.map((t) => t.name));
      const toolsHash = sha1(tools.map((t) => t.name).sort().join(","));

      let hasLockedCurrent = false;
      let currentUuidFromMap = null;
      try {
        if (existsSync(SESSION_MAP_PATH)) {
          const raw = readFileSync(SESSION_MAP_PATH, 'utf-8');
          const sm = JSON.parse(raw);
          currentUuidFromMap = (sm['__current__'] || {}).uuid;
          hasLockedCurrent = !!(currentUuidFromMap && currentUuidFromMap !== '');
        }
      } catch (e) { log('read SESSION_MAP_PATH error:', String(e)); }

      const explicitNew = options?.providerOptions?.webdomNewSession === true || process.env.WEB_DOM_NEW_SESSION === '1';
      const ownUuid = sess && sess.uuid ? sess.uuid : null;
      const globalUuid = gcur && gcur.uuid ? gcur.uuid : null;
      
      const anchorUuid = hasLockedCurrent ? currentUuidFromMap : (explicitNew ? null : (ownUuid || globalUuid));
      const isAnchorChanged = !!(hasLockedCurrent && sess && sess.uuid && sess.uuid !== anchorUuid);

      let isGlobalLockedNoSession = false;
      if (hasLockedCurrent && !sess) {
        isGlobalLockedNoSession = true;
        sess = { uuid: currentUuidFromMap, msg_count: 0, cursor: 0, tools_hash: toolsHash, created_at: Math.floor(Date.now() / 1000) };
      }

      let degrade = false;
      if (!hasLockedCurrent && sess && sess.uuid) {
        if (sess.msg_count > SOFT_MSG_LIMIT) degrade = true;
        if ((sess.cursor || 0) > msgs.length) degrade = true;
        if (sess.tools_hash && sess.tools_hash !== toolsHash) degrade = true;
        if (sess.cursor >= 1 && sess.cursor <= msgs.length && sess.cursor_hash) {
          if (sha1(JSON.stringify(msgs[sess.cursor - 1])) !== sess.cursor_hash) degrade = true;
        }
      }

      let fresh = false;
      if (hasLockedCurrent) { fresh = isGlobalLockedNoSession || isAnchorChanged; }
      else { fresh = explicitNew || (!sess || !sess.uuid || degrade); }

      let sendText;
      let files = [];
      
      const lastUserMsg = msgs.filter(m => m.role === "user").pop();
      let collectedCurrent = { files: [], skipped: [], inline: [] };
      if (lastUserMsg) {
        collectedCurrent = await collectFileParts([lastUserMsg]);
      }

      if (fresh) {
        const spec = toolsUsable ? buildSystemPrompt(tools) + "\n\n" : "";
        sendText = spec + renderPrompt(msgs, { includeAssistant: true });
        
        if (!hasLockedCurrent && !isGlobalLockedNoSession && !isAnchorChanged) {
          const collectedAll = await collectFileParts(msgs);
          files = Array.from(new Set([...collectedAll.files, ...collectedCurrent.files]));
          if (collectedAll.inline.length || collectedAll.skipped.length) { sendText += "\n\n" + [...collectedAll.inline, ...collectedAll.skipped].join("\n"); }
        } else {
          files = collectedCurrent.files;
          if (collectedCurrent.inline.length || collectedCurrent.skipped.length) { sendText += "\n\n" + [...collectedCurrent.inline, ...collectedCurrent.skipped].join("\n"); }
        }
      } else {
        const tail = msgs.slice(sess?.cursor || 0).filter((m) => m.role !== "assistant");
        const fallback = [...msgs].reverse().find((m) => m.role === "user" || m.role === "tool");
        sendText = renderPrompt(tail.length ? tail : fallback ? [fallback] : msgs.slice(-1), { includeAssistant: false });
        
        files = collectedCurrent.files;
        if (collectedCurrent.inline.length || collectedCurrent.skipped.length) { sendText += "\n\n" + [...collectedCurrent.inline, ...collectedCurrent.skipped].join("\n"); }
      }

      sendText = enforceMaxLength(sendText);

      if (!sendText.trim() && !files.length) throw domError("web-dom: empty prompt");
      if (process.env.WEB_DOM_DEBUG) log("SEND", fresh ? "fresh" : "incr", ":\n" + sendText.slice(0, 4000));

      const base = { backend: this._backend, python: this._python, timeout_s: this._timeout };
      let r;
      try {
        r = await callPython(
          { action: fresh ? "new_session_ask" : "ask", prompt: sendText, files, uuid: fresh ? null : sess?.uuid, ...base },
          options.abortSignal,
        );
      } catch (e) {
        const msg = String(e?.message || e);
        if (/对话已达上限|已达到对话上限|内容过长|超出长度限制|已达最大长度|请开启新对话/.test(msg)) { log("撞到网页端上限，已停止自动重发，请手动换新对话。", msg.slice(0, 200)); }
        throw e; 
      }

      const text = r.text || "";
      const uuid = r.uuid || (fresh ? null : (sess?.uuid || null)) || null;
      const calls = helperParse(text, allowed);

      sessions[skey] = {
        uuid, title: sess?.title || (uuid ? uuid.slice(0, 8) : ""),
        cursor: msgs.length, cursor_hash: msgs.length ? sha1(JSON.stringify(msgs[msgs.length - 1])) : null,
        tools_hash: toolsHash, created_at: sess?.created_at || Math.floor(Date.now() / 1000),
        last_used: Math.floor(Date.now() / 1000), msg_count: (sess?.msg_count || 0) + 1, status: "active",
      };
      saveSessions({ [skey]: sessions[skey] });
      return { prompt: sendText, text, calls };
    });
  }

  async _buildCallResult(options) {
    const tools = enabledTools(options);
    const { text: rawText, calls, prompt } = await this._callOnce(options, tools);
    const text = stripToolBlocks(rawText) || (calls.length ? "" : rawText);
    const content = [];
    if (text && text.trim()) content.push({ type: "text", text });
    for (let i = 0; i < calls.length; i++) {
      content.push({ type: "tool-call", toolCallId: `webdom-${Date.now()}-${i}`, toolName: calls[i].name, input: JSON.stringify(calls[i].args ?? {}) });
    }
    if (!content.length) throw domError("web-dom: 无有效输出", 503);
    const usage = makeUsage(estTokens(prompt), estTokens(text || JSON.stringify(calls)));
    const finishReason = calls.length ? finishReasonOf("tool-calls", undefined) : finishReasonOf("stop", undefined);
    return { content, finishReason, usage, warnings: [], text, calls };
  }

  async doGenerate(options) {
    const { content, finishReason, usage, warnings } = await this._buildCallResult(options);
    return { content, finishReason, usage, warnings };
  }

  async doStream(options) {
    const { content, finishReason, usage } = await this._buildCallResult(options);
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue({ type: "stream-start", warnings: [] });
        controller.enqueue({ type: "response-metadata", id: createHash("md5").update(`${Date.now()}-${usage.outputTokens.total}`).digest("hex").slice(0, 12), timestamp: new Date(), modelId: options.modelId || undefined });
        let textId = 0;
        for (const part of content) {
          if (part.type === "text") {
            const id = `txt-${textId++}`;
            controller.enqueue({ type: "text-start", id });
            controller.enqueue({ type: "text-delta", id, delta: part.text });
            controller.enqueue({ type: "text-end", id });
          } else if (part.type === "tool-call") {
            const id = part.toolCallId;
            controller.enqueue({ type: "tool-input-start", id, toolName: part.toolName });
            controller.enqueue({ type: "tool-input-delta", id, delta: part.input });
            controller.enqueue({ type: "tool-input-end", id });
            controller.enqueue({ type: "tool-call", toolCallId: part.toolCallId, toolName: part.toolName, input: part.input });
          }
        }
        controller.enqueue({ type: "finish", finishReason, usage });
        controller.close();
      },
    });
    return { stream };
  }
}

export async function extractImageFiles(msgs, files) {
  const { extractImages } = await import('./image_helper.mjs');
  let scene = 'latest';
  for (let i = 0; i < msgs.length - 1; i++) {
    const msg = msgs[i];
    if (msg.role === 'user' && Array.isArray(msg.content)) {
      for (const part of msg.content) { if (part.type === 'image') { scene = 'history'; break; } }
    }
    if (scene === 'history') break;
  }
  if (scene !== 'history') {
    for (let i = 0; i < msgs.length; i++) {
      const msg = msgs[i];
      if (msg.role === 'tool' && msg.content && Array.isArray(msg.content)) {
        const toolResultParts = msg.content.filter(p => p.type === 'tool-result');
        for (const part of toolResultParts) {
          const output = part.output;
          if (output && typeof output === 'object' && (output.type === 'image' || (output.type && output.type.startsWith('image/')))) {
            for (let j = i + 1; j < msgs.length; j++) { if (msgs[j].role === 'user') { scene = 'history'; break; } }
            break;
          }
          const result = part.result;
          if (result && typeof result === 'object' && (result.type === 'image' || (result.type && result.type.startsWith('image/')))) {
            for (let j = i + 1; j < msgs.length; j++) { if (msgs[j].role === 'user') { scene = 'history'; break; } }
            break;
          }
        }
      }
      if (scene === 'history') break;
    }
  }
  if (scene === 'latest') {
    const extraFiles = await extractImages(msgs);
    for (const f of extraFiles) { files.push(f); }
  }
  for (const msg of msgs) {
    if (!Array.isArray(msg.content)) continue;
    for (const part of msg.content) {
      if (msg.role === 'user' && part.type === 'image') {
        if (scene === 'history') { part.type = 'text'; part.text = '[历史图片已省略]'; }
        continue;
      }
      if (msg.role === 'tool' && part.type === 'tool-result' && part.output) {
        const output = part.output;
        if (output && typeof output === 'object' && (output.type === 'image' || (output.type && output.type.startsWith('image/')))) {
          part.output.type = 'text'; part.output.value = scene === 'history' ? '[历史工具图片已省略]' : '[图片已作为附件发送';
        }
      }
      if (msg.role === 'tool' && part.type === 'tool-result' && part.result) {
        const result = part.result;
        if (result && typeof result === 'object' && (result.type === 'image' || (result.type && result.type.startsWith('image/')))) {
          if (scene === 'latest') { part.result.type = 'text'; part.result.value = '[图片已作为附件发送'; }
        }
      }
    }
  }
}

export function createWebDom(options = {}) {
  return {
    specificationVersion: "v3",
    languageModel(modelId) {
      const mid = String(modelId).toLowerCase();
      const backend = mid.includes("deepseek") ? "deepseek" : "qwen";
      return new DomLanguageModel({
        provider: options.name || "web-dom", modelId: String(modelId), backend,
        python: options.python, timeout: options.timeout, workdir: options.workdir, virtualTools: options.virtualTools,
      });
    },
  };
}
export default createWebDom;
