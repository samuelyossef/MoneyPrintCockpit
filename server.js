/**
 * ============================================================================
 * Servidor HTTP Local (Zero Dependências Externas)
 * Moneyprint / Lattice Cockpit Frontend
 * ============================================================================
 * Este servidor estático utiliza exclusivamente os módulos nativos do Node.js
 * (`http`, `fs`, `path`) para servir o layout frontend com máxima velocidade,
 * suporte a tipos MIME corretos e detecção automática de portas ocupadas.
 */

const http = require('http');
const fs = require('fs');
const path = require('path');

// Definição da porta inicial (prioriza a variável de ambiente PORT ou 3100).
// A 3000 é do Gateway no Docker: ele escuta só em 127.0.0.1, então no Windows este servidor
// conseguiria abrir a mesma porta (em ::) sem EADDRINUSE e "roubar" o localhost dele.
let port = parseInt(process.env.PORT || '3100', 10);
const ROOT_DIR = __dirname;

/**
 * Tabela de mapeamento de extensões de arquivos para seus respectivos tipos MIME.
 * Garante que HTML, CSS, JavaScript, imagens e vídeos sejam interpretados
 * corretamente pelo navegador sem bloqueios de segurança (CORS/MIME sniff).
 */
const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.webp': 'image/webp',
  '.mp4': 'video/mp4',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf'
};

/**
 * Criação da instância do servidor HTTP
 */
const server = http.createServer((req, res) => {
  // 1. Sanitização e decodificação do caminho solicitado na URL
  let reqPath = decodeURIComponent(req.url.split('?')[0]);

  // Se a rota for a raiz '/', redireciona internamente para 'index.html'
  if (reqPath === '/' || reqPath === '') {
    reqPath = '/index.html';
  }

  // Prevenção de Path Traversal (segurança contra '../')
  const safePath = path.normalize(reqPath).replace(/^(\.\.[\/\\])+/, '');
  let filePath = path.join(ROOT_DIR, safePath);

  // 2. Verificação da existência do arquivo no disco
  fs.stat(filePath, (err, stats) => {
    if (err) {
      // Retorna 404 caso o arquivo não seja encontrado
      res.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(`<h1>404 Não Encontrado</h1><p>Arquivo não encontrado: ${reqPath}</p>`);
      return;
    }

    // Se o caminho apontar para um diretório, busca o 'index.html' interno
    if (stats.isDirectory()) {
      filePath = path.join(filePath, 'index.html');
    }

    // 3. Determinação do Content-Type baseado na extensão
    const ext = path.extname(filePath).toLowerCase();
    const contentType = MIME_TYPES[ext] || 'application/octet-stream';

    // 4. Leitura e envio do arquivo para o cliente
    fs.readFile(filePath, (readErr, content) => {
      if (readErr) {
        // Diretórios sem index.html (e arquivos removidos entre stat e read) são 404, não 500.
        if (readErr.code === 'ENOENT') {
          res.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8' });
          res.end(`<h1>404 Não Encontrado</h1><p>Arquivo não encontrado: ${reqPath}</p>`);
          return;
        }
        res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end(`Erro interno ao ler arquivo: ${readErr.message}`);
        return;
      }

      // Envia resposta HTTP 200 com cabeçalho de cache desativado para desenvolvimento
      res.writeHead(200, {
        'Content-Type': contentType,
        'Cache-Control': 'no-cache'
      });
      res.end(content);
    });
  });
});

/**
 * Inicializa a escuta na porta especificada e imprime URLs amigáveis
 * @param {number} p Porta em que o servidor irá escutar
 */
function startServer(p) {
  server.listen(p, () => {
    console.log(`=======================================================`);
    console.log(`🚀 Moneyprint / Lattice Cockpit Frontend rodando em:`);
    console.log(`👉 http://localhost:${p}`);
    console.log(`👉 http://localhost:${p}/Cockpit.html`);
    console.log(`=======================================================`);
  });
}

/**
 * Tratamento de erros do servidor
 * Caso a porta já esteja em uso (EADDRINUSE), tenta automaticamente a próxima porta (ex: 3001, 3002)
 */
server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.log(`⚠️  Porta ${port} já está em uso, tentando porta ${port + 1}...`);
    port++;
    startServer(port);
  } else {
    console.error('❌ Erro no servidor:', err);
  }
});

// Início do servidor
startServer(port);
