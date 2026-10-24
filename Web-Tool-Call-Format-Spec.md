# 📐 Web Tool Call Output Format Specification

## Specification Version: v3.0

---

## 🎯 Purpose

Define the exact output format for tool calls in the Web UI environment, ensuring reliable parsing by `parseToolCalls` and consistent model behavior.

---

## 📑 Markdown Fence Structure

### Correct Format

```markdown
```json
{
  "tool_calls": [
    {
      "id": "call_1",
      "type": "function",
      "function": {
        "name": "read",
        "arguments": "{\"path\":\"a.txt\"}"
      }
    }
  ]
}
```
```

### Structure Breakdown

| Component | Description | Required |
|-----------|-------------|----------|
| ```json | Fence marker (must contain `json` or `JSON`) | ✅ |
| JSON content | Valid JSON with `tool_calls` key | ✅ |
| Text after fence | Optional, ignored by parser | ❌ |

### Fence Variations

| Format | Valid | Notes |
|--------|-------|-------|
| ```json | ✅ | Standard |
| ```JSON | ✅ | Case‑insensitive |
| ```javascript | ❌ | Not recognized |
| ```py | ❌ | Not recognized |

---

## 📋 JSON Internal Structure

```json
{
  "tool_calls": [
    {
      "id": "call_1",
      "type": "function",
      "function": {
        "name": "read",
        "arguments": "{\"path\":\"a.txt\"}"
      }
    }
  ]
}
```

### Each Tool Call Item

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `id` | `string` | Unique identifier | `call_1` |
| `type` | `string` | Must be `function` | `function` |
| `function.name` | `string` | Tool name | `read`, `bash`, `glob` |
| `function.arguments` | `string` | JSON string of parameters | `{"path":"a.txt"}` |

---

## 🌟 Common Scenarios

### Scenario A: Single Tool Call

```markdown
```json
{
  "tool_calls": [
    {
      "id": "call_1",
      "type": "function",
      "function": {
        "name": "read",
        "arguments": "{\"path\":\"a.txt\"}"
      }
    }
  ]
}
```
```

**Result**: 1 call extracted → `name=read`, `args={path: "a.txt"}`

### Scenario B: Multiple Tool Calls

```markdown
```json
{
  "tool_calls": [
    {
      "id": "call_1",
      "type": "function",
      "function": {
        "name": "read",
        "arguments": "{\"path\":\"a.txt\"}"
    },
    {
      "id": "call_2",
      "type": "function",
      "function": {
        "name": "bash",
        "arguments": "{\"command\":\"echo hi\"}"
      }
    }
  ]
}
```
```

**Result**: 2 calls extracted in order

### Scenario C: Code Block with Extra Fields

```markdown
```json
{
  "text": "hello",
  "tool_calls": [
    {
      "id": "c1",
      "type": "function",
      "function": {
        "name": "read",
        "arguments": "{\"path\":\"a.txt\"}"
      }
    }
  ]
}
```
```

**Result**: Extract `tool_calls`, ignore `text` field

### Scenario D: No Fence (Plain Text)

```
用户输入：帮我分析这个问题
```

**Result**: `[]` (0 tool calls) - treated as plain text

### Scenario E: Fence Followed by Text

```markdown
```json
{
  "tool_calls": [
    {
      "id": "c1",
      "type": "function",
      "function": {
        "name": "read",
        "arguments": "{\"path\":\"a.txt\"}"
      }
    }
  ]
}
```
后面的说明文字
```

**Result**: Extract first fence's `tool_calls`, ignore trailing text

---

## ⚠️ Strictly Prohibited Formats

| Format | Result |
|--------|--------|
| XML format ` engineered...` | ❌ Rejected, returns `[]` |
| Old simplified format `{name: 'read', arguments: {path: 'b.txt'}}` | ❌ Rejected, returns `[]` |
| Bare `{command: 'echo'}` (object without `command` key) | ❌ Rejected, returns `[]` |

---

## 📊 Input/Output Comparison Table

| Input | Parsed Result |
|-------|--------------|
| `只是文字` | `[]` (plain text) |
| ```json\n{...}``` | Extract `tool_calls` array |
| ```JSON\n{...}``` | Same (case‑insensitive) |
| Empty/whitespace | `[]` |
| XML markup | `[]` (rejected) |

---

## ❓ Frequently Asked Questions

### Q: I sent ```json ``` but it didn't parse
A: Verify the fence is **closed** (another line with ```). The JSON inside must contain `tool_calls` array.

### Q: Model output code block but no `tool_calls`
A: This is valid output but no tool calls extracted. Model may have decided not to call tools, or parameter format is incorrect.

### Q: Why doesn't ```JSON ``` (uppercase) work?
A: Both ```json ``` and ```JSON ``` are valid fence markers - case‑insensitive.

### Q: What if there are multiple code blocks?
A: Only the **first** ```json ``` fence's `tool_calls` is extracted. Subsequent fences are ignored.

---

## 📝 Document Information

- **Specification Version**: v3.0
- **Last Updated**: 2026-10-22
- **Based On**: opencode web‑dom provider format specification
- **Parser**: `parseToolCalls` function in `index.mjs`

---

## 🎯 Quick Checklist

Before sending tool calls from the web UI:

- [ ] Output contains ```json ``` fence (or ```JSON ```)
- [ ] Fence is **closed** with another ``` ```
- [ ] JSON inside contains `tool_calls` array
- [ ] Each call has `id`, `type: "function"`, `function.name`, `function.arguments`
- [ ] Output has **proper indentation and line breaks** (not compressed on one line)
- [ ] No need to worry about indentation/formatting - parser only cares about structure

---

## 📬 Feedback

If you experience any parsing issues, check:

1. Fence is properly opened and closed with ``` ```
2. JSON is valid and contains `tool_calls` key
3. No extraneous characters outside the fence
4. Indentation and line breaks are present (improves readability but parser is flexible)

---

*Specification last updated: 2026-10-22*
*基于 opencode web‑dom provider 格式规范*