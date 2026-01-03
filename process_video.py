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

# --- GLOBAL GUI VARIABLES ---
root = None
progress_var = None
status_var = None
close_button = None
abort_button = None
abort_event = threading.Event()

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
        base_path = sys._MEIPASS
        return os.path.join(base_path, 'ffmpeg')
    return "ffmpeg"

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
def run_processing_logic(args):
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
        setup_logging()
        
        if '-p' in args: args.remove('-p')

        if not args:
            show_error_state("No file dropped.")
            return

        target_arg = args[0]
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
        base_name = os.path.splitext(os.path.basename(json_file))[0]
        output_video = os.path.join(work_dir, f"{base_name}.mp4")

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
            
            prev_s1, prev_s2 = 0, 0
            for seg in segments:
                score = seg.get('scoreState', {'t1':0, 't2':0})
                winner = 0
                if score['t1'] > prev_s1: winner = 1; prev_s1 = score['t1']
                elif score['t2'] > prev_s2: winner = 2; prev_s2 = score['t2']

                all_segments.append({
                    'video_id': video_id,
                    'start': seg['start'], 'end': seg['end'],
                    't1_name': t1_name.replace(":", "\\:").replace("'", ""),
                    't2_name': t2_name.replace(":", "\\:").replace("'", ""),
                    's1': score['t1'], 's2': score['t2'], 'winner': winner
                })

        if not all_segments:
            show_error_state("No segments in JSON.")
            return

        os.makedirs(temp_dir, exist_ok=True)
        os.makedirs(processed_dir, exist_ok=True)

        downloaded_clips = []
        font_path = get_font_path()
        total_segs = len(all_segments)

        for i, seg in enumerate(all_segments):
            if abort_event.is_set():
                print("ABORT SIGNAL RECEIVED.")
                cleanup_workspace()
                show_error_state("Aborted by user.")
                return 

            percent = 10 + int((i / total_segs) * 80)
            update_gui(percent, f"Processing Clip {i+1} of {total_segs}...")

            raw_filename = os.path.join(temp_dir, f"raw_{i:03d}.mp4")
            final_filename = os.path.join(processed_dir, f"clip_{i:03d}.mp4")
            
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
                        'download_ranges': lambda info, ydl: [{'start_time': seg['start'], 'end_time': seg['end']}]
                    }
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])
                
                elif source_mode == 'local':
                    
                    start_sec = float(seg['start'])
                    duration = float(seg['end']) - start_sec
                    
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
                found = glob.glob(os.path.join(temp_dir, f"raw_{i:03d}*"))
                if not found: 
                    print(f"Failed to generate raw clip for segment {i}")
                    continue
                downloaded_file = found[0]
                
                # --- OVERLAY PROCESSING ---
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
                prog_slot_h = min(prog_available_h / total_segs, vid_h * 0.05)
                prog_gap = max(1, int(prog_slot_h * 0.1))
                prog_box_dim = prog_slot_h - prog_gap

                filters = []
                
                # Force timestamps to start at 0 (Redundant check, but safe)
                filters.append("setpts=PTS-STARTPTS")

                filters.append(f"drawbox=x={prog_line_x}:y={prog_margin_top}:w={prog_line_w}:h={int(prog_available_h)}:color=white@1:t=fill")
                
                for k in range(i + 1):
                    pt_winner = all_segments[k]['winner']
                    if pt_winner == 0: continue
                    y_pos = int(prog_margin_top + k * prog_slot_h)
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
                if i == 0: prev_s1, prev_s2 = 0, 0
                else: prev_s1, prev_s2 = all_segments[i-1]['s1'], all_segments[i-1]['s2']
                
                filters.append(f"drawtext=fontfile='{font_path}':text='{prev_s1}':fontcolor=white:fontsize={font_score}:x={box_t1_x}+(({box_width}-text_w)/2):y={box_y}+(({box_height}-text_h)/2):enable='lte(t,{trigger_time})'")
                filters.append(f"drawtext=fontfile='{font_path}':text='{seg['s1']}':fontcolor=white:fontsize={font_score}:x={box_t1_x}+(({box_width}-text_w)/2):y={box_y}+(({box_height}-text_h)/2):enable='gt(t,{trigger_time})'")
                filters.append(f"drawtext=fontfile='{font_path}':text='{prev_s2}':fontcolor=white:fontsize={font_score}:x={box_t2_x}+(({box_width}-text_w)/2):y={box_y}+(({box_height}-text_h)/2):enable='lte(t,{trigger_time})'")
                filters.append(f"drawtext=fontfile='{font_path}':text='{seg['s2']}':fontcolor=white:fontsize={font_score}:x={box_t2_x}+(({box_width}-text_w)/2):y={box_y}+(({box_height}-text_h)/2):enable='gt(t,{trigger_time})'")

                final_filter_str = ",".join(filters)
                cmd = [
                    ffmpeg_exe, "-i", downloaded_file,
                    "-vf", final_filter_str,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                    "-c:a", "copy", "-y", final_filename
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

            cmd_concat = [ffmpeg_exe, "-f", "concat", "-safe", "0", "-i", list_file_path, "-c", "copy", "-y", output_video]
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

# --- MAIN ENTRY POINT ---
def main():
    global root, progress_var, status_var, close_button, abort_button
    
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