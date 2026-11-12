import { fileURLToPath } from 'node:url';
import { startEmbeddedServer } from '../desktop/embedded-server.ts';

const distDir = fileURLToPath(new URL('../dist', import.meta.url));
const port = Number(process.env.OPENCHATCUT_WEB_PORT ?? '5199');
if (!Number.isInteger(port) || port < 1024 || port > 65535) {
  throw new Error('OPENCHATCUT_WEB_PORT must be a port between 1024 and 65535');
}
const { origin } = await startEmbeddedServer(distDir, port);
console.log(`OpenChatCut: ${origin}`);
