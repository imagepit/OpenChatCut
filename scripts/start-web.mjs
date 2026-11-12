import { fileURLToPath } from 'node:url';
import { startEmbeddedServer } from '../desktop/embedded-server.ts';

const distDir = fileURLToPath(new URL('../dist', import.meta.url));
const { origin } = await startEmbeddedServer(distDir);
console.log(`OpenChatCut: ${origin}`);
