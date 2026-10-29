const sharp = require('sharp')
const fs = require('fs')
const path = require('path')

async function test() {
  const svg = fs.readFileSync(path.join(__dirname, '..', 'public', 'icon.svg'))
  const sizes = [16, 32, 48, 64, 256]
  console.log('With clip-path:')
  for (const s of sizes) {
    const buf = await sharp(svg).resize(s, s).png().toBuffer()
    console.log(s + 'x' + s + ': ' + buf.length + ' bytes')
  }

  // Simple version — no clip-path, just rings
  const simpleSvg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">' +
    '<rect width="1024" height="1024" rx="192" fill="#1C1C2E"/>' +
    '<circle cx="370" cy="512" r="200" fill="none" stroke="#007AFF" stroke-width="120"/>' +
    '<circle cx="654" cy="512" r="200" fill="none" stroke="#FF2D78" stroke-width="120"/>' +
    '</svg>'
  console.log('\nWithout clip-path:')
  for (const s of sizes) {
    const buf = await sharp(Buffer.from(simpleSvg)).resize(s, s).png().toBuffer()
    console.log(s + 'x' + s + ': ' + buf.length + ' bytes')
  }

  // Also test just a simple clean design — dark bg + single white circle
  const cleanSvg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">' +
    '<rect width="1024" height="1024" rx="192" fill="#1C1C2E"/>' +
    '<circle cx="512" cy="512" r="300" fill="none" stroke="#007AFF" stroke-width="160"/>' +
    '<path d="M384 384 L640 384 L640 640 L384 640 Z" fill="none" stroke="#FF2D78" stroke-width="80"/>' +
    '</svg>'
  console.log('\nClean design (single circle + square):')
  for (const s of sizes) {
    const buf = await sharp(Buffer.from(cleanSvg)).resize(s, s).png().toBuffer()
    console.log(s + 'x' + s + ': ' + buf.length + ' bytes')
  }
}

test().catch(console.error)
