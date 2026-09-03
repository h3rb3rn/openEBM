/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/app/templates/**/*.html",
    "./src/app/static/js/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        ebm: { 50: '#f0f9ff', 500: '#0ea5e9', 600: '#0284c7', 900: '#0c4a6e' },
      },
    },
  },
  plugins: [],
};
