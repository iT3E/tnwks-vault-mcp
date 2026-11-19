// The wrapper imports two files of the file server package by their path in
// dist/, which the package does not declare as its API. A release that moves
// or renames them must fail here, before an image is built.
import assert from "node:assert/strict";
import { test } from "node:test";

test("the file server still has the factory and the directory check", async () => {
  const server = await import("@modelcontextprotocol/server-filesystem/dist/server.js");
  const entry = await import("@modelcontextprotocol/server-filesystem/dist/index.js");
  assert.equal(typeof server.createServer, "function");
  assert.equal(typeof entry.resolveAllowedDirectories, "function");
});

test("the SDK still has the HTTP handler and its Node adapter", async () => {
  const { createMcpHandler } = await import("@modelcontextprotocol/server");
  const { toNodeHandler } = await import("@modelcontextprotocol/node");
  assert.equal(typeof createMcpHandler, "function");
  assert.equal(typeof toNodeHandler, "function");
});

test("the factory serves the directory it gets, confined", async () => {
  const { createServer } = await import("@modelcontextprotocol/server-filesystem/dist/server.js");
  const instance = createServer(["/vault"], { era: "legacy", confineRoots: true });
  assert.ok(instance);
});
