// Test only (tests/test_image.py, TheHungShare): loaded into the server with
// --import. On SIGUSR2 it opens six named pipes in the share for reading.
// Each open waits in the kernel in a thread of the libuv pool, as a file
// call on a hung hard NFS mount does, so all four threads are held. The
// test opens the pipes for writing to let them go.
import { close, open } from "node:fs";

process.on("SIGUSR2", () => {
  for (let index = 0; index < 6; index += 1) {
    open(`/vault/hold-${index}.fifo`, "r", (error, fd) => {
      if (!error) close(fd, () => {});
    });
  }
});
