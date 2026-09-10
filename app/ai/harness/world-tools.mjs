// Only installed world tools are exposed. The player cannot select paths,
// processes, URLs, credentials, or a different world's state.
export const name = 'storyworld-world-tools';
export const inject = ['tools'];

export function apply(ctx, config) {
  let requests = 0;
  const call = async (name, args, signal) => {
    const response = await fetch(config.endpoint, {
      method: 'POST', signal,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${config.token}` },
      body: JSON.stringify({ name, args }),
    });
    if (!response.ok) throw new Error('World tool transport failed');
    return await response.json();
  };
  for (const tool of config.tools) {
    ctx.tools.register({
      ...tool,
      output: { schema: { type: 'object', additionalProperties: true },
        render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      execute: (args, exec) => call(tool.name, args, exec.signal),
    });
  }
  ctx.on('tools/post-execute', async (exec, result, next) => {
    if (exec.name === 'skill' && !result.isError) {
      await call('_skill_loaded', { name: exec.arguments.name }, exec.signal);
    }
    return await next();
  });
  ctx.on('agent/request', async (_payload, next) => {
    if (++requests > config.maxSteps) throw new Error('World reasoning step budget reached');
    const request = await next();
    const allowed = new Set(['skill', ...config.tools.map(t => t.name)]);
    if (request.tools?.some(t => !allowed.has(t.name))) throw new Error('Unexpected world tool');
    return { ...request, temperature: config.temperature, maxTokens: config.maxTokens };
  });
}
