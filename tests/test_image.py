"""The test of the image (README.md, "Tests"). Python standard library and
the Docker CLI only. IMAGE_REF names the image.

Each container of the image runs as uid 1000 with a read-only root file
system, no capability and no new privileges. The calls go from a second
container of the same image (tests/client.mjs) on an internal network.
The values are fake: a share of test files and a directory outside it.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

IMAGE = os.environ["IMAGE_REF"]
HERE = Path(__file__).resolve().parent
HARDENED = ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "1000:1000"]
MEMORY_LIMIT = "256m"
NOTE_BODY = "MARKER-BODY-7f3a"
MARKERS = ["marker-note-7f3a", NOTE_BODY, "MARKER-WRITE-NAME", "MARKER-WRITE-BODY", "MARKER-MISSING-NAME",
           "MARKER-OUTSIDE-NAME", "MARKER-BADARG-NAME", "outside-secret"]


def docker(*args, check=True, timeout=120):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=timeout)


def name(prefix):
    return f"vault-test-{prefix}-{uuid.uuid4().hex[:8]}"


def make_share():
    """A share with one note and two symlinks that lead outside it, and the
    directory they lead to. The container user is not the owner, so the
    share is open to all."""
    root = Path(tempfile.mkdtemp(prefix="vault-test-"))
    share, outside = root / "vault", root / "outside"
    (share / "notes").mkdir(parents=True)
    outside.mkdir()
    (share / "notes/marker-note-7f3a.md").write_text(NOTE_BODY + "\n")
    (outside / "MARKER-OUTSIDE-NAME.txt").write_text("outside-secret\n")
    (share / "link-file").symlink_to("/outside/MARKER-OUTSIDE-NAME.txt")
    (share / "link-dir").symlink_to("/outside")
    for path in (share, share / "notes"):
        path.chmod(0o777)
    for path in (root, outside):
        path.chmod(0o755)
    return root


def mounts(root):
    return ["-v", f"{root}/vault:/vault", "-v", f"{root}/outside:/outside:ro"]


def wait_listening(container, seconds=20):
    probe = "require('net').connect(8080,'127.0.0.1').on('connect',()=>process.exit(0)).on('error',()=>process.exit(1))"
    deadline = time.time() + seconds
    while time.time() < deadline:
        if docker("exec", container, "node", "-e", probe, check=False).returncode == 0:
            return
        time.sleep(0.3)
    raise AssertionError("the server did not listen on 8080:\n" + docker("logs", container, check=False).stderr)


class TheImage(unittest.TestCase):
    def test_user_command_and_port(self):
        (info,) = json.loads(docker("image", "inspect", IMAGE).stdout)
        config = info["Config"]
        self.assertEqual(config["User"], "1000:1000")
        self.assertEqual(config["Cmd"], ["node", "src/serve.mjs"])
        self.assertFalse(config.get("Entrypoint"))
        self.assertEqual(list(config["ExposedPorts"]), ["8080/tcp"])

    def test_no_share_and_no_config_in_the_image(self):
        listing = docker("run", "--rm", "--entrypoint", "node", IMAGE, "-e",
                         "const fs=require('fs');console.log(JSON.stringify([fs.existsSync('/vault'),"
                         "fs.readdirSync('/app').sort(),fs.readdirSync('/app/src')]))").stdout
        self.assertEqual(json.loads(listing), [False, ["node_modules", "package-lock.json", "package.json", "src"],
                                               ["serve.mjs"]])


class TheStart(unittest.TestCase):
    def setUp(self):
        self.root = make_share()
        self.names = []

    def tearDown(self):
        for container in self.names:
            docker("rm", "-f", container, check=False)
        shutil.rmtree(self.root, ignore_errors=True)

    def run_server(self, *extra):
        container = name("start")
        self.names.append(container)
        docker("run", "-d", "--name", container, *HARDENED, *extra, IMAGE)
        return container

    def test_starts_read_only_as_uid_1000_with_no_network(self):
        container = self.run_server("--network", "none", *mounts(self.root))
        wait_listening(container)
        uid = docker("exec", container, "node", "-e", "console.log(process.getuid(), process.getgid())").stdout
        self.assertEqual(uid.split(), ["1000", "1000"])
        self.assertEqual(docker("inspect", "-f", "{{.State.Running}}", container).stdout.strip(), "true")

    def test_without_the_share_it_stops_with_an_error(self):
        container = self.run_server("--network", "none")
        docker("wait", container, timeout=60)
        self.assertEqual(docker("inspect", "-f", "{{.State.ExitCode}}", container).stdout.strip(), "1")
        self.assertIn("None of the specified directories are accessible", docker("logs", container).stderr)

    def test_a_moved_import_stops_the_start(self):
        # An empty dist/ stands for a release that moved the files the
        # wrapper imports by path.
        dist = "/app/node_modules/@modelcontextprotocol/server-filesystem/dist"
        container = self.run_server("--network", "none", "--tmpfs", dist, *mounts(self.root))
        docker("wait", container, timeout=60)
        self.assertNotEqual(docker("inspect", "-f", "{{.State.ExitCode}}", container).stdout.strip(), "0")
        self.assertIn("ERR_MODULE_NOT_FOUND", docker("logs", container).stderr)

    def test_sigterm_stops_it_at_once_and_cleanly(self):
        container = self.run_server("--network", "none", *mounts(self.root))
        wait_listening(container)
        start = time.time()
        docker("stop", "-t", "20", container, timeout=60)
        self.assertLess(time.time() - start, 5)
        self.assertEqual(docker("inspect", "-f", "{{.State.ExitCode}}", container).stdout.strip(), "0")


class TheCalls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = make_share()
        cls.network = name("net")
        cls.server = name("server")
        docker("network", "create", "--internal", cls.network)
        docker("run", "-d", "--name", cls.server, "--network", cls.network, "--memory", MEMORY_LIMIT,
               *HARDENED, *mounts(cls.root), IMAGE)
        wait_listening(cls.server)

    @classmethod
    def tearDownClass(cls):
        docker("rm", "-f", cls.server, check=False)
        docker("network", "rm", cls.network, check=False)
        shutil.rmtree(cls.root, ignore_errors=True)

    def client(self, *args):
        result = docker("run", "--rm", "--network", self.network, *HARDENED, "-v", f"{HERE}/client.mjs:/t/client.mjs:ro",
                        "--entrypoint", "node", IMAGE, "/t/client.mjs", f"http://{self.server}:8080/mcp", *args,
                        timeout=300)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_the_rows_of_the_design_that_the_image_proves(self):
        rows = self.client("rows")
        # Roots do not widen the directory, and a read outside it fails.
        self.assertEqual(rows["allowed"].split(), ["Allowed", "directories:", "/vault"])
        self.assertEqual(rows["readOutside"], {"ok": False, "leaked": False})
        # A symlink that leads outside: read, write and overwrite fail.
        self.assertEqual(rows["readThroughSymlink"], {"ok": False, "leaked": False})
        self.assertFalse(rows["writeThroughSymlinkDir"])
        self.assertFalse(rows["overwriteSymlink"])
        self.assertEqual((self.root / "outside/MARKER-OUTSIDE-NAME.txt").read_text(), "outside-secret\n")
        # Two edits of one note: the stale one fails, the newer text stays.
        self.assertTrue(rows["firstWrite"])
        self.assertTrue(rows["firstEdit"])
        self.assertFalse(rows["staleEdit"])
        self.assertIn("v2 text", rows["noteAfter"])
        self.assertNotIn("v3 text", rows["noteAfter"])
        # No tool deletes.
        self.assertTrue(rows["tools"])
        self.assertFalse([tool for tool in rows["tools"] if "delete" in tool or "remove" in tool])

    def test_the_log_holds_no_note_name_and_no_note_text(self):
        calls = self.client("markers")
        self.assertEqual(calls, {"read": True, "write": True, "missing": False, "outside": False,
                                 "badArguments": False})
        logs = docker("logs", self.server)
        text = logs.stdout + logs.stderr
        self.assertTrue(text, "the server logged nothing")
        self.assertEqual([marker for marker in MARKERS if marker in text], [])

    def test_the_http_surface(self):
        surface = self.client("surface")
        status = {key: value["status"] for key, value in surface.items() if isinstance(value, dict) and "status" in value}
        # Only the MCP path and the health path answer; any other path is 404.
        self.assertEqual(status["health"], 200)
        self.assertEqual(status["healthQuery"], 200)
        self.assertEqual(status["healthPost"], 404)
        self.assertEqual(status["root"], 404)
        self.assertEqual(status["unknownPath"], 404)
        self.assertEqual(status["belowMcp"], 404)
        self.assertEqual(status["mcpInit"], 200)
        self.assertEqual(status["mcpGet"], 405)
        # A request with an Origin header (a browser) is refused.
        self.assertEqual(status["mcpOrigin"], 403)
        # Bad JSON is refused, and the next call works.
        self.assertEqual(status["badJson"], 400)
        self.assertTrue(surface["worksAfterBadJson"])
        # A note of 1.5 MiB is written. A body over 2 MiB is refused, and the next call works.
        self.assertEqual(status["bigWrite"], 200)
        self.assertEqual((self.root / "vault/notes/big-note.md").stat().st_size, 1536 * 1024)
        self.assertIn(status["tooBig"], (413, "error"))
        self.assertFalse((self.root / "vault/notes/too-big.md").exists())
        self.assertTrue(surface["worksAfterTooBig"])
        # Tool errors come back as errors, and the next call works.
        self.assertFalse(surface["folderRead"]["ok"])
        self.assertFalse(surface["unknownTool"]["ok"])
        self.assertTrue(surface["worksAfterToolErrors"])
        self.assertTrue(json.loads(docker("inspect", "-f", "{{json .State}}", self.server).stdout)["Running"])

    def test_twenty_calls_at_one_time_under_the_memory_limit(self):
        result = self.client("burst", "20", "5")
        self.assertEqual(result["failed"], 0, result)
        state = json.loads(docker("inspect", "-f", "{{json .State}}", self.server).stdout)
        self.assertTrue(state["Running"])
        self.assertFalse(state["OOMKilled"])
        peak = docker("exec", self.server, "node", "-e",
                      "try{console.log(require('fs').readFileSync('/sys/fs/cgroup/memory.peak','utf8').trim())}"
                      "catch{console.log('')}").stdout.strip()
        if peak:
            print(f"\npeak memory at 20 calls at one time: {int(peak) // 1048576} MiB of {MEMORY_LIMIT}")


class TheHungShare(unittest.TestCase):
    """Files that do not answer. On a hard NFS mount, a file call waits in
    the kernel while the NFS server is away, in a thread of the libuv pool,
    which has four. A pipe in the share is no stand-in: the file server
    refuses a file that is not a regular file. So tests/hold-pool.mjs,
    loaded into the server, opens six named pipes on a signal: six blocked
    opens hold the four threads, as six calls on a hung share do. Then a
    call that reads a note waits, and the health path, which reads no file,
    must still answer. A writer on each pipe stands for the return of the
    share: the opens end, the waiting call ends, and new calls work. A test
    on a cluster with a stopped NFS server must show the same."""

    HELD = 6

    def setUp(self):
        self.root = make_share()
        self.fifos = [self.root / f"vault/hold-{index}.fifo" for index in range(self.HELD)]
        for fifo in self.fifos:
            os.mkfifo(fifo)
            fifo.chmod(0o666)
        self.network = name("net")
        self.server = name("hung")
        self.waiting = name("waiting")
        docker("network", "create", "--internal", self.network)
        docker("run", "-d", "--name", self.server, "--network", self.network, "--memory", MEMORY_LIMIT,
               *HARDENED, *mounts(self.root), "-v", f"{HERE}/hold-pool.mjs:/t/hold-pool.mjs:ro",
               "-e", "NODE_OPTIONS=--import /t/hold-pool.mjs", IMAGE)
        wait_listening(self.server)

    def tearDown(self):
        self.release()
        for container in (self.waiting, self.server):
            docker("rm", "-f", container, check=False)
        docker("network", "rm", self.network, check=False)
        shutil.rmtree(self.root, ignore_errors=True)

    def release(self):
        """Open each pipe for writing and close it, so each blocked open ends.
        It returns the number of pipes that had a reader."""
        released = 0
        for fifo in self.fifos:
            try:
                os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
                released += 1
            except OSError:
                pass
        return released

    def client_args(self, *args):
        return ["--network", self.network, *HARDENED, "-v", f"{HERE}/client.mjs:/t/client.mjs:ro", "--entrypoint",
                "node", IMAGE, "/t/client.mjs", f"http://{self.server}:8080/mcp", *args]

    def client(self, *args):
        result = docker("run", "--rm", *self.client_args(*args), timeout=120)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_the_health_path_answers_while_the_files_hang_and_calls_recover(self):
        self.assertEqual(self.client("read", "5000"), {"answer": True})
        docker("kill", "-s", "USR2", self.server)
        time.sleep(1)
        # The pool is held: a call that reads a note waits.
        self.assertEqual(self.client("read", "3000"), {"answer": "timeout"})
        docker("run", "-d", "--name", self.waiting, *self.client_args("read", "60000"))
        # The health path still answers, at once.
        for _ in range(3):
            health = self.client("health")
            self.assertEqual(health["status"], 200, health)
            self.assertLess(health["ms"], 2000)
        # The files come back: the waiting call ends with its note, and new calls work.
        self.assertGreaterEqual(self.release(), 4)
        self.assertEqual(docker("wait", self.waiting, timeout=90).stdout.strip(), "0")
        self.assertEqual(json.loads(docker("logs", self.waiting).stdout.strip().splitlines()[-1]), {"answer": True})
        self.assertEqual(self.client("read", "5000"), {"answer": True})
        self.assertEqual(self.client("health")["status"], 200)
        self.assertTrue(json.loads(docker("inspect", "-f", "{{json .State}}", self.server).stdout)["Running"])

if __name__ == "__main__":
    unittest.main()
