/** A file's size as people read it: "2,4 MB", "140 MB", "380 KB" (as the attrappe shows it). */
const MB = 1024 * 1024

export function sizeText(bytes: number, language: string): string {
  if (bytes >= MB) {
    const value = bytes / MB
    return `${new Intl.NumberFormat(language, { maximumFractionDigits: value >= 100 ? 0 : 1 }).format(value)} MB`
  }
  return `${new Intl.NumberFormat(language).format(Math.max(1, Math.round(bytes / 1024)))} KB`
}
