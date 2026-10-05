## Workflow:
- Define the segments and the score using [this tool](https://oraziogallo.github.io/game_scoring/). Download the JSON file.
- Drag and drop the JSON file on the game_scoring app:
  -  If you used a Youtube video, be patient---it'll take several minutes.
  -  If you worked on a local video it should be fast, but make sure the JSON file is in the same folder as the video it refers to.

## Gaps in the recording
If the recording stopped and restarted, some points were never filmed and the counted score falls behind. Click the score of the first play after the gap and type the real score after that play (Enter to save, Esc to cancel); every later play counts on from there. The ↺ next to a corrected score undoes the correction.

In the JSON, that play gets a `resumeScore` field holding the score just *before* it. The video overlay and `plot_timelines.py` draw a break in the point sequence (and in the lead chart) at that play. Files without the field behave exactly as before.

## Setup
- Download game_scoring.zip from the [latest release](https://github.com/oraziogallo/game_scoring/releases).
- Unzip it to the location where you want to keep it. Your Mac may ask for you permission at different stage, allow it.
- Since this is not a signed app, your Mac will say it's damaged and won't let you open it. To fix that, we need to clear the quarantine attribute that your Mac sets:
  - Open the terminal (⌘+space, then type "terminal")
  - In the terminal window that just opened type `xattr -cr` 
  - Drag the icon of the app to the terminal window
  - Press enter

## More on the script to create the video:
Process a single JSON file
```python process_video.py -f config.json```

Process all JSON files in a folder
```python process_video.py -d ./videos_folder```

Launch the GUI (original behavior)
```python process_video.py```
