## Workflow:
- Define the segments and the score using [this tool](https://oraziogallo.github.io/game_scoring/). Download the JSON file.
- Drag and drop the JSON file on the game_scoring app:
  -  If you used a Youtube video, be patient---it'll take several minutes.
  -  If you worked on a local video it should be fast, but make sure the JSON file is in the same folder as the video it refers to.

## Setup
- Download game_scoring.zip from the [latest release](https://github.com/oraziogallo/game_scoring/releases).
- Unzip it to the location where you want to keep it. Your Mac may ask for you permission at different stage, allow it.
- Since this is not a signed app, your Mac will say it's damaged and won't let you open it. To fix that, we need to clear the quarantine attribute that your Mac sets:
  - Open the terminal (⌘+space, then type "terminal")
  - In the terminal window that just opened type `xattr -cr` 
  - Drag the icon of the app to the terminal window
  - Press enter
