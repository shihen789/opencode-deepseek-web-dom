import { jsonrepair } from 'jsonrepair';

const NL = String.fromCharCode(10);

export function buildSystemPrompt(tools) {
    const lines = [
        '【系统指令】请使用极简格式调用工具，绝对禁止使用嵌套结构！',
        '',
        '正确格式：',
        '{"tool": "工具名", "参数1": "值1"}',
        '',
        '规则：tool必须填工具名，参数平铺在顶层。',
        '如果需要调用多个工具，请分成多个独立的 JSON 块输出。',
        '',
        '可用工具：'
    ];
    for (const t of tools) lines.push('- ' + t.name + ': ' + (t.description || ''));
    return lines.join(NL);
}

export function parseToolCalls(rawText, allowed) {
    if (!rawText) return [];
    let text = String(rawText).replace(/json\s*复制\s*下载/gi, '');
    
    const allowedSet = allowed instanceof Set ? allowed : new Set(allowed || ['bash', 'read', 'write', 'edit', 'glob', 'grep']);
    const results = [];
    let depth = 0;
    let start = -1;
    let inString = false;
    let escape = false;

    for (let i = 0; i < text.length; i++) {
        const ch = text[i];
        if (escape) { escape = false; continue; }
        if (ch === '\\') { escape = true; continue; }
        if (ch === '"') { inString = !inString; continue; }
        if (inString) continue;

        if (ch === '{') {
            if (depth === 0) start = i;
            depth++;
        } else if (ch === '}') {
            depth--;
            if (depth === 0 && start !== -1) {
                const jsonStr = text.slice(start, i + 1);
                start = -1;
                _tryParseBlock(jsonStr, allowedSet, results);
            }
        }
    }
    return results;
}

function _tryParseBlock(jsonStr, allowedSet, results) {
    let parsed = null;
    try {
        parsed = JSON.parse(jsonStr);
    } catch {
        try {
            parsed = JSON.parse(jsonrepair(jsonStr));
        } catch {
            return;
        }
    }

    if (!parsed || typeof parsed !== 'object') return;

    // 兼容 1: 标准嵌套 tool_calls 数组
    if (Array.isArray(parsed.tool_calls)) {
        for (const tc of parsed.tool_calls) {
            if (tc.type === 'function' && tc.function && tc.function.name) {
                const name = tc.function.name;
                if (!allowedSet.has(name)) continue;
                let args = tc.function.arguments;
                if (typeof args === 'string') {
                    try { args = JSON.parse(args); } catch { args = {}; }
                }
                args = args || {};
                if (name === 'bash' && !args.command) args.command = 'echo 1';
                if (!results.find(r => r.name === name && JSON.stringify(r.args) === JSON.stringify(args))) {
                    results.push({ name, args });
                }
            }
        }
        return;
    }

    // 兼容 2: 极简扁平 {"tool": "bash", ...}
    if (typeof parsed.tool === 'string' && parsed.tool) {
        const name = parsed.tool;
        if (!allowedSet.has(name)) return;
        const args = {};
        for (const k in parsed) {
            if (k !== 'tool') args[k] = parsed[k];
        }
        if (name === 'bash' && !args.command) args.command = 'echo 1';
        if (!results.find(r => r.name === name && JSON.stringify(r.args) === JSON.stringify(args))) {
            results.push({ name, args });
        }
    }
}