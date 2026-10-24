const VALID_STATUS = new Set(["pending", "in_progress", "completed", "cancelled"]);

export function normalizeTodos(todos) {
  if (!Array.isArray(todos)) return [];
  return todos
    .filter(t => t && typeof t.content === "string")
    .map(t => ({
      id: t.id || String(Math.random()).slice(2, 10),
      content: t.content.trim(),
      status: VALID_STATUS.has(t.status) ? t.status : "pending",
    }));
}

export function renderTodos(todos) {
  if (!todos) return "（当前没有 Todo 任务）";
  
  let arr = todos;
  if (typeof arr === "string") {
    try { arr = JSON.parse(arr); } catch { return arr; }
  }
  
  if (arr && typeof arr === "object" && !Array.isArray(arr)) {
     if (Array.isArray(arr.todos)) arr = arr.todos;
     else if (Array.isArray(arr.value)) arr = arr.value;
     else if (Array.isArray(arr.content)) arr = arr.content;
     else return JSON.stringify(arr, null, 2);
  }
  
  if (!Array.isArray(arr)) return String(arr);
  if (arr.length === 0) return "（当前没有 Todo 任务）";
  
  return arr
    .map(t => `- [${t.status === "completed" ? "x" : " "}] ${t.content || JSON.stringify(t)}`)
    .join("\n");
}
