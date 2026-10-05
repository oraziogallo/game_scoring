import json
import os
import sys
import glob
import subprocess
import shutil
import yt_dlp
import platform
import traceback
import stat
import threading
import tkinter as tk
from tkinter import ttk
import re
import argparse
from tqdm import tqdm

FINAL_SCORE_EXTRA_SECS = 3  # extra seconds appended to the last segment to show the final score
GAP_SLOTS = 2               # height of a gap in the recording on the progress line, in plays

# --- GLOBAL GUI VARIABLES ---
root = None
progress_var = None
cli_mode = False
cli_warning_log = None   # WarningCapture instance in CLI mode
status_var = None
close_button = None
abort_button = None
abort_event = threading.Event()

class WarningCapture:
    """Wraps a log file and tracks whether anything was written to it."""
    def __init__(self, path):
        self._f = open(path, 'w', buffering=1)
        self.had_warnings = False

    def write(self, s):
        if s.strip():
            self.had_warnings = True
        self._f.write(s)

    def flush(self):
        self._f.flush()

    def close(self):
        self._f.close()

    # yt_dlp logger interface
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg):
        self.write(f"WARNING: {msg}\n")
    def error(self, msg):
        self.write(f"ERROR: {msg}\n")

# --- 1. LOGGING SETUP ---
def setup_logging():
    """Redirects standard output and errors to a log file next to the executable."""
    
    if getattr(sys, 'frozen', False):
        application_path = os.path.dirname(sys.executable)
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))

    log_file = os.path.join(application_path, "debug.log")
    
    log_fs = open(log_file, "w", buffering=1)
    
    sys.stdout = log_fs
    sys.stderr = log_fs
    
    print("="*60)
    print(f"🚀 NEW SESSION STARTED: {sys.argv}")
    print(f"📂 Execution Directory: {os.getcwd()}")
    print(f"📝 Log File: {log_file}")

# --- GUI HELPERS ---
def update_gui(progress_percent, message):
    if root:
        def _update():
            if progress_var: progress_var.set(progress_percent)
            if status_var: status_var.set(message)
        root.after(0, _update)

def show_finish_state(message="Done!"):
    if root:
        def _finish():
            if status_var: status_var.set(message)
            if progress_var: progress_var.set(100)
            if close_button:
                close_button.config(state="normal")
                close_button.config(text="Close Window")
            if abort_button:
                abort_button.config(state="disabled")
            os.system(f"""osascript -e 'display notification "{message}" with title "game_scoring"'""")
        root.after(0, _finish)
    else:
        print(f"✅ {message}")

def show_error_state(error_msg):
    if root:
        def _err():
            if status_var: status_var.set(f"Stopped: {error_msg}")
            if close_button:
                close_button.config(state="normal")
                close_button.config(text="Close Window")
            if abort_button:
                abort_button.config(state="disabled")
        root.after(0, _err)
    else:
        print(f"❌ Error: {error_msg}", file=sys.stderr)

def trigger_abort():
    if status_var: status_var.set("Aborting... please wait.")
    if abort_button: abort_button.config(state="disabled")
    abort_event.set()

# --- UTILS ---
def get_font_path():
    system = platform.system()
    if system == "Windows": return "C\\\\:/Windows/Fonts/arial.ttf"
    elif system == "Darwin": return "/System/Library/Fonts/Helvetica.ttc"
    else: return "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def get_ffmpeg_path():
    if getattr(sys, 'frozen', False):
        # Logic for the compiled app
        base_path = sys._MEIPASS
        return os.path.join(base_path, 'ffmpeg')
    else:
        # Logic for local debugging
        return "/opt/homebrew/bin/ffmpeg"

def is_highlight(seg):
    """Truthy test for the optional 'highlight' field written by the web UI.

    The field is absent on plays that are not highlights, so older JSON files
    simply report False."""
    v = seg.get('highlight')
    if isinstance(v, str):
        return v.strip().lower() in ('yes', 'true', '1')
    return bool(v)

def get_resume_score(seg):
    """The optional 'resumeScore' field the web UI writes on the first play
    after a gap in the recording: the real score just before that play, as a
    (t1, t2) tuple. None for every other play, and for older JSON files."""
    r = seg.get('resumeScore')
    try:
        return int(r['t1']), int(r['t2'])
    except (TypeError, KeyError, ValueError):
        return None

def get_game_order(data):
    """The optional top-level 'gameOrder' the web UI writes: where this game
    falls among the games of the same set (1, 2, ...). None when unset."""
    try:
        return int(data.get('gameOrder'))
    except (TypeError, ValueError):
        return None

def output_video_name(json_file, highlights_only=False):
    """<json name>.mp4, prefixed with 'N_' when the JSON has a game order.
    The web UI already names ordered JSON files N_..., so the prefix is only
    added when the name does not start with it."""
    base = os.path.splitext(os.path.basename(json_file))[0]
    try:
        with open(json_file, 'r') as f:
            order = get_game_order(json.load(f))
    except (OSError, ValueError, AttributeError):
        order = None   # unreadable here; run_processing_logic reports it
    prefix = f"{order}_" if order is not None else ""
    if base.startswith(prefix):
        prefix = ""
    suffix = "_highlights" if highlights_only else ""
    return f"{prefix}{base}{suffix}.mp4"

def get_video_dimensions(filepath, ffmpeg_exe):
    try:
        cmd = [ffmpeg_exe, "-i", filepath]
        result = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
        match = re.search(r"Video:.*,\s*(\d{3,5})x(\d{3,5})", result.stderr)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception as e:
        print(f"⚠️ Resolution detection failed: {e}")
    return 1920, 1080

# --- CORE LOGIC (THREADED) ---
def run_processing_logic(args, highlights_only=False):
    temp_dir = None
    processed_dir = None
    list_file_path = None

    def cleanup_workspace():
        print("🧹 Cleaning workspace...")
        try:
            if temp_dir and os.path.exists(temp_dir): shutil.rmtree(temp_dir)
            if processed_dir and os.path.exists(processed_dir): shutil.rmtree(processed_dir)
            if list_file_path and os.path.exists(list_file_path): os.remove(list_file_path)
        except Exception as e:
            print(f"Warning during cleanup: {e}")

    try:
        if not cli_mode:
            setup_logging()

        if '-p' in args: args.remove('-p')

        if not args:
            show_error_state("No file dropped.")
            return

        # Absolute, because we chdir into the JSON's folder below
        target_arg = os.path.abspath(args[0])
        update_gui(5, "Initializing...")
        
        json_file = None
        work_dir = "."

        if os.path.isfile(target_arg) and target_arg.lower().endswith('.json'):
            json_file = target_arg
            work_dir = os.path.dirname(target_arg) or "."
        elif os.path.isdir(target_arg):
            work_dir = target_arg
            files = glob.glob(os.path.join(work_dir, "*.json"))
            if len(files) == 1: json_file = files[0]
            else:
                show_error_state("Multiple/No JSON found.")
                return
        else:
            show_error_state("File not found.")
            return

        os.chdir(work_dir)
        
        temp_dir = os.path.join(work_dir, "temp_clips")
        processed_dir = os.path.join(work_dir, "processed_clips")
        list_file_path = os.path.join(work_dir, "ffmpeg_list.txt")
        output_video = os.path.join(work_dir, output_video_name(json_file, highlights_only))

        # Permissions Fix
        ffmpeg_exe = get_ffmpeg_path()
        try:
            if os.path.exists(ffmpeg_exe):
                st = os.stat(ffmpeg_exe)
                os.chmod(ffmpeg_exe, st.st_mode | stat.S_IEXEC)
        except: pass
        
        ffmpeg_dir = os.path.dirname(ffmpeg_exe)
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ["PATH"]

        update_gui(10, "Reading JSON...")
        all_segments = []
        
        # --- PARSING MODES ---
        source_mode = 'youtube'
        local_video_path = None

        with open(json_file, 'r') as f:
            data = json.load(f)
            
            if data.get('mode') == 'local':
                source_mode = 'local'
            elif data.get('videoId'):
                source_mode = 'youtube'
            
            video_id = data.get('videoId')
            video_title = data.get('videoTitle', '')
            
            if source_mode == 'local':
                possible_path = os.path.join(work_dir, video_title)
                if not os.path.exists(possible_path):
                     show_error_state(f"Missing video: {video_title}\nMove JSON to video folder.")
                     return
                local_video_path = possible_path

            t1_name = data.get('team1', 'Home')
            t2_name = data.get('team2', 'Away')
            segments = data.get('segments', [])
            
            # Both the score before and after each play come from the JSON:
            # after a gap in the recording the score jumps, so it cannot be
            # rebuilt by counting points from the start.
            prev_s1, prev_s2 = 0, 0
            for seg in segments:
                score = seg.get('scoreState', {'t1':0, 't2':0})
                resume = get_resume_score(seg)
                if resume: prev_s1, prev_s2 = resume
                winner = 0
                if score['t1'] > prev_s1: winner = 1
                elif score['t2'] > prev_s2: winner = 2

                all_segments.append({
                    'video_id': video_id,
                    'start': seg['start'], 'end': seg['end'],
                    't1_name': t1_name.replace(":", "\\:").replace("'", ""),
                    't2_name': t2_name.replace(":", "\\:").replace("'", ""),
                    'prev_s1': prev_s1, 'prev_s2': prev_s2,
                    's1': score['t1'], 's2': score['t2'], 'winner': winner,
                    'after_gap': resume is not None,
                    'highlight': is_highlight(seg)
                })
                prev_s1, prev_s2 = score['t1'], score['t2']

        if not all_segments:
            show_error_state("No segments in JSON.")
            return

        if highlights_only:
            # Scores were accumulated over every play above, so filtering here
            # keeps the running score correct even though we do not draw it.
            all_segments = [s for s in all_segments if s['highlight']]
            if not all_segments:
                show_error_state("No plays marked as highlight in JSON.")
                return
            if cli_mode:
                print(f"Found {len(all_segments)} highlight play(s)")

        total_segs = len(all_segments)

        # Where each play sits on the progress line, counted in plays: one
        # slot per play, plus an empty stretch before every play that
        # resumes after a gap in the recording.
        play_slot, gap_slots = [], []   # gap_slots: (slot, index of the play after the gap)
        slot = 0
        for k, s in enumerate(all_segments):
            if s['after_gap']:
                gap_slots.append((slot, k))
                slot += GAP_SLOTS
            play_slot.append(slot)
            slot += 1
        total_slots = slot

        os.makedirs(temp_dir, exist_ok=True)
        os.makedirs(processed_dir, exist_ok=True)

        downloaded_clips = []
        font_path = get_font_path()

        seg_iter = enumerate(all_segments)
        if cli_mode:
            seg_iter = tqdm(seg_iter, total=total_segs, desc="Segments", unit="seg", file=sys.stdout)
        for i, seg in seg_iter:
            if abort_event.is_set():
                print("ABORT SIGNAL RECEIVED.")
                cleanup_workspace()
                show_error_state("Aborted by user.")
                return

            percent = 10 + int((i / total_segs) * 80)
            update_gui(percent, f"Processing Clip {i+1} of {total_segs}...")

            prefix = "hl_" if highlights_only else ""
            raw_filename = os.path.join(temp_dir, f"raw_{prefix}{i:03d}.mp4")
            final_filename = os.path.join(processed_dir, f"clip_{prefix}{i:03d}.mp4")

            # The tail exists only to hold the final score on screen, and the
            # highlight reel has no scoreboard.
            is_last_seg = (i == total_segs - 1)
            clip_end = seg['end'] + (FINAL_SCORE_EXTRA_SECS if is_last_seg and not highlights_only else 0)

            if os.path.exists(final_filename):
                downloaded_clips.append(final_filename)
                continue

            # --- GET RAW CLIP ---
            try:
                if source_mode == 'youtube':
                    url = f"https://www.youtube.com/watch?v={seg['video_id']}"
                    ydl_opts = {
                        'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                        'quiet': True, 'no_warnings': True,
                        'ffmpeg_location': ffmpeg_exe,
                        'outtmpl': raw_filename,
                        'download_ranges': lambda info, ydl, _s=seg['start'], _e=clip_end: [{'start_time': _s, 'end_time': _e}],
                        'postprocessor_args': ['-loglevel', 'error'],
                    }
                    if cli_warning_log:
                        ydl_opts['logger'] = cli_warning_log
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])

                elif source_mode == 'local':

                    start_sec = float(seg['start'])
                    duration = float(clip_end) - start_sec
                    
                    cmd_cut = [
                        ffmpeg_exe, "-y",
                        "-ss", str(start_sec),       # Seek to exact start
                        "-i", local_video_path,      # Input video
                        "-t", str(duration),         # Record for exact duration
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", 
                        raw_filename
                    ]
                    subprocess.run(cmd_cut, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # Validate raw file exists
                found = glob.glob(os.path.join(temp_dir, f"raw_{prefix}{i:03d}*"))
                if not found: 
                    print(f"Failed to generate raw clip for segment {i}")
                    continue
                downloaded_file = found[0]
                
                # --- OVERLAY PROCESSING ---
                # Force timestamps to start at 0 (Redundant check, but safe)
                filters = ["setpts=PTS-STARTPTS"]

                # The highlight reel keeps the frame clean: no scoreboard
                # and no progress timeline down the left edge.
                if not highlights_only:
                    vid_w, vid_h = get_video_dimensions(downloaded_file, ffmpeg_exe)
                
                    sb_height = int(vid_h * 0.15)
                    sb_y = vid_h - sb_height
                    accent_height = max(2, int(vid_h * 0.006))
                    box_height = int(sb_height * 0.5)
                    box_width = int(box_height * 1.2)
                    box_y = sb_y + (sb_height - box_height) // 2
                    center_x = vid_w // 2
                    box_t1_x = center_x - box_width
                    box_t2_x = center_x
                    font_score = int(box_height * 0.8)
                    font_team = int(sb_height * 0.25)
                    text_team_offset_x = int(box_width * 0.2)
                    prog_margin_top = int(vid_h * 0.05)
                    prog_available_h = vid_h - prog_margin_top - sb_height - int(vid_h * 0.02)
                    prog_line_x = int(vid_h * 0.05) + 14
                    prog_line_w = max(2, int(vid_h * 0.003))
                    prog_slot_h = min(prog_available_h / total_slots, vid_h * 0.05)
                    prog_gap = max(1, int(prog_slot_h * 0.1))
                    prog_box_dim = prog_slot_h - prog_gap

                    # The line is cut at every gap in the recording the game
                    # has reached so far, and dots bridge the missing points.
                    line_y = prog_margin_top
                    line_end = prog_margin_top + int(prog_available_h)
                    for g_slot, g_play in gap_slots:
                        if g_play > i: break
                        g_top = int(prog_margin_top + g_slot * prog_slot_h)
                        g_h = GAP_SLOTS * prog_slot_h
                        if g_top > line_y:
                            filters.append(f"drawbox=x={prog_line_x}:y={line_y}:w={prog_line_w}:h={g_top - line_y}:color=white@1:t=fill")
                        n_dots = max(1, min(3, int(g_h / (2 * prog_line_w)) - 1))
                        for d in range(n_dots):
                            dot_y = int(g_top + (d + 1) * g_h / (n_dots + 1) - prog_line_w / 2)
                            filters.append(f"drawbox=x={prog_line_x}:y={dot_y}:w={prog_line_w}:h={prog_line_w}:color=white@1:t=fill")
                        line_y = int(g_top + g_h)
                    if line_end > line_y:
                        filters.append(f"drawbox=x={prog_line_x}:y={line_y}:w={prog_line_w}:h={line_end - line_y}:color=white@1:t=fill")

                    for k in range(i + 1):
                        pt_winner = all_segments[k]['winner']
                        if pt_winner == 0: continue
                        y_pos = int(prog_margin_top + play_slot[k] * prog_slot_h)
                        if pt_winner == 1: x_pos = int(prog_line_x - prog_gap - prog_box_dim); color = "red@0.8"
                        else: x_pos = int(prog_line_x + prog_line_w + prog_gap); color = "blue@0.8"
                        box_cmd = f"drawbox=x={x_pos}:y={y_pos}:w={int(prog_box_dim)}:h={int(prog_box_dim)}:color={color}:t=fill"
                        if k == i:
                            trigger = max(0, (seg['end'] - seg['start']))
                            box_cmd += f":enable='gt(t,{trigger})'"
                        filters.append(box_cmd)

                    filters.append(f"drawbox=y={sb_y}:h={sb_height}:w={vid_w}:color=black@0.8:t=fill")
                    filters.append(f"drawbox=y={sb_y}:h={accent_height}:w={vid_w}:color=orange@1:t=fill")
                    filters.append(f"drawbox=x={box_t1_x}:y={box_y}:w={box_width}:h={box_height}:color=red@0.8:t=fill")
                    filters.append(f"drawbox=x={box_t2_x}:y={box_y}:w={box_width}:h={box_height}:color=blue@0.8:t=fill")
                
                    t1_text_x = box_t1_x - text_team_offset_x
                    t2_text_x = box_t2_x + box_width + text_team_offset_x
                    filters.append(f"drawtext=fontfile='{font_path}':text='{seg['t1_name']}':fontcolor=white:fontsize={font_team}:x={t1_text_x}-text_w:y={box_y}+(({box_height}-text_h)/2)")
                    filters.append(f"drawtext=fontfile='{font_path}':text='{seg['t2_name']}':fontcolor=white:fontsize={font_team}:x={t2_text_x}:y={box_y}+(({box_height}-text_h)/2)")

                    trigger_time = max(0, (seg['end'] - seg['start']))
                    prev_s1, prev_s2 = seg['prev_s1'], seg['prev_s2']

                    filters.append(f"drawtext=fontfile='{font_path}':text='{prev_s1}':fontcolor=white:fontsize={font_score}:x={box_t1_x}+(({box_width}-text_w)/2):y={box_y}+(({box_height}-text_h)/2):enable='lte(t,{trigger_time})'")
                    filters.append(f"drawtext=fontfile='{font_path}':text='{seg['s1']}':fontcolor=white:fontsize={font_score}:x={box_t1_x}+(({box_width}-text_w)/2):y={box_y}+(({box_height}-text_h)/2):enable='gt(t,{trigger_time})'")
                    filters.append(f"drawtext=fontfile='{font_path}':text='{prev_s2}':fontcolor=white:fontsize={font_score}:x={box_t2_x}+(({box_width}-text_w)/2):y={box_y}+(({box_height}-text_h)/2):enable='lte(t,{trigger_time})'")
                    filters.append(f"drawtext=fontfile='{font_path}':text='{seg['s2']}':fontcolor=white:fontsize={font_score}:x={box_t2_x}+(({box_width}-text_w)/2):y={box_y}+(({box_height}-text_h)/2):enable='gt(t,{trigger_time})'")

                final_filter_str = ",".join(filters)
                cmd = [
                    ffmpeg_exe, "-i", downloaded_file,
                    "-vf", final_filter_str,
                    # The video filter resets the clip's clock (setpts=PTS-STARTPTS),
                    # so the audio must be re-anchored to that same zero. Copying it
                    # (-c:a copy) instead preserves the raw clip's AAC priming/offset,
                    # leaving audio and video misaligned within every clip. Re-encode
                    # with async resampling so each clip's audio starts exactly at 0.
                    "-af", "aresample=async=1:first_pts=0",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                    "-y", final_filename
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                downloaded_clips.append(final_filename)
                
                try: os.remove(downloaded_file)
                except: pass

            except Exception as e:
                print(f"Error on segment {i}: {e}")
                traceback.print_exc()

        if downloaded_clips and not abort_event.is_set():
            update_gui(90, "Merging Clips...")
            with open(list_file_path, 'w') as f:
                for clip in downloaded_clips:
                    safe_path = os.path.abspath(clip).replace("'", "'\\''")
                    f.write(f"file '{safe_path}'\n")

            # Video concatenates losslessly (-c:v copy), but AAC audio cannot be
            # stream-copied across clip boundaries: its 1024-sample frames and
            # per-clip encoder priming never line up at the joins, so ffmpeg emits
            # "Non-monotonic DTS" warnings and the audio drifts progressively later
            # with each clip. Re-encoding the audio (async resampling keeps it locked
            # to the video timeline) removes both the warnings and the drift.
            # -loglevel warning so the captured stderr holds only real warnings/
            # errors: otherwise ffmpeg's normal banner trips the had_warnings flag
            # and every clean run falsely reports "There were warnings from FFmpeg".
            cmd_concat = [
                ffmpeg_exe, "-hide_banner", "-loglevel", "warning",
                "-f", "concat", "-safe", "0", "-i", list_file_path,
                "-c:v", "copy",
                "-af", "aresample=async=1",
                "-c:a", "aac", "-b:a", "128k",
                "-y", output_video
            ]
            if cli_mode and cli_warning_log:
                result = subprocess.run(cmd_concat, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                if result.stderr.strip():
                    cli_warning_log.write(result.stderr)
                if result.returncode != 0:
                    raise subprocess.CalledProcessError(result.returncode, cmd_concat)
            else:
                subprocess.run(cmd_concat, check=True)
            
            cleanup_workspace()
            show_finish_state(f"Saved: {os.path.basename(output_video)}")
            
        elif not abort_event.is_set():
            show_error_state("No clips processed.")

    except Exception as e:
        msg = str(e)
        print(f"CRASH: {msg}")
        traceback.print_exc()
        show_error_state("Check log on Desktop.")

    finally:
        cleanup_workspace()

# --- MAIN ENTRY POINT ---
def main():
    global root, progress_var, status_var, close_button, abort_button

    parser = argparse.ArgumentParser(description="Process video clips from JSON configuration files")
    parser.add_argument('-f', '--file', type=str, help='Path to a single JSON file')
    parser.add_argument('-d', '--directory', type=str, help='Path to a directory containing JSON files')
    parser.add_argument('-H', '--highlight', action='store_true',
                        help='Build a highlight reel from the plays marked "highlight" in the JSON, '
                             'with no scoreboard and no timeline overlay')

    args = parser.parse_args()

    if args.highlight and not (args.file or args.directory):
        print("Error: --highlight requires -f/--file or -d/--directory")
        sys.exit(1)

    # CLI mode: -f or -d specified
    if args.file or args.directory:
        json_files = []

        if args.file:
            if not os.path.isfile(args.file) or not args.file.lower().endswith('.json'):
                print(f"Error: {args.file} is not a valid JSON file")
                sys.exit(1)
            json_files.append(args.file)

        elif args.directory:
            if not os.path.isdir(args.directory):
                print(f"Error: {args.directory} is not a valid directory")
                sys.exit(1)
            json_files = glob.glob(os.path.join(args.directory, "*.json"))
            if not json_files:
                print(f"Error: No JSON files found in {args.directory}")
                sys.exit(1)

        global cli_mode, cli_warning_log
        cli_mode = True

        log_dir = os.path.abspath(args.directory if args.directory else os.path.dirname(args.file))
        log_path = os.path.join(log_dir, "debug.log")
        cli_warning_log = WarningCapture(log_path)
        sys.stderr = cli_warning_log

        n = len(json_files)
        what = "highlight reels" if args.highlight else "videos"
        print(f"Creating {what} for a total of {n} game(s)")

        # Process each JSON file
        for idx, json_file in enumerate(json_files, start=1):
            output_name = output_video_name(json_file, args.highlight)
            print(f"\nWorking on {output_name}")
            run_processing_logic([json_file], highlights_only=args.highlight)
            print(f"Completed {output_name}")

        sys.stderr = sys.__stderr__
        if cli_warning_log.had_warnings:
            print(f"\nThere were warnings from FFmpeg, check the log file: {log_path}")
        cli_warning_log.close()

        print("\nAll files processed.")
        return

    # GUI mode: no arguments
    root = tk.Tk()
    root.title("game_scoring")

    w, h = 400, 200
    ws = root.winfo_screenwidth()
    hs = root.winfo_screenheight()
    x = (ws/2) - (w/2)
    y = (hs/2) - (h/2)
    root.geometry(f'{w}x{h}+{int(x)}+{int(y)}')
    root.resizable(False, False)

    root.lift()
    root.attributes('-topmost', True)
    root.after_idle(root.attributes, '-topmost', False)
    root.focus_force()
    try:
        pid = os.getpid()
        os.system(f"osascript -e 'tell application \"System Events\" to set frontmost of the first process whose unix id is {pid} to true'")
    except: pass

    frame = ttk.Frame(root, padding="20")
    frame.pack(fill=tk.BOTH, expand=True)

    status_var = tk.StringVar(value="Starting...")
    lbl = ttk.Label(frame, textvariable=status_var, font=("Helvetica", 12))
    lbl.pack(pady=(0, 10))

    progress_var = tk.IntVar(value=0)
    pb = ttk.Progressbar(frame, orient="horizontal", length=300, mode="determinate", variable=progress_var)
    pb.pack(pady=10)

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(pady=(10, 0))

    abort_button = ttk.Button(btn_frame, text="Abort", command=trigger_abort, state="normal")
    abort_button.pack(side=tk.LEFT, padx=5)

    close_button = ttk.Button(btn_frame, text="Processing...", command=root.destroy, state="disabled")
    close_button.pack(side=tk.LEFT, padx=5)

    thread = threading.Thread(target=run_processing_logic, args=(sys.argv[1:],))
    thread.daemon = True
    thread.start()

    root.mainloop()

if __name__ == "__main__":
    main()