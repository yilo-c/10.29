/**
 * Generate app icons from the source SVG.
 * Produces PNG icons at various sizes for electron-builder and web.
 *
 * Usage: node scripts/generate-icons.cjs
 */

const sharp = require('sharp')
const fs = require('fs')
const path = require('path')

const SRC = path.join(__dirname, '..', 'public', 'icon.svg')
const OUT = path.join(__dirname, '..', 'public')

const SIZES = [16, 32, 48, 64, 96, 128, 192, 256, 512]

async function main() {
  const svgBuffer = fs.readFileSync(SRC)

  // Generate PNG at each size
  for (const size of SIZES) {
    const pngPath = path.join(OUT, `icon-${size}.png`)
    await sharp(svgBuffer)
      .resize(size, size)
      .png()
      .toFile(pngPath)
    console.log(`Generated ${pngPath}`)
  }

  // Copy 512x512 as the primary icon for electron-builder
  const mainIcon = path.join(OUT, 'icon.png')
  await sharp(svgBuffer).resize(512, 512).png().toFile(mainIcon)
  console.log(`Generated ${mainIcon}`)

  // ── Generate Windows ICO (.ico) ──────────────────────────────────
  const icoSizes = [16, 32, 48, 256]
  const icoPath = path.join(OUT, 'icon.ico')
  const header = Buffer.alloc(6)
  header.writeUInt16LE(0, 0)        // reserved
  header.writeUInt16LE(1, 2)        // ICO type
  header.writeUInt16LE(icoSizes.length, 4)  // image count

  const entries = []
  let dataOffset = 6 + icoSizes.length * 16

  for (const size of icoSizes) {
    const png = sharp(svgBuffer).resize(size, size).png().toBuffer()
    entries.push({ size, png })
  }

  const resolved = await Promise.all(entries.map(e => e.png))
  await fs.promises.writeFile(icoPath, Buffer.concat([
    header,
    ...icoSizes.map((size, i) => {
      const entry = Buffer.alloc(16)
      entry.writeUInt8(size === 256 ? 0 : size, 0)   // width (0 = 256)
      entry.writeUInt8(size === 256 ? 0 : size, 1)   // height
      entry.writeUInt8(0, 2)                           // color count
      entry.writeUInt8(0, 3)                           // reserved
      entry.writeUInt16LE(1, 4)                        // planes
      entry.writeUInt16LE(32, 6)                       // bit count
      entry.writeUInt32LE(resolved[i].length, 8)       // image size
      entry.writeUInt32LE(dataOffset, 12)              // offset
      dataOffset += resolved[i].length
      return entry
    }),
    ...resolved,
  ]))
  console.log(`Generated ${icoPath}`)

  console.log('\nAll icons generated successfully!')
}

main().catch(err => {
  console.error('Icon generation failed:', err)
  process.exit(1)
})
