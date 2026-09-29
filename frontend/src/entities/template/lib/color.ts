function channels(hex: string): [number, number, number] {
  const value = hex.replace('#', '')
  return [0, 2, 4].map((offset) => parseInt(value.slice(offset, offset + 2), 16)) as [number, number, number]
}

export function colorDistance(a: string, b: string): number {
  const [r1, g1, b1] = channels(a)
  const [r2, g2, b2] = channels(b)
  return Math.hypot(r1 - r2, g1 - g2, b1 - b2)
}

export function isLightColor(hex: string): boolean {
  const [r, g, b] = channels(hex)
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.93
}
