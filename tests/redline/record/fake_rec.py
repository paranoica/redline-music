"""A stand-in for the redline-rec binary: the same command line and JSON-lines protocol as
apps/redline-dsp/rec, no audio device.

Configured through the environment:
FAKE_REC_RATE      the graph rate (default 48000); a beat at another rate gets the binary's
                   wrong_rate line and exit 2
FAKE_REC_FAIL      "nojack": say JACK is missing and exit 2 before the startup line
FAKE_REC_DROPOUTS  JSON list of dropouts put in every take report
FAKE_REC_TONE      "sweep:<frames>": a stopped take holds the beat delayed by that many frames;
                   "silence": it holds zeros
FAKE_REC_QUANTUM   the quantum it reports instead of the one asked for
FAKE_REC_TAKE_S    seconds of the 220 Hz sine at 0.1 a take holds otherwise (default 0.5)
"""

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def reply(**fields):
    print(json.dumps(fields), flush=True)


class Fake:
    def __init__(self, beat, rate):
        self.beat = beat
        self.rate = rate
        self.take = None
        self.racks = 0
        self.quantum = None
        self.dropouts = json.loads(os.environ.get("FAKE_REC_DROPOUTS", "[]"))

    def samples(self):
        tone = os.environ.get("FAKE_REC_TONE", "")
        if tone.startswith("sweep:"):
            delay = int(tone.split(":")[1])
            played = sf.read(self.beat, dtype="float32", always_2d=True)[0][:, 0]
            out = np.zeros(delay + len(played) + self.rate // 4, dtype=np.float32)
            out[delay : delay + len(played)] = 0.5 * played
            return out
        seconds = float(os.environ.get("FAKE_REC_TAKE_S", "0.5"))
        if tone == "silence":
            return np.zeros(int(seconds * self.rate), dtype=np.float32)
        t = np.arange(int(seconds * self.rate)) / self.rate
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
            quantum=int(os.environ.get("FAKE_REC_METERS_QUANTUM", self.quantum)),
        )


def startup(rate, quantum, round_trip, connect, beat_frames):
    capture, playback = [quantum, quantum], [128, 256]
    source = "calibrated" if round_trip is not None else "port_latency_estimate"
    if round_trip is None:
        round_trip = capture[1] + playback[1]
    whole = round(0.010 * rate)
    ports = ("fake:capture_1", "fake:playback_1", "fake:playback_2") if connect else (None,) * 3
    reply(
        event="started",
        client="redline-rec",
        rate=rate,
        quantum=quantum,
        requested_quantum=quantum,
        latency_request=f"{quantum}/{rate}",
        capture_latency=capture,
        playback_latency=playback,
        round_trip=round_trip,
        round_trip_ms=round_trip * 1000 / rate,
        round_trip_source=source,
        under_10ms=round_trip < whole,
        monitor_budget=max(0, whole - round_trip),
        mic=ports[0],
        left=ports[1],
        right=ports[2],
        mic_aliases=[],
        left_aliases=[],
        right_aliases=[],
        beat_frames=beat_frames,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("beat")
    parser.add_argument("--quantum", type=int, default=64)
    parser.add_argument("--round-trip", type=int)
    parser.add_argument("--no-connect", action="store_true")
    args = parser.parse_args()
    if os.environ.get("FAKE_REC_FAIL") == "nojack":
        print("redline-rec: cannot connect to JACK: is PipeWire running?", file=sys.stderr)
        sys.exit(2)
    rate = int(os.environ.get("FAKE_REC_RATE", "48000"))
    beat = sf.info(args.beat)
    beat_rate = beat.samplerate
    if beat_rate != rate:
        reply(event="wrong_rate", rate=rate, beat_rate=beat_rate)
        print(
            f"redline-rec: the beat is at {beat_rate} Hz and the audio graph runs at {rate} Hz",
            file=sys.stderr,
        )
        sys.exit(2)
    quantum = int(os.environ.get("FAKE_REC_QUANTUM", args.quantum))
    startup(rate, quantum, args.round_trip, not args.no_connect, beat.frames)
    fake = Fake(args.beat, rate)
    fake.quantum = quantum
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
