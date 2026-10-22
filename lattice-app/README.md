# Lattice App — protótipo visual congelado

Status: **protótipo congelado** (referência de design, fora do produto).

- Não é servido pelo `cockpit-api` nem pelo `server.js` (modo `npm run static`).
- Não tem pipeline de build: os arquivos JSX não passam por bundler/transpiler e não rodam diretamente no navegador.
- Nenhuma funcionalidade nova deve nascer aqui. A interface em produção é a das páginas `*.html` da raiz com `shared/cockpit-*.js`.

Uso pretendido: rascunho visual do Lattice Cockpit. Mudanças de layout podem ser esboçadas aqui antes de serem implementadas no cockpit real.

Este diretório é a **fonte única** desses arquivos. Não cole cópias em `uploads/` ou `scraps/`.
