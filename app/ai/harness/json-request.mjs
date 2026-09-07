// Cordis plugin：通过 Harness 官方 hook/extension 保留结构化生成参数。
export const name = 'storyworld-json-request';
export const inject = ['deepseekLlmApiExtensions'];

export function apply(ctx, config) {
  if (!Number.isFinite(config.temperature) || config.temperature < 0 || config.temperature > 2
      || !Number.isInteger(config.maxTokens) || config.maxTokens < 1) {
    throw new Error('Invalid StoryWorld generation parameters');
  }
  ctx.on('agent/request', async (_payload, next) => {
    const request = await next();
    if (request.tools?.length) {
      throw new Error('StoryWorld requires a tool-free Harness profile');
    }
    return { ...request, temperature: config.temperature, maxTokens: config.maxTokens };
  });
  ctx.deepseekLlmApiExtensions.register('response_format', {
    prepare: async () => ({ value: { type: 'json_object' } }),
  });
}
