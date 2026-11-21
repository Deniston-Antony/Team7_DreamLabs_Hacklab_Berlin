"""Record the top camera of a sim run to an MP4, and join several runs into one grid video.

`attach(env, path)` wraps `env.images`, which dream_control.capture calls once per 50 ms control step, so the video
plays in simulated time (the sim holds while Dream-1 thinks) at 20 frames per second. The run itself is unchanged.
"""

import av
import numpy as np
from PIL import Image, ImageDraw

FPS = 20
CAMERA = "top"
GREEN, RED = (46, 160, 67), (200, 50, 50)


class Recorder:
    """Writes RGB frames to an H.264 MP4."""

    def __init__(self, path, size=(640, 360), fps=FPS):
        """Open the file.
        @param path Where to write the MP4.
        @param size Width and height of every frame, both even.
        @param fps Frames per second the video plays at.
        """
        self.path = path
        self.container = av.open(str(path), "w")
        self.stream = self.container.add_stream("libx264", rate=fps)
        self.stream.width, self.stream.height = size
        self.stream.pix_fmt = "yuv420p"
        self.stream.options = {"crf": "23", "preset": "veryfast"}
        self.frames = 0

    def add(self, image):
        """Append one frame.
        @param image Height x width x 3 uint8 RGB array of the size given to the constructor.
        """
        frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(image), format="rgb24")
        for packet in self.stream.encode(frame):
            self.container.mux(packet)
        self.frames += 1

    def close(self):
        """Finish the file."""
        for packet in self.stream.encode():
            self.container.mux(packet)
        self.container.close()


class Attached:
    """A Recorder fed by an environment's own camera renders."""

    def __init__(self, env, path):
        """Start recording `env`'s top camera.
        @param env Simulation environment whose images() is wrapped.
        @param path Where to write the MP4.
        """
        self.env, self.recorder, self.failed = env, Recorder(path), False
        original = env.images

        def images():
            result = original()
            if not self.failed:
                try:
                    self.recorder.add(result[CAMERA])
                except Exception as error:  # a recording problem must never stop the run
                    self.failed = True
                    print(f"  Recording stopped: {error}", flush=True)
            return result

        env.images = images  # an instance attribute, which shadows the method until close()

    def close(self):
        """Stop recording, finish the file and give the environment its own images() back."""
        self.env.__dict__.pop("images", None)
        try:
            self.recorder.close()
        except Exception as error:
            print(f"  Recording could not be finished: {error}", flush=True)


def attach(env, path):
    """Record an environment's top camera until the returned object's close().
    @param env Simulation environment.
    @param path Where to write the MP4.
    @return An Attached; call close() when the run ends.
    """
    return Attached(env, path)


def read_frames(path):
    """All frames of a video.
    @param path The MP4.
    @return List of height x width x 3 uint8 RGB arrays.
    """
    with av.open(str(path)) as container:
        return [frame.to_ndarray(format="rgb24") for frame in container.decode(video=0)]


def grid(entries, out, width=1280):
    """Join several runs into one video, side by side, each labelled and framed green or red.
    @param entries List of (path, label, success): an attempt's MP4, its caption and whether it succeeded.
    @param out Where to write the grid MP4.
    @param width Width of the whole grid in pixels.
    @return The number of frames written; 0 when no entry could be read.
    """
    clips = []
    for path, label, success in entries:
        try:
            frames = read_frames(path)
        except (OSError, av.error.FFmpegError):
            continue
        if frames:
            clips.append((frames, label, success))
    if not clips:
        return 0
    columns = 1 if len(clips) == 1 else 2 if len(clips) <= 4 else 3 if len(clips) <= 9 else 4
    rows = -(-len(clips) // columns)
    panel_w = (width // columns) // 2 * 2
    panel_h = (panel_w * 9 // 16) // 2 * 2
    length = max(len(frames) for frames, _, _ in clips)
    recorder = Recorder(out, size=(panel_w * columns, panel_h * rows))
    try:
        for index in range(length):
            canvas = np.zeros((panel_h * rows, panel_w * columns, 3), dtype=np.uint8)
            for number, (frames, label, success) in enumerate(clips):
                frame = frames[min(index, len(frames) - 1)]  # a finished run holds its last frame
                tile = Image.fromarray(frame).resize((panel_w, panel_h))
                draw = ImageDraw.Draw(tile)
                colour = GREEN if success else RED
                draw.rectangle([0, 0, panel_w - 1, panel_h - 1], outline=colour, width=4)
                draw.rectangle([0, 0, panel_w - 1, 22], fill=colour)
                draw.text((8, 5), label, fill=(255, 255, 255))
                top, left = divmod(number, columns)
                canvas[top * panel_h : (top + 1) * panel_h, left * panel_w : (left + 1) * panel_w] = np.asarray(tile)
            recorder.add(canvas)
    finally:
        recorder.close()
    return length
