/** GitLab links and workspace selection keep the configured instance and subgroup. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '../..');
const app = fs.readFileSync(path.join(root, 'src/api/static/js/app.js'), 'utf8');
const repos = fs.readFileSync(path.join(root, 'src/api/static/js/pages/repos.js'), 'utf8');
const user = {bindings: [], gitlab_connections: ['gitlab@corp'], gitlab_instances: {'gitlab@corp': 'https://private.example.test/gitlab'}};
let selected = {fullName: 'team/subgroup/project', repoKey: 'gitlab@corp:team/subgroup/project'};
const context = {window: {}, localStorage: {getItem: () => JSON.stringify(user)},
  sessionStorage: {getItem: () => JSON.stringify(selected), setItem() {}}, _projectsRepos: []};
vm.createContext(context);
vm.runInContext(app.slice(app.indexOf('function _vcsUrl('), app.indexOf("document.addEventListener('DOMContentLoaded'")), context);
assert.strictEqual(context._vcsUrl('gitlab@corp:team/subgroup/project', '/issues/7'), 'https://private.example.test/gitlab/team/subgroup/project/-/issues/7');
assert.strictEqual(context._vcsUrl('gitlab:team/project', '/pull/9'), 'https://gitlab.com/team/project/-/merge_requests/9');
assert.strictEqual(context._vcsUrl('gitlab@unknown:team/project', '/issues/7'), '#');
assert.strictEqual(context._vcsUrl('github:team/project', '/issues/7'), 'https://github.com/team/project/issues/7');
vm.runInContext(app.slice(app.indexOf('  function currentRepo()'), app.indexOf('  function showLogin()')), context);
assert.strictEqual(context.currentRepo().owner, 'gitlab@corp:team/subgroup');
assert.strictEqual(context.currentRepo().name, 'project');
selected = {fullName: 'team/project', repoKey: 'gitlab:team/project'};
assert.strictEqual(context.currentRepo().owner, 'gitlab:team');
vm.runInContext(repos.slice(repos.indexOf('function _getAvailablePlatforms()'), repos.indexOf('function _buildPlatformBar()')), context);
assert.strictEqual(context._getAvailablePlatforms()[0].key, 'gitlab@corp');
console.log('8 GitLab UI assertions passed');
