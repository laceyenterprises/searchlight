import { readFileSync } from 'node:fs';

export default function (pi) {
  const config = JSON.parse(readFileSync(process.env.SEW_PI_CELL_CONFIG, 'utf8'));
  if (!process.env.SEW_LITELLM_API_KEY) throw new Error('LiteLLM key is required');
  pi.registerProvider('searchlight-litellm', {
    api: 'openai-completions', baseUrl: config.baseUrl,
    apiKey: process.env.SEW_LITELLM_API_KEY, authHeader: true,
    models: [{ id: config.route, name: config.route, reasoning: false, input: ['text'],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: config.contextWindow, maxTokens: config.maxTokens }],
  });
}
