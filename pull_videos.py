#!/usr/bin/env python3
"""Pull videos from a connected Android device via ADB."""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


def run_adb(args: list[str], device_id: str | None = None) -> subprocess.CompletedProcess:
    cmd = ["adb"]
    if device_id:
        cmd += ["-s", device_id]
    cmd += args
    return subprocess.run(cmd, capture_output=True, text=True)


def get_devices() -> list[str]:
    result = run_adb(["devices"])
    lines = result.stdout.strip().splitlines()
    devices = []
    for line in lines[1:]:
        line = line.strip()
        if line and "\t" in line:
            device_id, status = line.split("\t", 1)
            if status.strip() == "device":
                devices.append(device_id.strip())
    return devices


def find_videos(device_id: str, start_epoch: int, end_epoch: int | None = None) -> list[dict]:
    search_dirs = [
        "/sdcard/DCIM",
        "/sdcard/Movies",
        "/sdcard/Videos",
        "/sdcard/Download",
    ]

    videos = []
    for search_dir in search_dirs:
        result = run_adb(
            ["shell", f"find {search_dir} -type f \\( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.mkv' -o -iname '*.avi' \\) 2>/dev/null"],
            device_id,
        )
        if result.returncode != 0 or not result.stdout.strip():
            continue

        for path in result.stdout.strip().splitlines():
            path = path.strip()
            if not path:
                continue

            stat_result = run_adb(
                ["shell", f"stat -c '%Y %s' \"{path}\" 2>/dev/null"],
                device_id,
            )
            if stat_result.returncode != 0 or not stat_result.stdout.strip():
                continue

            parts = stat_result.stdout.strip().split()
            if len(parts) < 2:
                continue

            try:
                mtime = int(parts[0])
                size_bytes = int(parts[1])
            except ValueError:
                continue

            if mtime < start_epoch:
                continue
            if end_epoch is not None and mtime > end_epoch:
                continue

            recorded_at = datetime.fromtimestamp(mtime)
            videos.append({
                "path": path,
                "name": os.path.basename(path),
                "date": recorded_at,
                "size_bytes": size_bytes,
            })

    videos.sort(key=lambda v: v["date"], reverse=True)
    return videos


def format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def parse_selection(selection: str, count: int) -> list[int]:
    """Parse a selection string like '1,3,5-8' into a sorted list of 0-based indices."""
    indices = set()
    for part in selection.split(","):
        part = part.strip()
        if not part:
            continue
        if part.lower() == "all":
            return list(range(count))
        match = re.fullmatch(r"(\d+)-(\d+)", part)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            for i in range(start, end + 1):
                if 1 <= i <= count:
                    indices.add(i - 1)
        elif re.fullmatch(r"\d+", part):
            i = int(part)
            if 1 <= i <= count:
                indices.add(i - 1)
        else:
            print(f"  Ignoring unrecognized token: '{part}'")
    return sorted(indices)


def pull_video(device_id: str, remote_path: str, local_path: Path) -> bool:
    print(f"  Pulling {local_path.name}...")
    result = subprocess.run(
        ["adb", "-s", device_id, "pull", remote_path, str(local_path)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def parse_date(s: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"Unrecognized date format: '{s}'. Use YYYY-MM-DD.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull videos from a connected Android device via ADB.")
    parser.add_argument("-d", "--dest", type=Path, default=Path("~/Volleyball").expanduser(), help="Destination directory (default: ~/Volleyball)")

    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("-n", "--days", type=int, default=None, help="Videos recorded in the last N days (default: 7)")
    date_group.add_argument("--date", type=parse_date, metavar="YYYY-MM-DD", help="Videos recorded on a specific day")
    date_group.add_argument("--from", dest="date_from", type=parse_date, metavar="YYYY-MM-DD", help="Start of date range (inclusive); use with --to")
    parser.add_argument("--to", dest="date_to", type=parse_date, metavar="YYYY-MM-DD", help="End of date range (inclusive); use with --from")

    args = parser.parse_args()

    if args.date_to is not None and args.date_from is None:
        parser.error("--to requires --from")

    # Resolve time range
    if args.date is not None:
        start_epoch = int(args.date.replace(hour=0, minute=0, second=0).timestamp())
        end_epoch = int(args.date.replace(hour=23, minute=59, second=59).timestamp())
        range_desc = f"on {args.date.strftime('%Y-%m-%d')}"
    elif args.date_from is not None:
        start_epoch = int(args.date_from.replace(hour=0, minute=0, second=0).timestamp())
        end_dt = (args.date_to or args.date_from).replace(hour=23, minute=59, second=59)
        end_epoch = int(end_dt.timestamp())
        if args.date_to:
            range_desc = f"from {args.date_from.strftime('%Y-%m-%d')} to {args.date_to.strftime('%Y-%m-%d')}"
        else:
            range_desc = f"on {args.date_from.strftime('%Y-%m-%d')}"
    else:
        days = args.days if args.days is not None else 7
        start_epoch = int((datetime.now() - timedelta(days=days)).timestamp())
        end_epoch = None
        range_desc = f"in the last {days} day(s)"

    # Check for connected devices
    print("Scanning for connected ADB devices...")
    devices = get_devices()
    if not devices:
        print("No connected Android devices found. Make sure USB debugging is enabled and the device is connected.")
        sys.exit(1)

    if len(devices) == 1:
        device_id = devices[0]
        print(f"Using device: {device_id}")
    else:
        print("Multiple devices found:")
        for i, d in enumerate(devices, 1):
            print(f"  {i}. {d}")
        while True:
            choice = input("Select device number: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(devices):
                device_id = devices[int(choice) - 1]
                break
            print("  Invalid choice, try again.")

    # Find videos
    print(f"\nSearching for videos recorded {range_desc}...")
    videos = find_videos(device_id, start_epoch, end_epoch)

    if not videos:
        print(f"No videos found {range_desc}.")
        sys.exit(0)

    print(f"\nFound {len(videos)} video(s):\n")
    print(f"  {'#':>3}  {'Date':>19}  {'Size':>9}  Name")
    print(f"  {'-'*3}  {'-'*19}  {'-'*9}  {'-'*30}")
    for i, v in enumerate(videos, 1):
        date_str = v["date"].strftime("%Y-%m-%d %H:%M:%S")
        size_str = format_size(v["size_bytes"])
        print(f"  {i:>3}  {date_str}  {size_str:>9}  {v['name']}")

    # Selection
    print("\nEnter video numbers to pull (e.g. '1,3,5-8' or 'all', or press Enter to cancel):")
    selection = input("> ").strip()
    if not selection:
        print("No selection made. Exiting.")
        sys.exit(0)

    indices = parse_selection(selection, len(videos))
    if not indices:
        print("No valid videos selected. Exiting.")
        sys.exit(0)

    selected = [videos[i] for i in indices]
    print(f"\nSelected {len(selected)} video(s).")

    # Prepare destination
    args.dest.mkdir(parents=True, exist_ok=True)
    print(f"Saving to: {args.dest}\n")

    # Pull videos
    success, failed = 0, 0
    for video in selected:
        local_path = args.dest / video["name"]
        if local_path.exists():
            print(f"  Skipping {video['name']} (already exists at destination)")
            success += 1
            continue

        ok = pull_video(device_id, video["path"], local_path)
        if ok:
            success += 1
        else:
            print(f"  Failed to pull {video['name']}")
            failed += 1

    print(f"\nDone. {success} pulled, {failed} failed.")


if __name__ == "__main__":
    main()
