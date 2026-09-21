#!/usr/bin/env python3
"""Draw the play-by-play timeline that process_video.py overlays on the left of
the frame, but laid out left-to-right, with every game in a single output file.

    python plot_timelines.py -f Home_vs_Away.json
    python plot_timelines.py -d /path/to/games -o season.pdf

Each game gets one row: a white line running left to right, one slot per play,
with a square above the line for every point won by team 1 and below it for
every point won by team 2. Under it, a lead chart shows who is ahead after each
play and by how much: above zero (red) when team 1 leads, below (blue) when
team 2 does.
"""

import argparse
import glob
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

# --- Look: mirrors the video overlay (white line, team 1 red, team 2 blue),
# with the two point colors lightened enough to stay legible in print. ---
BG_COLOR = "#1a1a1a"
LINE_COLOR = "#ffffff"
T1_COLOR = "#e02020"
T2_COLOR = "#3d7dff"
MUTED_COLOR = "#8a8a8a"

# --- Geometry, in "slots": one slot per play, as in the video overlay. ---
SLOT = 1.0
GAP = 0.1 * SLOT              # same 10% gap the video leaves between boxes
BOX = SLOT - GAP
LINE_W = 0.16 * SLOT
LABEL_SLOTS = 13.0            # room at the left for the team names
SCORE_SLOTS = 6.0             # room at the right for the final score
BOX_HALF = LINE_W / 2 + GAP + BOX   # half-height of the box strip
LEAD_H = 3.0                  # the biggest lead in the file reaches this high
LEAD_GAP = 0.9                # space between the box strip and the lead chart
LEAD_ALPHA = 0.8
GRID_STEP = 5                 # faint reference lines every N points of lead
PEAK_LABEL_ROOM = 1.1         # space kept for the "+N" / "-N" peak labels
ROW_PAD = 0.9                 # space above and below each game
UNIT_IN = 0.13                # inches per slot
MARGIN_IN = 0.35
MAX_FIG_W_IN = 26.0


def parse_game(json_file):
    """Read one scoring JSON into {'name', 't1', 't2', 'winners', 's1', 's2'}.

    'winners' has one entry per play, in JSON order: 1, 2, or 0 for a play that
    scored no point. The winner is derived from the running score exactly the
    way process_video.py derives it, so the diagram matches the video.
    """
    with open(json_file, 'r') as f:
        data = json.load(f)

    segments = data.get('segments', [])
    if not segments:
        return None

    winners = []
    leads = []      # team 1 minus team 2 after each play
    prev_s1, prev_s2 = 0, 0
    for seg in segments:
        score = seg.get('scoreState', {'t1': 0, 't2': 0})
        winner = 0
        if score['t1'] > prev_s1:
            winner = 1
            prev_s1 = score['t1']
        elif score['t2'] > prev_s2:
            winner = 2
            prev_s2 = score['t2']
        winners.append(winner)
        leads.append(prev_s1 - prev_s2)

    return {
        'name': os.path.splitext(os.path.basename(json_file))[0],
        't1': data.get('team1', 'Home'),
        't2': data.get('team2', 'Away'),
        'winners': winners,
        'leads': leads,
        's1': prev_s1,
        's2': prev_s2,
    }


def draw_lead_chart(ax, game, y0, scale):
    """Draw the lead-over-time chart with its zero line at height y0.

    The lead after play k is drawn across slot k, so each step lines up with
    the box that caused it. scale is slots per point of lead.
    """
    n = len(game['leads'])
    leads = np.array(game['leads'], dtype=float)
    # step='post' holds each value until the next x, so repeat the last one.
    xs = np.arange(n + 1) * SLOT
    ys = y0 + np.append(leads, leads[-1]) * scale

    ax.fill_between(xs, y0, np.maximum(ys, y0), step='post',
                    color=T1_COLOR, alpha=LEAD_ALPHA, linewidth=0)
    ax.fill_between(xs, y0, np.minimum(ys, y0), step='post',
                    color=T2_COLOR, alpha=LEAD_ALPHA, linewidth=0)

    # Reference lines every GRID_STEP points, only on the side(s) the lead
    # actually reached, then the zero line on top.
    for sign, extent in ((1, leads.max()), (-1, -leads.min())):
        for lvl in range(GRID_STEP, int(extent) + 1, GRID_STEP):
            ax.plot([0, n * SLOT], [y0 + sign * lvl * scale] * 2,
                    color=MUTED_COLOR, alpha=0.25, linewidth=0.5,
                    linestyle=(0, (2, 3)))
    ax.plot([0, n * SLOT], [y0, y0], color=MUTED_COLOR, linewidth=0.6)

    # Peak lead for each side, placed at the first play that reached it.
    best1 = leads.max()
    if best1 > 0:
        k = int(np.argmax(leads))
        ax.text(k * SLOT + SLOT / 2, y0 + best1 * scale + 0.25, f"+{int(best1)}",
                color=T1_COLOR, fontsize=6.5, ha='center', va='bottom')
    best2 = leads.min()
    if best2 < 0:
        k = int(np.argmin(leads))
        ax.text(k * SLOT + SLOT / 2, y0 + best2 * scale - 0.25, f"{int(best2)}",
                color=T2_COLOR, fontsize=6.5, ha='center', va='top')

    ax.text(-0.8, y0, "lead", color=MUTED_COLOR, fontsize=7,
            ha='right', va='center')


def lead_extents(game, scale):
    """How far the lead chart reaches above and below its zero line, in slots,
    including room for the peak label on any side that was reached."""
    up = max(0, max(game['leads'])) * scale
    down = max(0, -min(game['leads'])) * scale
    if up > 0:
        up += PEAK_LABEL_ROOM
    if down > 0:
        down += PEAK_LABEL_ROOM
    return up, down


def row_height(game, scale):
    up, down = lead_extents(game, scale)
    return ROW_PAD + 2 * BOX_HALF + LEAD_GAP + up + down + ROW_PAD


def draw_game(ax, game, top, lead_scale):
    """Draw one game with the top of its row at height `top`."""
    n = len(game['winners'])
    y = top - ROW_PAD - BOX_HALF          # the white line

    ax.add_patch(Rectangle((0, y - LINE_W / 2), n * SLOT, LINE_W,
                           facecolor=LINE_COLOR, edgecolor='none'))

    for k, winner in enumerate(game['winners']):
        if winner == 0:
            continue
        x = k * SLOT + GAP / 2
        if winner == 1:
            ax.add_patch(Rectangle((x, y + LINE_W / 2 + GAP), BOX, BOX,
                                   facecolor=T1_COLOR, edgecolor='none'))
        else:
            ax.add_patch(Rectangle((x, y - LINE_W / 2 - GAP - BOX), BOX, BOX,
                                   facecolor=T2_COLOR, edgecolor='none'))

    # Team names sit on the side their points are drawn on, which doubles as
    # the colour key.
    label_x = -0.8
    ax.text(label_x, y + LINE_W / 2 + GAP + BOX / 2, game['t1'],
            color=T1_COLOR, fontsize=8, ha='right', va='center')
    ax.text(label_x, y - LINE_W / 2 - GAP - BOX / 2, game['t2'],
            color=T2_COLOR, fontsize=8, ha='right', va='center')

    # Anchored on the dash so the two halves stay symmetric whatever the digits.
    dash_x = n * SLOT + 2.2
    ax.text(dash_x, y, "-", color=MUTED_COLOR, fontsize=9,
            ha='center', va='center')
    ax.text(dash_x - 0.6, y, f"{game['s1']}", color=T1_COLOR, fontsize=9,
            ha='right', va='center', fontweight='bold')
    ax.text(dash_x + 0.6, y, f"{game['s2']}", color=T2_COLOR, fontsize=9,
            ha='left', va='center', fontweight='bold')

    up, _ = lead_extents(game, lead_scale)
    draw_lead_chart(ax, game, y - BOX_HALF - LEAD_GAP - up, lead_scale)


def plot_games(games, out_path):
    max_plays = max(len(g['winners']) for g in games)
    # One scale for every game so leads are comparable across rows.
    max_lead = max(max(abs(l) for l in g['leads']) for g in games)
    lead_scale = LEAD_H / max(1, max_lead)

    x0 = -LABEL_SLOTS
    x1 = max_plays * SLOT + SCORE_SLOTS
    data_w = x1 - x0
    heights = [row_height(g, lead_scale) for g in games]
    data_h = sum(heights)

    # Squares must stay square, so the axes box is sized directly from the data
    # extent rather than left to the aspect machinery.
    unit = min(UNIT_IN, (MAX_FIG_W_IN - 2 * MARGIN_IN) / data_w)
    fig_w = data_w * unit + 2 * MARGIN_IN
    fig_h = data_h * unit + 2 * MARGIN_IN

    fig = plt.figure(figsize=(fig_w, fig_h), facecolor=BG_COLOR)
    ax = fig.add_axes([MARGIN_IN / fig_w, MARGIN_IN / fig_h,
                       data_w * unit / fig_w, data_h * unit / fig_h])
    ax.set_facecolor(BG_COLOR)
    ax.set_xlim(x0, x1)
    ax.set_ylim(-data_h, 0)
    ax.axis('off')

    top = 0.0
    for game, h in zip(games, heights):
        draw_game(ax, game, top, lead_scale)
        top -= h

    fig.savefig(out_path, facecolor=BG_COLOR)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Plot the play-by-play timeline of one or more scoring JSON "
                    "files, left to right, into a single PDF or image")
    parser.add_argument('-f', '--file', type=str, help='Path to a single JSON file')
    parser.add_argument('-d', '--directory', type=str,
                        help='Path to a directory containing JSON files')
    parser.add_argument('-o', '--output', type=str,
                        help='Output file; the extension picks the format '
                             '(.pdf, .png, .svg). Default: <name>_timeline.pdf '
                             'for -f, timelines.pdf for -d')

    args = parser.parse_args()

    if not args.file and not args.directory:
        parser.error("one of -f/--file or -d/--directory is required")

    if args.file:
        if not os.path.isfile(args.file) or not args.file.lower().endswith('.json'):
            print(f"Error: {args.file} is not a valid JSON file")
            sys.exit(1)
        json_files = [args.file]
        default_out = os.path.join(
            os.path.dirname(args.file) or ".",
            os.path.splitext(os.path.basename(args.file))[0] + "_timeline.pdf")
    else:
        if not os.path.isdir(args.directory):
            print(f"Error: {args.directory} is not a valid directory")
            sys.exit(1)
        json_files = sorted(glob.glob(os.path.join(args.directory, "*.json")))
        if not json_files:
            print(f"Error: No JSON files found in {args.directory}")
            sys.exit(1)
        default_out = os.path.join(args.directory, "timelines.pdf")

    out_path = args.output or default_out

    games = []
    for json_file in json_files:
        try:
            game = parse_game(json_file)
        except Exception as e:
            print(f"Skipping {os.path.basename(json_file)}: {e}")
            continue
        if game is None:
            print(f"Skipping {os.path.basename(json_file)}: no segments")
            continue
        games.append(game)
        print(f"{game['name']}: {len(game['winners'])} plays, "
              f"{game['s1']}-{game['s2']}")

    if not games:
        print("Error: No games to plot")
        sys.exit(1)

    plot_games(games, out_path)
    print(f"\nSaved {len(games)} game(s) to {out_path}")


if __name__ == "__main__":
    main()
