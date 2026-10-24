// 独立图片提取模块：不依赖任何业务逻辑
import { writeFileSync, readFileSync, existsSync, statSync } from 'node:fs';
import { join } from 'node:path';
import os from 'node:os';

const _decodePart = async (p) => {
  let raw = typeof p === 'string' ? p : (p.image ?? p.data ?? p.url ?? p.value ?? p.path ?? p.filename ?? p.output);
  let mime = (typeof p === 'object' ? (p.mimeType || p.mediaType || p.contentType || p.mime) : null) || 'image/png';
  if (raw == null) return null;

  if (typeof raw === 'object' && raw.type === 'image' && raw.data) {
    raw = raw.data;
  }

  if (Buffer.isBuffer(raw)) return { buf: raw, mime };
  if (raw instanceof Uint8Array) return { buf: Buffer.from(raw), mime };

  if (typeof raw === 'string') {
    if (raw.startsWith('data:')) {
      const m = raw.match(/^data:([^;,]+)(;base64)?,([\s\S]*)$/);
      if (!m) return null;
      const buf = m[2] ? Buffer.from(m[3], 'base64') : Buffer.from(decodeURIComponent(m[3]), 'binary');
      return { buf, mime: m[1] || mime };
    }
    if (raw.startsWith('http://') || raw.startsWith('https://')) {
      try { const r = await fetch(raw); return { buf: Buffer.from(await r.arrayBuffer()), mime }; }
      catch (e) { return null; }
    }
    if (raw.startsWith('file://')) {
      try { return { buf: readFileSync(decodeURIComponent(raw.replace(/^file:\/\//, ''))), mime }; } catch { return null; }
    }
    try {
      if (existsSync(raw) && statSync(raw).isFile()) {
        return { buf: readFileSync(raw), mime };
      }
    } catch {}
    try { return { buf: Buffer.from(raw, 'base64'), mime }; } catch { return null; }
  }
  return null;
};

/**
 * 从消息列表中提取所有图片并保存为临时文件
 * @param {Array} msgs - 待处理的消息数组
 * @param {Function} logFn - 日志记录函数（可选）
 * @returns {Promise<string[]>} 临时文件路径数组
 */
export async function extractImages(msgs, logFn = () => {}) {
  const extraFiles = [];

  const _saveBuf = (buf, mime) => {
    try {
      const ext = String(mime || 'image/png').split('/')[1].split('+')[0] || 'png';
      const tmpPath = join(os.tmpdir(), 'webdom-img-' + Date.now() + '-' + Math.random().toString(36).slice(2) + '.' + ext);
      writeFileSync(tmpPath, buf);
      extraFiles.push(tmpPath);
      logFn('attachment saved', tmpPath, mime);
    } catch (e) { logFn('Image save failed:', e.message); }
  };

  const _isImg = (p) => {
    const mt = String(p.mimeType || p.mediaType || p.contentType || '');
    return mt.startsWith('image/') || p.type === 'image';
  };

  for (const msg of msgs) {
    if (!Array.isArray(msg.content)) continue;
    for (const part of msg.content) {
      // 处理工具返回的图片
      if (msg.role === 'tool' && part.type === 'tool-result') {
        const res = part.output ?? part.result;
        if (res && typeof res === 'object' && (res.type === 'image' || (res.contentType && String(res.contentType).startsWith('image/')) || (res.mimeType && String(res.mimeType).startsWith('image/')))) {
          const dec = await _decodePart(res);
          if (dec) _saveBuf(dec.buf, dec.mime);
        } else if (typeof res === 'string') {
          const dec = await _decodePart(res);
          if (dec) _saveBuf(dec.buf, dec.mime);
        }
      }
      // 处理用户上传的图片
      else if (msg.role === 'user' && (part.type === 'image' || part.type === 'file')) {
        if (!_isImg(part)) continue;
        const dec = await _decodePart(part);
        if (dec) _saveBuf(dec.buf, dec.mime);
      }
    }
  }

  return extraFiles;
}
