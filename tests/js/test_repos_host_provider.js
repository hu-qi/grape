/** Repository hosting preserves the platform and GitLab instance selected by the user. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../src/api/static/js/pages/repos.js'), 'utf8');
const start = source.indexOf('function hostAndSelect(');
const end = source.indexOf('\nfunction _showBotCollaboratorPrompt', start);

async function host(repoKey) {
  const calls = [];
  const selected = [];
  const sandbox = {
    API: {post: async (url, body) => { calls.push({url, body}); return {}; }},
    app: {showToast() {}, loadProfile: async () => {}, showConfirm: (_, action) => action()},
    window: {_grapeConfirm: options => options.onConfirm()},
    I18N: {t: key => key},
    selectRepo: (...args) => selected.push(args),
  };
  sandbox._grapeConfirm = sandbox.window._grapeConfirm;
  vm.runInNewContext(source.slice(start, end), sandbox);
  sandbox.hostAndSelect('team/subgroup/project', 'team/subgroup', 'project', repoKey);
  await new Promise(setImmediate);
  return JSON.parse(JSON.stringify({calls, selected}));
}

(async () => {
  for (const provider of ['gitcode', 'github']) {
    const result = await host(provider + ':team/project');
    assert.strictEqual(result.calls[0].url, '/repos/host?provider=' + provider);
    assert.deepStrictEqual(result.calls[0].body, {full_name: 'team/subgroup/project'});
  }
  for (const provider of ['gitlab', 'gitlab@corp']) {
    const key = provider + ':team/subgroup/project';
    const result = await host(key);
    assert.strictEqual(result.calls[0].url, '/gitlab/host');
    assert.deepStrictEqual(result.calls[0].body, {provider, project: 'team/subgroup/project'});
    assert.strictEqual(result.selected[0][3], key);
  }
  const legacy = await host('');
  assert.strictEqual(legacy.calls[0].url, '/repos/host');
  console.log('5 hosting provider scenarios passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
