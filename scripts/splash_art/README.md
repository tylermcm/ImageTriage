# Splash artwork source

`scene.html` draws the startup splash (`image_triage/ui/assets/splash_background-v8.png`).
The ring, type, icons, notes and frames are code; the photos inside the frames are the
cropped `*.png` files here (taken from the earlier splash).

Render at 2x (3266x1926):

    NODE_PATH=$(npm root -g) node shot.js "$PWD/scene.html" splash.png 1633 963 2

Needs Playwright and network access once for the Montserrat and Mrs Saint Delafield
Google fonts. Save the result as `splash_background-v8.png` (bump the version when it changes).
