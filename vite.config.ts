import { defineConfig } from 'vite';

// GitHub Pages project sites are served from /<repo>/, so assets must be
// requested from that prefix. Override with BASE_PATH=/ for other hosts.
const base = process.env.BASE_PATH ?? '/experiment-geo/';

export default defineConfig({
  base,
  build: {
    target: 'es2022',
    sourcemap: true,
  },
  server: {
    host: true,
  },
});
