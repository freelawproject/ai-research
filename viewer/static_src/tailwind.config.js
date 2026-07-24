/** Tailwind v3 config. Build (from the repo root):
 *  npx -y tailwindcss@3.4.17 -c viewer/static_src/tailwind.config.js \
 *    -i viewer/static_src/input.css \
 *    -o viewer/static/viewer/css/tailwind.css --minify
 *  (or `docker compose --profile dev up tailwind` for watch mode)
 */
module.exports = {
  content: [
    "./viewer/templates/**/*.html",
    "./viewer/static/viewer/js/components/*.js",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
