import { createHash } from 'node:crypto'
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Where the proxy points: the backend of a development run on 8585 (the port of the container). NEXTIME_API changes it.
const apiTarget = process.env.NEXTIME_API || 'http://127.0.0.1:8585'

/** The tab's icon with its checksum in the address. A browser keeps a tab icon per address and does not ask again,
 * so an icon that changed under the same address stayed old in the tab. */
function iconWithChecksum(): Plugin {
  return {
    name: 'icon-with-checksum',
    transformIndexHtml(html) {
      const sum = createHash('sha256').update(readFileSync(new URL('./public/logo.svg', import.meta.url))).digest('hex').slice(0, 12)
      return html.replace('href="/logo.svg"', `href="/logo.svg?v=${sum}"`)
    },
  }
}

/** The files of the shell the service worker keeps beside the built ones (TIME-28, as nexnotes NOTES-23): what the
 * start page names without a hash in its address. */
export const SHELL_FILES = [
  '/theme.js',
  '/logo.svg',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/maskable-192.png',
  '/icons/maskable-512.png',
  '/icons/apple-touch-icon.png',
  '/icons/icon-192-dark.png',
]

/** The built files the start page names itself (its script, its styles, the chunks it preloads). */
export function namedAssets(html: string): string[] {
  const found = new Set<string>()
  for (const match of html.matchAll(/(?:src|href)="(\/assets\/[^"?#]+)"/g)) found.add(match[1])
  return [...found].sort()
}

/** The service worker learns its build: a stamp from the start page and the worker itself, and the files of the shell
 * (`public/sw.js`). A new build is a new worker, and its cache has a name of its own (TIME-28 EC-7). */
function serviceWorker(): Plugin {
  let outDir = 'dist'
  return {
    name: 'service-worker',
    apply: 'build',
    configResolved(config) {
      outDir = config.build.outDir
    },
    writeBundle() {
      const root = new URL('./', import.meta.url)
      const dist = new URL(outDir.replace(/\\/g, '/') + '/', root)
      const html = readFileSync(new URL('index.html', dist), 'utf8')
      const worker = readFileSync(new URL('sw.js', dist), 'utf8')
      const shell = [...namedAssets(html), ...SHELL_FILES]
      const stamp = createHash('sha256').update(html).update(worker).update(shell.join('\n')).digest('hex').slice(0, 16)
      const written = worker.replace("'__NEXTIME_BUILD__'", JSON.stringify(stamp)).replace('/* __NEXTIME_SHELL__ */ []', JSON.stringify(shell))
      if (written === worker) throw new Error('sw.js: the marks for the build are missing')
      writeFileSync(new URL('sw.js', dist), written)
    },
  }
}

/** Swagger UI for the description of the API (TIME-26 AC-8) beside the built files, with its licence, as nexnotes has it:
 * the page at `/api/v1/docs` loads nothing from the internet. Its own start script and styles are in `public/api-docs/`. */
function apiDocs(): Plugin {
  let outDir = 'dist'
  return {
    name: 'api-docs',
    apply: 'build',
    configResolved(config) {
      outDir = config.build.outDir
    },
    writeBundle() {
      const root = new URL('./', import.meta.url)
      const target = new URL(outDir.replace(/\\/g, '/') + '/api-docs/', root)
      const source = new URL('node_modules/swagger-ui-dist/', root)
      mkdirSync(target, { recursive: true })
      for (const name of ['swagger-ui-bundle.js', 'swagger-ui.css', 'LICENSE', 'NOTICE']) copyFileSync(new URL(name, source), new URL(name, target))
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), iconWithChecksum(), serviceWorker(), apiDocs()],
  build: {
    rollupOptions: {
      output: {
        // Libraries change rarely. In their own file, they stay cached in the browser after an update.
        manualChunks(id) {
          if (id.includes('node_modules')) return 'vendor'
          return undefined
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    css: false,
    exclude: ['node_modules/**', 'dist/**'],
    // Two at a time: the machine this is built on does not take load peaks well.
    maxWorkers: 2,
  },
  server: {
    // Fixed port: if it is taken, Vite aborts instead of silently falling back to another one.
    port: 5585,
    strictPort: true,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: false },
    },
  },
  preview: {
    port: 5585,
    strictPort: true,
  },
})
