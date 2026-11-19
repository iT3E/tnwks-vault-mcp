// The MCP client of the image test. It runs in a container of the image
// itself (node, no other image to pin), on the network of the server, and
// prints one JSON object. It speaks the 2025-era protocol, as the central
// gateway does: each call opens with initialize, with no session.
//
//   node client.mjs <url> burst <calls at one time> <rounds>
//   node client.mjs <url> markers
//   node client.mjs <url> rows
//   node client.mjs <url> surface         the HTTP surface: paths, methods, limits, errors
//   node client.mjs <url> health          one GET of the health path, 2 s at most
//   node client.mjs <url> read <ms>       one call of a note, <ms> at most

const [url, mode, ...args] = process.argv.slice(2);
const PROTOCOL = "2025-06-18";

class Session {
  constructor(capabilities = {}) {
    this.capabilities = capabilities;
    this.id = 0;
    this.sessionId = undefined;
  }

  async post(message) {
    const headers = {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      "mcp-protocol-version": PROTOCOL,
    };
    if (this.sessionId) headers["mcp-session-id"] = this.sessionId;
    const response = await fetch(url, { method: "POST", headers, body: JSON.stringify(message) });
    this.sessionId = response.headers.get("mcp-session-id") ?? this.sessionId;
    const text = await response.text();
    if (!response.ok) return { httpStatus: response.status, text: text.slice(0, 300) };
    if (!text.trim()) return null;
    if ((response.headers.get("content-type") ?? "").includes("text/event-stream")) {
      for (const line of text.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const data = JSON.parse(line.slice(5));
        if (data.id === message.id) return data;
      }
      return null;
    }
    return JSON.parse(text);
  }

  request(method, params = {}) {
    this.id += 1;
    return this.post({ jsonrpc: "2.0", id: this.id, method, params });
  }

  notify(method, params = {}) {
    return this.post({ jsonrpc: "2.0", method, params });
  }

  async open() {
    await this.request("initialize", {
      protocolVersion: PROTOCOL,
      capabilities: this.capabilities,
      clientInfo: { name: "image-test", version: "0" },
    });
    await this.notify("notifications/initialized");
    return this;
  }

  async call(name, args) {
    const answer = await this.request("tools/call", { name, arguments: args });
    if (!answer || answer.httpStatus) return { ok: false, text: JSON.stringify(answer) };
    if (answer.error) return { ok: false, text: answer.error.message ?? "" };
    const text = (answer.result.content ?? []).map((c) => c.text ?? "").join(" ");
    return { ok: !answer.result.isError, text };
  }
}

const NOTE = "/vault/notes/marker-note-7f3a.md";
const BODY = "MARKER-BODY-7f3a";

async function burst(count, rounds) {
  const failures = [];
  const times = [];
  for (let round = 0; round < rounds; round += 1) {
    await Promise.all(
      Array.from({ length: count }, async () => {
        const start = performance.now();
        try {
          const session = await new Session().open();
          const { ok, text } = await session.call("read_text_file", { path: NOTE });
          if (!ok || !text.includes(BODY)) failures.push(text.slice(0, 120));
        } catch (error) {
          failures.push(String(error));
        }
        times.push(performance.now() - start);
      }),
    );
  }
  times.sort((a, b) => a - b);
  return { calls: count * rounds, failed: failures.length, firstFailure: failures[0], maxMs: Math.round(times.at(-1)) };
}

async function markers() {
  const session = await new Session().open();
  return {
    read: (await session.call("read_text_file", { path: NOTE })).ok,
    write: (
      await session.call("write_file", { path: "/vault/notes/MARKER-WRITE-NAME.md", content: "MARKER-WRITE-BODY" })
    ).ok,
    missing: (await session.call("read_text_file", { path: "/vault/notes/MARKER-MISSING-NAME.md" })).ok,
    outside: (await session.call("read_text_file", { path: "/outside/MARKER-OUTSIDE-NAME.txt" })).ok,
    badArguments: (await session.call("edit_file", { path: "/vault/notes/MARKER-BADARG-NAME.md", edits: "x" })).ok,
  };
}

async function rows() {
  const result = {};
  // Roots that offer the whole disk and the outside directory.
  const rooted = await new Session({ roots: { listChanged: true } }).open();
  await rooted.notify("notifications/roots/list_changed");
  result.allowed = (await rooted.call("list_allowed_directories", {})).text;
  const outside = await rooted.call("read_text_file", { path: "/outside/MARKER-OUTSIDE-NAME.txt" });
  result.readOutside = { ok: outside.ok, leaked: outside.text.includes("outside-secret") };

  const session = await new Session().open();
  const viaFile = await session.call("read_text_file", { path: "/vault/link-file" });
  result.readThroughSymlink = { ok: viaFile.ok, leaked: viaFile.text.includes("outside-secret") };
  result.writeThroughSymlinkDir = (
    await session.call("write_file", { path: "/vault/link-dir/new.txt", content: "x" })
  ).ok;
  result.overwriteSymlink = (await session.call("write_file", { path: "/vault/link-file", content: "overwrite" })).ok;

  const note = "/vault/notes/two-edits.md";
  result.firstWrite = (await session.call("write_file", { path: note, content: "line one\nv1 text\n" })).ok;
  result.firstEdit = (
    await session.call("edit_file", { path: note, edits: [{ oldText: "v1 text", newText: "v2 text" }] })
  ).ok;
  result.staleEdit = (
    await session.call("edit_file", { path: note, edits: [{ oldText: "v1 text", newText: "v3 text" }] })
  ).ok;
  result.noteAfter = (await session.call("read_text_file", { path: note })).text;

  const tools = await session.request("tools/list");
  result.tools = (tools?.result?.tools ?? []).map((tool) => tool.name).sort();
  return result;
}

const base = url.replace(/\/mcp$/, "");
const INIT = JSON.stringify({
  jsonrpc: "2.0",
  id: 1,
  method: "initialize",
  params: { protocolVersion: PROTOCOL, capabilities: {}, clientInfo: { name: "image-test", version: "0" } },
});
const JSON_HEADERS = { "content-type": "application/json", accept: "application/json, text/event-stream" };

async function status(path, options = {}) {
  try {
    const response = await fetch(base + path, { signal: AbortSignal.timeout(10000), ...options });
    return { status: response.status, body: (await response.text()).slice(0, 80) };
  } catch (error) {
    return { status: "error", body: String(error.cause?.code ?? error.message) };
  }
}

async function works() {
  const { ok, text } = await (await new Session().open()).call("read_text_file", { path: NOTE });
  return ok && text.includes(BODY);
}

function writeBody(size, path) {
  return JSON.stringify({
    jsonrpc: "2.0",
    id: 2,
    method: "tools/call",
    params: { name: "write_file", arguments: { path, content: "a".repeat(size) } },
  });
}

async function surface() {
  const result = {};
  result.health = await status("/healthz");
  result.healthQuery = await status("/healthz?x=1");
  result.healthPost = await status("/healthz", { method: "POST", headers: JSON_HEADERS, body: INIT });
  result.root = await status("/");
  result.unknownPath = await status("/other", { method: "POST", headers: JSON_HEADERS, body: INIT });
  result.belowMcp = await status("/mcp/x", { method: "POST", headers: JSON_HEADERS, body: INIT });
  result.mcpInit = await status("/mcp", { method: "POST", headers: JSON_HEADERS, body: INIT });
  result.mcpGet = await status("/mcp", { headers: { accept: "text/event-stream" } });
  result.mcpOrigin = await status("/mcp", {
    method: "POST",
    headers: { ...JSON_HEADERS, origin: "http://example.invalid" },
    body: INIT,
  });
  result.badJson = await status("/mcp", { method: "POST", headers: JSON_HEADERS, body: "{not json" });
  result.worksAfterBadJson = await works();
  // A note of 1.5 MiB is written; a body of 2 MiB and one byte is not.
  result.bigWrite = await status("/mcp", {
    method: "POST",
    headers: JSON_HEADERS,
    body: writeBody(1.5 * 1024 * 1024, "/vault/notes/big-note.md"),
  });
  const tooBig = writeBody(0, "/vault/notes/too-big.md");
  result.tooBig = await status("/mcp", {
    method: "POST",
    headers: JSON_HEADERS,
    body: writeBody(2 * 1024 * 1024 + 1 - tooBig.length, "/vault/notes/too-big.md"),
  });
  result.worksAfterTooBig = await works();
  // Tool errors: a read of a folder throws in the file server, and a tool that does not exist.
  const session = await new Session().open();
  result.folderRead = await session.call("read_text_file", { path: "/vault/notes" });
  result.unknownTool = await session.call("no_such_tool", {});
  result.worksAfterToolErrors = await works();
  return result;
}

async function health() {
  const start = performance.now();
  const answer = await status("/healthz", { signal: AbortSignal.timeout(2000) });
  return { ...answer, ms: Math.round(performance.now() - start) };
}

async function read(ms) {
  try {
    const done = await Promise.race([works(), new Promise((resolve) => setTimeout(() => resolve("timeout"), ms))]);
    return { answer: done };
  } catch (error) {
    return { answer: String(error) };
  }
}

const run = {
  burst: () => burst(Number(args[0]), Number(args[1])),
  markers,
  rows,
  surface,
  health,
  read: () => read(Number(args[0])),
}[mode];
console.log(JSON.stringify(await run()));
// A call that waits on held files must not keep the client alive.
process.exit(0);
