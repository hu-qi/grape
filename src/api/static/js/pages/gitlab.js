/* GitLab connection settings. Tokens are sent once and never cached in browser storage. */
window._gitlabInstanceUrls = window._gitlabInstanceUrls || {gitlab: 'https://gitlab.com'};

function renderGitLabConnections() {
  var root = document.getElementById('accountTabContent');
  if (!root) return;
  root.replaceChildren();
  var zh = typeof I18N === 'undefined' || I18N.getLang() === 'zh';
  var text = function(cn, en) { return zh ? cn : en; };
  var node = function(tag, content) {
    var el = document.createElement(tag);
    if (content) el.textContent = content;
    return el;
  };
  root.appendChild(node('p', text('绑定 GitLab.com 或管理员配置的私有 GitLab，选择项目后即可托管。', 'Connect GitLab.com or a configured private GitLab, then select a project to host.')));
  var select = node('select');
  select.setAttribute('aria-label', 'GitLab instance');
  var input = node('input');
  input.type = 'password';
  input.autocomplete = 'new-password';
  input.placeholder = 'Personal access token';
  input.setAttribute('aria-label', 'GitLab personal access token');
  var status = node('p');
  status.setAttribute('role', 'status');
  var list = node('div');
  var save = node('button', text('验证并绑定', 'Verify and connect'));
  save.className = 'bt bt-p';
  var load = node('button', text('加载项目', 'Load projects'));
  load.className = 'bt bt-o';
  var disconnect = node('button', text('解除绑定', 'Disconnect'));
  disconnect.className = 'bt bt-o';
  var controls = node('div');
  controls.style.cssText = 'display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:16px 0';
  select.style.maxWidth = '100%';
  input.style.minWidth = '220px';
  [select, input, save, load, disconnect].forEach(function(el) { controls.appendChild(el); });
  [controls, status, list].forEach(function(el) { root.appendChild(el); });
  var report = function(error) { status.textContent = error.message || String(error); };
  var records = [];
  function update() {
    var current = records.find(function(r) { return r.provider === select.value; });
    status.textContent = current && current.connected ? text('已绑定', 'Connected') : text('尚未绑定', 'Not connected');
    input.value = '';
    list.replaceChildren();
  }
  select.onchange = update;
  save.onclick = function() {
    var token = input.value.trim();
    input.value = '';
    if (!token) return;
    save.disabled = true;
    var provider = select.value;
    API.put('/gitlab/connection', {provider: provider, token: token}).then(function() {
      var current = records.find(function(r) { return r.provider === provider; });
      if (current) current.connected = true;
      update();
      if (app.loadProfile) app.loadProfile();
      localStorage.removeItem('repo_member_cache');
    }).catch(report).finally(function() { save.disabled = false; });
    token = '';
  };
  disconnect.onclick = function() {
    var provider = select.value;
    API.del('/gitlab/connection?provider=' + encodeURIComponent(provider)).then(function() {
      var current = records.find(function(r) { return r.provider === provider; });
      if (current) current.connected = false;
      update();
      if (app.loadProfile) app.loadProfile();
    }).catch(report);
  };
  load.onclick = function() {
    load.disabled = true;
    var provider = select.value;
    API.get('/gitlab/projects?provider=' + encodeURIComponent(provider)).then(function(data) {
      if (provider !== select.value) return;
      list.replaceChildren();
      (data.repos || []).forEach(function(repo) {
        var row = node('div');
        row.className = 'srow';
        row.appendChild(node('span', repo.full_name));
        var button = node('button', repo.is_hosted ? text('进入项目', 'Open project') : text('托管', 'Host'));
        button.className = 'bt bt-o';
        button.disabled = !repo.is_hosted && !repo.can_host;
        button.onclick = function() {
          function open() {
            selectRepo(repo.full_name, repo.owner, repo.name, repo.repo_key);
          }
          if (repo.is_hosted) return open();
          button.disabled = true;
          API.post('/gitlab/host', {provider: provider, project: repo.full_name}).then(function() {
            localStorage.removeItem('repo_member_cache');
            app.initSidebarRepos();
            open();
          }).catch(report).finally(function() { button.disabled = false; });
        };
        row.appendChild(button);
        list.appendChild(row);
      });
      status.textContent = text('项目列表已更新', 'Projects updated');
    }).catch(report).finally(function() { load.disabled = false; });
  };
  API.get('/gitlab/instances').then(function(data) {
    records = data.instances || [];
    records.forEach(function(instance) {
      window._gitlabInstanceUrls[instance.provider] = instance.url;
      var option = node('option', instance.url);
      option.value = instance.provider;
      select.appendChild(option);
    });
    update();
  }).catch(report);
}
