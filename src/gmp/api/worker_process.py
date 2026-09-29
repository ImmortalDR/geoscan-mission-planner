"""Drain worker pipes continuously while retaining only bounded diagnostics."""
import subprocess
import threading


class BoundedProcess:
    def __init__(self, command, *, env, limit=65536):
        self.process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.tails = [bytearray(), bytearray()]
        self.readers = []
        for stream, tail in zip((self.process.stdout, self.process.stderr), self.tails):
            def drain(stream=stream, tail=tail):
                try:
                    while chunk := stream.read(4096):
                        tail.extend(chunk)
                        if len(tail) > limit:
                            del tail[:-limit]
                finally:
                    stream.close()
            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            self.readers.append(reader)

    @property
    def returncode(self):
        return self.process.returncode

    def poll(self):
        return self.process.poll()

    def kill(self):
        self.process.kill()

    def communicate(self, timeout=None):
        self.process.wait(timeout=timeout)
        for reader in self.readers:
            reader.join()
        return tuple(t.decode("utf-8", errors="replace") for t in self.tails)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.poll() is None:
            self.kill()
        self.communicate()
