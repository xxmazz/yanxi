const PDF_HEADER = [0x25, 0x50, 0x44, 0x46, 0x2d];

export function hasPdfHeader(bytes: Uint8Array): boolean {
  const lastStart = Math.min(bytes.length - PDF_HEADER.length, 1023);
  for (let offset = 0; offset <= lastStart; offset += 1) {
    if (PDF_HEADER.every((byte, index) => bytes[offset + index] === byte))
      return true;
  }
  return false;
}
