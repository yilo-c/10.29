const sharp = require('sharp');
const path = require('path');
const fs = require('fs');

const SRC = path.join(__dirname, '..', 'public', 'icon-source.png');
const OUT = path.join(__dirname, '..', 'public');
const SIZES = [16, 32, 48, 64, 96, 128, 192, 256, 512];

async function main() {
  const meta = await sharp(SRC).metadata();
  console.log('Source:', meta.width + 'x' + meta.height, meta.format);

  const size = Math.min(meta.width, meta.height);
  const crop = sharp(SRC).extract({
    left: Math.floor((meta.width - size) / 2),
    top: Math.floor((meta.height - size) / 2),
    width: size,
    height: size,
  });

  for (const s of SIZES) {
    const outPath = path.join(OUT, 'icon-' + s + '.png');
    await crop.clone().resize(s, s).png().toFile(outPath);
    console.log('Generated icon-' + s + '.png');
  }

  const mainPath = path.join(OUT, 'icon.png');
  await crop.clone().resize(512, 512).png().toFile(mainPath);
  console.log('Generated icon.png');
  console.log('Done!');
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
