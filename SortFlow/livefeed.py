"""Stream a run's camera frames to another process over a pipe, newest frame only, without ever slowing the run.

A worker process attaches a `Sender` to its environment; the process that started it reads the pipe with
`read_messages`. Frames are JPEGs of the three cameras, plus small status messages. The sender keeps only the newest
frame of each kind, so a reader that is slow or gone never makes the simulation wait: the writer thread blocks, the
simulation does not.

Wire format, repeated: `>BI` header (kind, length) then `length` bytes. Kind 0 is a JSON status, kinds 1 to 3 are
the top, left and right camera as JPEG.
"""

import io
import json
import struct
import threading
import time

from PIL import Image

CAMERAS = ("top", "left", "right")
STATUS = 0
FREE = 4
HEADER = struct.Struct(">BI")
EVERY = 4  # send one frame in this many renders: dream_control renders every 50 ms step, so 5 frames per second
QUALITY = 70
STATUS_EVERY_S = 0.5


def kind_name(kind):
    """The camera name of a frame kind, or "status" for kind 0."""
    if kind == STATUS:
        return "status"
    return "free" if kind == FREE else CAMERAS[kind - 1]


def pack(kind, payload):
    """One message as bytes."""
    return HEADER.pack(kind, len(payload)) + payload


def read_messages(stream):
    """Yield (kind, payload) from a binary stream until it ends, or until a message is cut off.
    @param stream A binary file object, such as os.fdopen(fd, "rb").
    """
    while True:
        header = stream.read(HEADER.size)
        if len(header) < HEADER.size:
            return
        kind, length = HEADER.unpack(header)
        payload = stream.read(length)
        if len(payload) < length:
            return
        yield kind, payload


def jpeg(image):
    """A JPEG of an RGB array."""
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="JPEG", quality=QUALITY)
    return buffer.getvalue()


class Sender(threading.Thread):
    """Writes the newest frame of each kind to a pipe on its own thread."""

    def __init__(self, stream):
        """Start the writer thread.
        @param stream A binary file object opened for writing, such as os.fdopen(fd, "wb", buffering=0).
        """
        super().__init__(daemon=True)
        self.stream = stream
        self.slots = {}
        self.wake = threading.Condition()
        self.closed = False
        self.start()

    def put(self, kind, item):
        """Offer an item (an RGB array for a camera, bytes for a status); it replaces any unsent one of that kind."""
        with self.wake:
            self.slots[kind] = item
            self.wake.notify()

    def run(self):
        """Encode and write until closed, or until the reader goes away."""
        try:
            while True:
                with self.wake:
                    while not self.slots and not self.closed:
                        self.wake.wait()
                    if not self.slots:
                        return
                    items, self.slots = self.slots, {}
                for kind, item in sorted(items.items()):
                    self.stream.write(pack(kind, item if kind == STATUS else jpeg(item)))
        except (OSError, ValueError):  # the reader closed the pipe, or the stream was closed under us
            return

    def close(self, timeout=2.0):
        """Send what is left, stop the thread and close the stream."""
        with self.wake:
            self.closed = True
            self.wake.notify()
        self.join(timeout)
        try:
            self.stream.close()
        except OSError:
            pass


class Attached:
    """A Sender fed by an environment's own camera renders."""

    def __init__(self, env, stream, every=EVERY, free=None):
        """Start streaming `env`'s three cameras.
        @param env Simulation environment whose images() is wrapped.
        @param stream A binary file object opened for writing.
        @param every Send one frame in this many renders.
        @param free Optional callable returning an RGB image of the 3D view, or None when nobody is watching it.
        """
        self.env, self.sender, self.every = env, Sender(stream), every
        self.free = free
        self.renders, self.last_status, self.state, self.extra = 0, 0.0, "running", {}
        original = env.images
        self._wrapped_before = "images" in env.__dict__

        def images():
            result = original()
            self.renders += 1
            if self.renders % self.every == 1:
                for index, name in enumerate(CAMERAS, 1):
                    self.sender.put(index, result[name])
                view = self.free() if self.free else None
                if view is not None:
                    self.sender.put(FREE, view)
            now = time.monotonic()
            if now - self.last_status >= STATUS_EVERY_S:
                self.last_status = now
                self.status(self.state)
            return result

        self._original = original
        env.images = images

    def status(self, state, **extra):
        """Send a status message: the state ("running", "succeeded", "failed", "error"), the simulated time, extras."""
        self.state, self.extra = state, {**self.extra, **extra}
        message = {"state": state, "sim_time": round(float(self.env.data.time), 1), **self.extra}
        self.sender.put(STATUS, json.dumps(message).encode())

    def close(self):
        """Stop streaming, flush, and give the environment the images() it had before."""
        if self._wrapped_before:
            self.env.images = self._original
        else:
            self.env.__dict__.pop("images", None)
        self.sender.close()


def attach(env, stream, every=EVERY, free=None):
    """Stream an environment's cameras to a pipe until the returned object's close().
    @param env Simulation environment.
    @param stream A binary file object opened for writing.
    @param every Send one frame in this many renders.
    @param free Optional callable returning an RGB image of the 3D view.
    @return An Attached; call status() with the outcome and close() when the run ends.
    """
    return Attached(env, stream, every, free)
