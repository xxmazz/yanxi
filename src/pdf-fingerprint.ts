/** Creates a deterministic local fingerprint for duplicate PDF detection. */
export async function fingerprintPdf(file: File): Promise<string> {
  const bytes = await file.arrayBuffer();
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    return (
      "sha256:" +
      Array.from(new Uint8Array(digest), (byte) =>
        byte.toString(16).padStart(2, "0"),
      ).join("")
    );
  }

  // LAN HTTP may not expose SubtleCrypto. Keep a deterministic local fallback.
  const data = new Uint8Array(bytes);
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (const byte of data) {
    first = Math.imul(first ^ byte, 0x01000193);
    second = Math.imul(second ^ byte, 0x85ebca6b);
  }
  return (
    "fallback:" +
    data.length +
    ":" +
    (first >>> 0).toString(16).padStart(8, "0") +
    (second >>> 0).toString(16).padStart(8, "0")
  );
}
