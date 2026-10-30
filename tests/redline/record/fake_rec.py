"""A stand-in for the redline-rec binary: the same JSON-lines protocol, no audio device.

Configured through the environment:
FAKE_REC_RATE      the graph rate it reports (default 48000)
FAKE_REC_FAIL      "nojack": say JACK is missing and exit 2 before the startup line
FAKE_REC_DROPOUTS  JSON list of dropouts put in every take report
FAKE_REC_TONE      "sweep:<frames>": a stopped take holds the beat delayed by that many frames
"""

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

TAKE_SECONDS = 0.5


def reply(**fields):
    print(json.dumps(fields), flush=True)


class Fake:
    def __init__(self, beat, rate):
        self.beat = beat
        self.rate = rate
        self.take = None
        self.racks = 0
        self.dropouts = json.loads(os.environ.get("FAKE_REC_DROPOUTS", "[]"))

    def samples(self):
        tone = os.environ.get("FAKE_REC_TONE", "")
        if tone.startswith("sweep:"):
            delay = int(tone.split(":")[1])
            played = sf.read(self.beat, dtype="float32", always_2d=True)[0][:, 0]
            out = np.zeros(delay + len(played) + self.rate // 4, dtype=np.float32)
            out[delay : delay + len(played)] = 0.5 * played
            return out
        t = np.arange(int(TAKE_SECONDS * self.rate)) / self.rate
        return (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    def start(self, command):
        if self.take is not None:
            return reply(reply="error", error="a take is already being recorded")
        if Path(command["path"]).exists():
            return reply(reply="error", error=f"{command['path']} already exists")
        self.take = (command["path"], command["offset"])
        sf.write(self.take[0], np.zeros(1, dtype=np.float32), self.rate, subtype="FLOAT")
        return reply(reply="start", path=self.take[0])

    def stop(self, command):
        if self.take is None:
            return reply(reply="error", error="no take is being recorded")
        samples = self.samples()
        sf.write(self.take[0], samples, self.rate, subtype="FLOAT")
        path, offset = self.take
        self.take = None
        take = {
            "path": path,
            "rate": self.rate,
            "frames": len(samples),
            "offset": offset,
            "dropouts": self.dropouts,
        }
        return reply(reply="stop", take=take)

    def set_rack(self, command):
        self.racks += 1
        effects = command["rack"]["effects"]
        dropped = []
        budget = command.get("budget")
        while budget is not None and 10 * len(effects) > budget and effects:
            dropped.append(effects.pop()["effect"])
        return reply(reply="set_rack", latency=10 * len(effects), dropped=dropped)

    def meters(self, command):
        return reply(
            reply="meters",
            mic_peak=0.5,
            out_peak=0.25,
            xruns=self.racks,
            overflows=0,
            recording=self.take is not None,
            take_frames=4800 if self.take else 0,
            rack_latency=0,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--beat", required=True)
    parser.add_argument("--quantum", type=int, default=64)
    args = parser.parse_args()
    if os.environ.get("FAKE_REC_FAIL") == "nojack":
        print("redline-rec: cannot connect to JACK: is PipeWire running?", file=sys.stderr)
        sys.exit(2)
    rate = int(os.environ.get("FAKE_REC_RATE", "48000"))
    reply(
        ready=True,
        rate=rate,
        quantum=args.quantum,
        latency={"capture": [args.quantum, args.quantum], "playback": [128, 256]},
        devices=["fake:capture_1", "fake:playback_1"],
        beat_rate=sf.info(args.beat).samplerate,
    )
    fake = Fake(args.beat, rate)
    handlers = {
        "start": fake.start,
        "stop": fake.stop,
        "set_rack": fake.set_rack,
        "meters": fake.meters,
    }
    for line in sys.stdin:
        if not line.strip():
            continue
        command = json.loads(line)
        if command.get("cmd") == "quit":
            reply(reply="quit")
            return
        handler = handlers.get(command.get("cmd"))
        if handler is None:
            reply(reply="error", error=f"unknown command: {line.strip()}")
        else:
            handler(command)


if __name__ == "__main__":
    main()
