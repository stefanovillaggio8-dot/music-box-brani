#!/usr/bin/env python3
"""Safely normalise ONE Music Box MP3 with two-pass EBU R128 loudness.

This utility never changes any other track. --write replaces track-245 only
after checking the encoded output, otherwise leaves the source untouched.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

TRACK = Path("songs/track-245.mp3")
TARGET_LUFS = -14.0
TARGET_PEAK = -1.5
TARGET_LRA = 11.0

def command(*args):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=True)

def measure(path):
    proc = command("ffmpeg", "-nostdin", "-hide_banner", "-nostats",
                   "-i", str(path), "-vn", "-af",
                   "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                   "-f", "null", "-")
    matches = re.findall(r'\{\s*"input_i"\s*:[^{}]+\}', proc.stderr, re.S)
    if not matches:
        raise RuntimeError("Unable to obtain loudnorm measurement")
    data = json.loads(matches[-1])
    for key in ("input_i", "input_tp", "input_lra", "input_thresh",
                "target_offset"):
        data[key] = float(data[key])
        if not math.isfinite(data[key]):
            raise ValueError("Invalid loudness measurement: " + key)
    return data

def validate_file(path):
    result = json.loads(command("ffprobe", "-v", "quiet", "-show_format",
                                "-show_streams", "-of", "json", str(path)).stdout)
    audio = [s for s in result["streams"] if s.get("codec_type") == "audio"]
    if len(audio) != 1 or audio[0].get("codec_name") != "mp3":
        raise ValueError("Expected one standard MP3 stream")
    duration = float(result["format"]["duration"])
    if not 40 <= duration <= 900:
        raise ValueError("Audio duration outside normal music range")
    return duration

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if not TRACK.is_file():
        raise FileNotFoundError(TRACK)
    before_seconds = validate_file(TRACK)
    before = measure(TRACK)
    print("SOURCE_LUFS=" + str(before["input_i"]), flush=True)
    print("SOURCE_TRUE_PEAK_DBTP=" + str(before["input_tp"]), flush=True)
    print("SOURCE_DURATION_SECONDS=" + str(round(before_seconds, 2)), flush=True)

    # Do not re-encode tracks that are already at normal playback loudness.
    if before["input_i"] >= -15.2:
        print("RESULT=ALREADY_NORMAL_LOUDNESS; investigate player/alternate URL", flush=True)
        return

    args = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(TRACK), "-vn",
        "-af",
        ("loudnorm=I=-14:TP=-1.5:LRA=11:"
         f"measured_I={before['input_i']}:"
         f"measured_TP={before['input_tp']}:"
         f"measured_LRA={before['input_lra']}:"
         f"measured_thresh={before['input_thresh']}:"
         f"offset={before['target_offset']}:linear=true"),
        "-codec:a", "libmp3lame", "-q:a", "2",
        "-ar", "48000", "-id3v2_version", "3"
    ]
    with tempfile.TemporaryDirectory(prefix="musicbox-normalize-") as d:
        candidate = Path(d) / "track-245-normalized.mp3"
        command(*(args + [str(candidate)]))
        after_seconds = validate_file(candidate)
        after = measure(candidate)
        print("NORMALIZED_LUFS=" + str(after["input_i"]), flush=True)
        print("NORMALIZED_TRUE_PEAK_DBTP=" + str(after["input_tp"]), flush=True)
        print("NORMALIZED_DURATION_SECONDS=" + str(round(after_seconds, 2)), flush=True)
        if abs(after_seconds - before_seconds) > .15:
            raise ValueError("Re-encoding unexpectedly changed song duration")
        if abs(after["input_i"] - TARGET_LUFS) > 1.25:
            raise ValueError("Output loudness out of tolerance")
        if after["input_tp"] > -.85:
            raise ValueError("Encoded audio true peak exceeds safe ceiling")
        if before["input_i"] - after["input_i"] >= -.9:
            raise ValueError("Candidate does not significantly increase volume")
        if args.write:
            os.replace(candidate, TRACK)
            print("RESULT=NORMALIZED_FILE_REPLACED", flush=True)
        else:
            print("RESULT=VERIFIED_DRY_RUN", flush=True)

if __name__ == "__main__":
    main()
