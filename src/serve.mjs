// The reference file server of the MCP project for one directory, /vault,
// over Streamable HTTP on port 8080 (README.md, "The contract"). The SDK
// serves each 2025-era request with a new server instance and no session.
import { createServer as listen } from "node:http";
import { createMcpHandler } from "@modelcontextprotocol/server";
import { toNodeHandler } from "@modelcontextprotocol/node";
import { createServer } from "@modelcontextprotocol/server-filesystem/dist/server.js";
import { resolveAllowedDirectories } from "@modelcontextprotocol/server-filesystem/dist/index.js";

// The largest request body: the request buffer of the central gateway, 2 MiB.
const limit = { maxRequestBodySize: 2 * 1024 * 1024 };
const directories = await resolveAllowedDirectories(["/vault"]);
const handler = createMcpHandler(({ era }) => createServer(directories, { era, confineRoots: true }), limit);
const mcp = toNodeHandler(handler, limit);
const server = listen((req, res) => {
  const path = req.url.split("?")[0];
  // A browser sends Origin with each POST; the gateway and the probes send none.
  if (req.headers.origin !== undefined) return res.writeHead(403).end();
  // The probes: no file is read, so a hung share does not hold the answer.
  if (path === "/healthz" && req.method === "GET") return res.writeHead(200).end("ok\n");
  if (path === "/mcp") return mcp(req, res).catch(() => res.destroy());
  res.writeHead(404).end();
}).listen(8080);
// Node as process 1 has no default action for SIGTERM: stop taking requests, end the open ones, exit.
process.once("SIGTERM", () => server.close(() => process.exit(0)));
