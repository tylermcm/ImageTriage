// Renders icon.svg (large sizes) and icon_small.svg (thicker blades, 32 px and below) to PNGs: node render.js OUTDIR
const { chromium } = require('playwright'); const fs = require('fs'); const path = require('path');
(async () => {
  const out = process.argv[2], dir = __dirname, b = await chromium.launch();
  const sizes = [16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 96, 128, 256, 1024];
  for (const px of sizes) {
    const svg = fs.readFileSync(path.join(dir, px <= 32 ? 'icon_small.svg' : 'icon.svg'), 'utf8');
    const p = await b.newPage({ viewport: { width: px, height: px } });
    await p.setContent(`<body style="margin:0;background:transparent">${svg.replace('<svg ', `<svg width="${px}" height="${px}" `)}</body>`);
    await p.screenshot({ path: path.join(out, `icon_${px}.png`), omitBackground: true });
    await p.close();
  }
  await b.close();
})();
