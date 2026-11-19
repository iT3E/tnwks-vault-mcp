# tnwks-vault-mcp

The vault server of the tnwks agent platform: the reference file server of
the MCP project (`@modelcontextprotocol/server-filesystem`), served over
Streamable HTTP by the official MCP TypeScript SDK, for one directory. A
short wrapper ([`src/serve.mjs`](src/serve.mjs)) connects the two, and adds
a health path, a body limit, a refusal of browser requests and a clean
stop. There is no bridge product and no other code.

The image is public (`ghcr.io/it3e/tnwks-vault-mcp`). The design is effort
0003 in tnwks-coe (the Design Doc, section 4.2.5). tnwks-ops runs one pod of
it for each share, behind the central MCP gateway.

## The contract

| Item               | Value                                                                                                                                                                                 |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Image              | `ghcr.io/it3e/tnwks-vault-mcp:<X.Y.Z>`, and `:sha-<short>` for the same build. Only a release tag pushes. There is no `:latest`. tnwks-ops pins `:<X.Y.Z>@sha256:<digest>`            |
| User               | uid 1000, gid 1000                                                                                                                                                                    |
| Root file system   | Read only. The server needs no writable path other than the share                                                                                                                     |
| Command            | `node src/serve.mjs`, with no entrypoint script and no shell                                                                                                                          |
| The share          | Mounted read and write at `/vault`. The server serves this directory only, and it stops with exit code 1 when `/vault` is missing                                                     |
| Port               | 8080. MCP over Streamable HTTP on `/mcp`, the health path on `/healthz`, 404 on any other path. No authentication: the gateway in front of it and the network policy are the controls |
| Probes             | `GET /healthz` gives 200 when the process serves HTTP. It reads no file (below)                                                                                                       |
| Stop               | SIGTERM: it stops taking requests, ends the open ones and exits with 0                                                                                                                |
| Network at a start | None. Nothing is fetched at a start                                                                                                                                                   |
| Memory             | 81 MiB at most in the test with 20 calls at one time. Request 128 MiB, limit 256 MiB                                                                                                  |

**Roots.** The server starts with `confineRoots` on. A Roots message of a
client can only narrow the directory, never widen it. The SDK serves each
2025-era request with a new server instance and no session, so a request of
the server to the client (such as `roots/list`) has no way back, and the
directory stays `/vault`.

**The health path.** `GET /healthz` reads no file and uses no thread of
the libuv pool. A probe that checks the share is not a good probe: on an
NFS mount with `hard`, a file call waits as long as the NFS server is away,
and a restart of the pod does not help then. While the share hangs, the
calls that read a file wait (the pool has four threads), and the health
path still answers. When the share is back, the waiting calls end and new
calls work. The image test shows it with held threads.

**A write that failed can still land.** On a hard mount, a write that
waits can end after the gateway has given its caller a timeout error. A
caller that gets an error on a write reads the note again before it tries
the write again. `edit_file` helps here: an edit whose old text is no longer
in the note fails, so a retry of an edit that already landed does not apply
twice.

## What it does with a call

The wrapper routes each request. `/mcp` goes to the handler of the SDK
(`createMcpHandler` of `@modelcontextprotocol/server`, with `toNodeHandler`
of `@modelcontextprotocol/node`):

| Request                                            | Answer                                                                                                                      |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Any request with an `Origin` header                | 403, from the wrapper. A browser sends `Origin`; the gateway and the probes do not                                          |
| `GET /healthz`                                     | 200, from the wrapper                                                                                                       |
| Any other path, or another method on `/healthz`    | 404, from the wrapper                                                                                                       |
| `POST /mcp` with `Content-Type: application/json`  | MCP. A 2025-era message gets a new server instance of the file server; a 2026-07-28 message the same, in its own era        |
| `GET`, `DELETE`, `PUT` and other methods on `/mcp` | 405, `Method not allowed.` No session and no stream                                                                         |
| `POST /mcp` with another content type              | 415                                                                                                                         |
| A body that is not JSON                            | 400, a JSON-RPC parse error. The next call works                                                                            |
| A body over 2 MiB                                  | 413. A `Content-Length` over the limit is refused before a read; a body without one stops at the limit. The next call works |
| A batch of more than 100 messages                  | 400                                                                                                                         |
| A tool that fails, or a tool that does not exist   | A JSON-RPC result with `isError`, or an error. The next call works                                                          |
| An error in the handler                            | 500 with no detail. A failure to send the answer closes that connection only                                                |

**The body limit is 2 MiB.** It is the request buffer of the central MCP
gateway (agentgateway 1.7.0: `DEFAULT_BUFFER_LIMIT` is 2,097,152 bytes), so
no larger body reaches this server through it. A write of a note of
1.5 MiB works, in the test. The largest note is a little under 2 MiB, less
for a text with many characters that JSON escapes. The SDK default is
4 MiB.

**Host and Origin.** `createMcpHandler` of the pinned SDK checks neither.
The `allowedHosts`, `allowedOrigins` and `enableDnsRebindingProtection`
options belong to the older transport class, are off by default and are
marked deprecated; the SDK exports `hostHeaderValidationResponse` and
`originValidationResponse` for the server to call. The wrapper refuses any
request with an `Origin` header, which costs one line: DNS rebinding needs a
browser, and a browser sends `Origin` with each such `POST`. It does not
check `Host`: the name that reaches the pod changes with the namespace and
the gateway config, and a list of names would be config to keep.

## The log

The wrapper and the SDK write nothing. The file server writes three lines to standard
error for each new connection that ends its handshake: that the client
did not give Roots, and the allowed directory, `/vault`. With the gateway in
front, that is each call. A client that gives Roots also gets one line that
names the roots it gave. No line has a file name, a file text, a tool
argument or a result: the test checks it with marker texts. The three lines
can not be turned off without a change to the file server. They are
`console.error` calls of the package, with no option.

## What is in it

- **Node** 24, the `node:24.22.3-trixie-slim` image at its digest
  ([`Dockerfile`](Dockerfile)).
- **The packages** of [`package-lock.json`](package-lock.json), each with its
  integrity hash: `@modelcontextprotocol/server-filesystem` 2.3.0,
  `@modelcontextprotocol/server` 2.4.0 and `@modelcontextprotocol/node`
  2.1.2 as direct dependencies at exact versions, and their dependencies.
  `npm ci` installs them with no install script.
- **The wrapper**, [`src/serve.mjs`](src/serve.mjs). It imports
  `createServer` and `resolveAllowedDirectories` from the `dist/` files of
  the file server, which the package does not declare as its API (it has no
  `exports` map, so Node allows the path). The unit test
  ([`test/imports.test.mjs`](test/imports.test.mjs)) and the image test fail
  when a release moves them.

## The review of the packages

A person reads the source of each new release of the three packages before
it merges. The last read was at the versions above:

- **The handler path:** as in the table of "What it does with a call". The
  body limit is in the SDK (`maxRequestBodySize`, default 4 MiB) and in the
  Node adapter, which counts the bytes as they arrive; the wrapper gives
  both 2 MiB. The Node adapter catches an error of the handler and answers
  500 with no detail. The SDK checks no `Host` and no `Origin` header.
- **The file server:** each path goes through its path check, with
  symlinks resolved, against the allowed directory. It has no delete tool.
  `edit_file` fails when the old text is not in the file.
- **Advisories:** none open at these versions, in the GitHub Advisory
  Database for the three packages, `@modelcontextprotocol/core`, `hono`,
  `@hono/node-server`, `zod`, `diff` and `minimatch`, and none in
  `npm audit`. The advisories of the SDK of 2026-10 (the body size limit,
  shared server instances, memory use of one request) are fixed before 2.3.0
  of `@modelcontextprotocol/server` and 2.1.0 of `@modelcontextprotocol/node`.

## The pins and their updates

| Pin                | Where                                      | Update                                                                                                              |
| ------------------ | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| Node               | `Dockerfile` (tag and digest) and `.nvmrc` | Renovate. A patch and a new digest merge when CI is green                                                           |
| The three packages | `package.json`, exact                      | Renovate, 7 days after the release, and a person reviews the source before the merge ("The review of the packages") |
| Their dependencies | `package-lock.json`                        | Lock file maintenance of Renovate, merged when CI is green                                                          |

## Tests

- `npm test`: the wrapper's imports still exist in the pinned packages.
- `bin/image-test <image ref>` ([`tests/test_image.py`](tests/test_image.py)):
  the test of the image job in CI, before any push. It needs Docker and
  python3 only, and `task test` runs it on a workstation. The containers run
  as uid 1000 with a read-only root file system and no capability, on a
  network of their own. The client is a container of the same image
  ([`tests/client.mjs`](tests/client.mjs)). It checks:
  - the user, the command and the port, and no share or config in the image;
  - only `/mcp` and `/healthz` answer, any other path gets 404, and a
    request with `Origin` gets 403;
  - bad JSON, a body over 2 MiB, a tool that fails and a tool that does not
    exist: each is refused or fails, and the next call works; a note of
    1.5 MiB is written;
  - with the threads of the pool held, as on a hung share, a call waits,
    the health path answers within 2 seconds, and the calls work again when
    the threads are free;
  - a start with no network, a stop with exit code 1 with no share, a stop
    with an error when an import of the file server moved, and a clean
    stop on SIGTERM;
  - Roots do not widen the directory, and a read outside it fails;
  - a symlink that leads outside fails for a read, a write and an overwrite;
  - an edit with old text that changed fails, and the newer text stays;
  - the tool list has no delete tool;
  - the log holds no file name and no file text after a read, a write, a
    missing file, a path outside and bad arguments;
  - 20 calls at one time, five times, with a limit of 256 MiB: each one
    works, and the container is not killed.

## Workflow

This repo follows the app-repo standard in
[iT3E/app-repo-template](https://github.com/iT3E/app-repo-template), with
CI from [iT3E/github-workflows](https://github.com/iT3E/github-workflows);
`AGENTS.md` has the short version.

- Changes go through PRs, never straight to `main`. Merge only when every
  check is green (`gh-merge-safe.sh <pr> iT3E/tnwks-vault-mcp`).
- `task precommit:init` installs the hooks: betterleaks, the privacy
  denylist (staged files, git identity, commit message) and prettier (after `npm ci`).
- Each PR adds a Changie fragment (`changie new`) or carries the
  `skip-changelog` label. Merging fragments cuts a release (`vX.Y.Z` tag,
  GitHub Release, image `:X.Y.Z`).
- Renovate keeps dependencies current. Patch, digest and non-0.x minor
  updates merge themselves once CI is green; majors wait for review on the
  Renovate Dashboard issue. Template releases arrive as their own
  `renovate/template` PRs.
