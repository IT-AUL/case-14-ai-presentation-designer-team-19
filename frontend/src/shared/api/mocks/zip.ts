export interface ZipEntry {
  path: string
  content: string | Uint8Array
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let index = 0; index < 256; index += 1) {
    let value = index
    for (let bit = 0; bit < 8; bit += 1) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1
    table[index] = value >>> 0
  }
  return table
})()

function crc32(data: Uint8Array): number {
  let crc = 0xffffffff
  for (const byte of data) crc = (CRC_TABLE[(crc ^ byte) & 0xff] ?? 0) ^ (crc >>> 8)
  return (crc ^ 0xffffffff) >>> 0
}

function dosDateTime(date: Date): { time: number; day: number } {
  const time = (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2)
  const day = ((Math.max(date.getFullYear(), 1980) - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate()
  return { time, day }
}

const UTF8_FLAG = 0x0800
const VERSION = 20

export function createZip(entries: readonly ZipEntry[], modified = new Date()): Uint8Array<ArrayBuffer> {
  const encoder = new TextEncoder()
  const { time, day } = dosDateTime(modified)
  const files = entries.map((entry) => {
    const name = encoder.encode(entry.path)
    const data = typeof entry.content === 'string' ? encoder.encode(entry.content) : entry.content
    return { name, data, crc: crc32(data) }
  })

  const localSize = files.reduce((sum, file) => sum + 30 + file.name.length + file.data.length, 0)
  const centralSize = files.reduce((sum, file) => sum + 46 + file.name.length, 0)
  const output = new Uint8Array(localSize + centralSize + 22)
  const view = new DataView(output.buffer)
  const offsets: number[] = []
  let cursor = 0

  for (const file of files) {
    offsets.push(cursor)
    view.setUint32(cursor, 0x04034b50, true)
    view.setUint16(cursor + 4, VERSION, true)
    view.setUint16(cursor + 6, UTF8_FLAG, true)
    view.setUint16(cursor + 8, 0, true)
    view.setUint16(cursor + 10, time, true)
    view.setUint16(cursor + 12, day, true)
    view.setUint32(cursor + 14, file.crc, true)
    view.setUint32(cursor + 18, file.data.length, true)
    view.setUint32(cursor + 22, file.data.length, true)
    view.setUint16(cursor + 26, file.name.length, true)
    view.setUint16(cursor + 28, 0, true)
    output.set(file.name, cursor + 30)
    output.set(file.data, cursor + 30 + file.name.length)
    cursor += 30 + file.name.length + file.data.length
  }

  const centralStart = cursor
  files.forEach((file, index) => {
    view.setUint32(cursor, 0x02014b50, true)
    view.setUint16(cursor + 4, VERSION, true)
    view.setUint16(cursor + 6, VERSION, true)
    view.setUint16(cursor + 8, UTF8_FLAG, true)
    view.setUint16(cursor + 10, 0, true)
    view.setUint16(cursor + 12, time, true)
    view.setUint16(cursor + 14, day, true)
    view.setUint32(cursor + 16, file.crc, true)
    view.setUint32(cursor + 20, file.data.length, true)
    view.setUint32(cursor + 24, file.data.length, true)
    view.setUint16(cursor + 28, file.name.length, true)
    view.setUint16(cursor + 30, 0, true)
    view.setUint16(cursor + 32, 0, true)
    view.setUint16(cursor + 34, 0, true)
    view.setUint16(cursor + 36, 0, true)
    view.setUint32(cursor + 38, 0, true)
    view.setUint32(cursor + 42, offsets[index] ?? 0, true)
    output.set(file.name, cursor + 46)
    cursor += 46 + file.name.length
  })

  view.setUint32(cursor, 0x06054b50, true)
  view.setUint16(cursor + 4, 0, true)
  view.setUint16(cursor + 6, 0, true)
  view.setUint16(cursor + 8, files.length, true)
  view.setUint16(cursor + 10, files.length, true)
  view.setUint32(cursor + 12, cursor - centralStart, true)
  view.setUint32(cursor + 16, centralStart, true)
  view.setUint16(cursor + 20, 0, true)
  return output
}
