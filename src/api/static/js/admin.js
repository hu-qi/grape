/* Grape Admin — 配置中心前端逻辑 */
(function () {
  'use strict';

  var TOKEN_KEY = 'grape_admin_token';
  var COOKIE_PATH = '/api/admin';

  function $2(id) { return document.getElementById(id); }

  function getToken() { return localStorage.getItem(TOKEN_KEY) || ''; }
  function setToken(t) { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY); }

  function toast(msg, ok) {
    var wrap = document.querySelector('.toast-wrap');
    if (!wrap) { wrap = document.createElement('div'); wrap.className = 'toast-wrap'; document.body.appendChild(wrap); }
    var el = document.createElement('div');
    el.className = 'toast ' + (ok ? 'ok' : 'err');
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(function () { el.remove(); }, 3500);
  }

  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    var tk = getToken();
    if (tk && path.indexOf('/login') < 0) opts.headers['Authorization'] = 'Bearer ' + tk;
    if (opts.body && typeof opts.body === 'object') opts.body = JSON.stringify(opts.body);
    return fetch('/api/admin' + path, opts).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) { var e = new Error(data && data.detail && data.detail.message) || '请求失败'; e.status = r.status; throw e; }
        return data;
      });
    });
  }

  // ====== 登录 ======
  function login() {
    var u = $2('loginUser').value.trim(), p = $2('loginPass').value.trim();
    if (!u || !p) { $2('loginErr').textContent = '请输入账号和密码'; return; }
    $2('loginErr').textContent = '';
    api('/login', { method: 'POST', body: { username: u, password: p } }).then(function (d) {
      setToken(d.token);
      $2('adminUser').textContent = d.user.username;
      showApp(d.pwd_initial);
    }).catch(function (e) { $2('loginErr').textContent = e.message; });
  }

  // ====== 视图切换 ======
  function showApp(pwdInitial) {
    $2('loginView').classList.add('hid');
    $2('mainView').classList.remove('hid');
    // 首次登录引导（默认密码）弹窗——仅在 pwd_initial 时弹出
    if (pwdInitial) openPwdModal();
  }

  // ====== 改密 ======
  var pwdModalMode = 'first';
  function openPwdModal(mode) {
    pwdModalMode = mode || 'first';
    $2('pwdModal').classList.remove('hid');
    $2('oldPass').value = ''; $2('newPass').value = ''; $2('confirmPass').value = '';
  }
  function closePwdModal() {
    $2('pwdModal').classList.add('hid');
  }
  function changePwd() {
    var oldP = $2('oldPass').value, newP = $2('newPass').value, confirmP = $2('confirmPass').value;
    if (!oldP || !newP) { toast('请填写原密码与新密码', false); return; }
    if (newP.length < 8) { toast('新密码至少 8 位', false); return; }
    if (newP !== confirmP) { toast('两次输入的新密码不一致', false); return; }
    api('/change-password', { method: 'POST', body: { old_password: oldP, new_password: newP } }).then(function () {
      toast('密码已修改，请重新登录', true);
      setToken(null);
      $2('pwdModal').classList.add('hid');
      $2('mainView').classList.add('hid');
      $2('loginView').classList.remove('hid');
      $2('loginPass').value = ''; $2('loginErr').textContent = '';
    }).catch(function (e) { toast(e.message, false); });
  }

  var _gitlabInstances = [];

  function renderGitlabInstances() {
    var container = $2('gitlab_instances_container');
    if (!container) return;
    container.innerHTML = '';
    if (_gitlabInstances.length === 0) {
      container.innerHTML = '<div style="color:var(--t2);font-size:12px;padding:8px 0;">暂未配置 GitLab 实例</div>';
      return;
    }
    _gitlabInstances.forEach(function (inst, idx) {
      var itemDiv = document.createElement('div');
      itemDiv.style.cssText = 'border:1px solid #e3e6ee;border-radius:8px;padding:12px;margin-bottom:10px;background:#fcfdfe;';
      itemDiv.innerHTML = [
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">',
        '  <b style="font-size:13px;color:var(--t)">实例 #' + (idx + 1) + ' (' + (inst.id || '新建') + ')</b>',
        '  <button type="button" class="s-btn" style="color:var(--r);border-color:#f3c2c2" onclick="removeGitlabInstanceRow(' + idx + ')">删除</button>',
        '</div>',
        '<div class="f-row"><label style="width:140px">ID (唯一标识)</label><input class="gl_id" value="' + (inst.id || '') + '" placeholder="如 private 或 cndatacom"></div>',
        '<div class="f-row"><label style="width:140px">名称 (显示名)</label><input class="gl_name" value="' + (inst.name || '') + '" placeholder="如 研发中心 GitLab"></div>',
        '<div class="f-row"><label style="width:140px">Base URL</label><input class="gl_base_url" value="' + (inst.base_url || '') + '" placeholder="如 https://gitlab.devops.cndatacom.com"></div>',
        '<div class="f-row"><label style="width:140px">Client ID</label><input class="gl_client_id" value="' + (inst.client_id || '') + '" placeholder="GitLab OAuth Application ID"></div>',
        '<div class="f-row"><label style="width:140px">Client Secret</label><input class="gl_client_secret" type="password" placeholder="' + (inst.client_secret_present ? '● 已配置（留空保持不变）' : 'GitLab OAuth Secret') + '"></div>',
        '<div class="f-row"><label style="width:140px">Redirect URI</label><input class="gl_redirect_uri" value="' + (inst.redirect_uri || '') + '" placeholder="如 https://your-domain/api/auth/callback/gitlab"></div>',
        '<div class="f-row"><label style="width:140px">启用登录</label><input type="checkbox" class="gl_enabled" ' + (inst.enabled !== false ? 'checked' : '') + ' style="width:auto"></div>',
        '<div class="f-row"><label style="width:140px">用户白名单</label><input class="gl_allowed_usernames" value="' + ((inst.allowed_usernames || []).join(', ')) + '" placeholder="逗号分隔，留空表示允许所有人"></div>'
      ].join('');
      container.appendChild(itemDiv);
    });
  }

  function addGitlabInstanceRow() {
    _gitlabInstances.push({
      id: 'gl_' + Date.now().toString(36),
      name: '',
      base_url: '',
      client_id: '',
      client_secret: '',
      redirect_uri: window.location.origin + '/api/auth/callback/gitlab',
      enabled: true,
      allowed_usernames: []
    });
    renderGitlabInstances();
  }

  function removeGitlabInstanceRow(idx) {
    _gitlabInstances.splice(idx, 1);
    renderGitlabInstances();
  }

  var _giteaInstances = [];

  function renderGiteaInstances() {
    var container = $2('gitea_instances_container');
    if (!container) return;
    container.innerHTML = '';
    if (_giteaInstances.length === 0) {
      container.innerHTML = '<div style="color:var(--t2);font-size:12px;padding:8px 0;">暂未配置 Gitea 实例</div>';
      return;
    }
    _giteaInstances.forEach(function (inst, idx) {
      var itemDiv = document.createElement('div');
      itemDiv.style.cssText = 'border:1px solid #e3e6ee;border-radius:8px;padding:12px;margin-bottom:10px;background:#fcfdfe;';
      itemDiv.innerHTML = [
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">',
        '  <b style="font-size:13px;color:var(--t)">实例 #' + (idx + 1) + ' (' + (inst.id || '新建') + ')</b>',
        '  <button type="button" class="s-btn" style="color:var(--r);border-color:#f3c2c2" onclick="removeGiteaInstanceRow(' + idx + ')">删除</button>',
        '</div>',
        '<div class="f-row"><label style="width:140px">ID (唯一标识)</label><input class="gt_id" value="' + (inst.id || '') + '" placeholder="如 official 或 private_gitea"></div>',
        '<div class="f-row"><label style="width:140px">名称 (显示名)</label><input class="gt_name" value="' + (inst.name || '') + '" placeholder="如 研发 Gitea 或 Gitea.com"></div>',
        '<div class="f-row"><label style="width:140px">Base URL</label><input class="gt_base_url" value="' + (inst.base_url || '') + '" placeholder="如 http://172.16.145.202:30344"></div>',
        '<div class="f-row"><label style="width:140px">Client ID</label><input class="gt_client_id" value="' + (inst.client_id || '') + '" placeholder="Gitea OAuth Application Client ID"></div>',
        '<div class="f-row"><label style="width:140px">Client Secret</label><input class="gt_client_secret" type="password" placeholder="' + (inst.client_secret_present ? '● 已配置（留空保持不变）' : 'Gitea OAuth Client Secret') + '"></div>',
        '<div class="f-row"><label style="width:140px">Redirect URI</label><input class="gt_redirect_uri" value="' + (inst.redirect_uri || '') + '" placeholder="如 http://your-domain/api/auth/callback/gitea"></div>',
        '<div class="f-row"><label style="width:140px">启用登录</label><input type="checkbox" class="gt_enabled" ' + (inst.enabled !== false ? 'checked' : '') + ' style="width:auto"></div>',
        '<div class="f-row"><label style="width:140px">用户白名单</label><input class="gt_allowed_usernames" value="' + ((inst.allowed_usernames || []).join(', ')) + '" placeholder="逗号分隔，留空表示允许所有人"></div>'
      ].join('');
      container.appendChild(itemDiv);
    });
  }

  function addGiteaInstanceRow() {
    _giteaInstances.push({
      id: 'gt_' + Date.now().toString(36),
      name: '',
      base_url: '',
      client_id: '',
      client_secret: '',
      redirect_uri: window.location.origin + '/api/auth/callback/gitea',
      enabled: true,
      allowed_usernames: []
    });
    renderGiteaInstances();
  }

  function removeGiteaInstanceRow(idx) {
    _giteaInstances.splice(idx, 1);
    renderGiteaInstances();
  }

  // ====== 登出 ======
  function logout() {
    api('/logout', { method: 'POST' }).then(function () { setToken(null); location.reload(); })
      .catch(function () { setToken(null); location.reload(); });
  }

  // ====== 配置中心 ======
  function showCfg() {
    api('/config').then(function (d) {
      var c = d.config || {};
      var os = c.oauth_status || {};
      applyToField('f_gitcode_api_token', c.gitcode_api_token_present, c.gitcode_api_token_tail);
      applyToField('f_github_bot_token', c.github_bot_token_present, c.github_bot_token_tail);
      // client_id / redirect_uri 非敏感，回显明文；secret 占位提示
      $2('f_gitcode_oauth_client_id').value = c.gitcode_oauth_client_id || '';
      $2('f_gitcode_oauth_client_id').placeholder = c.gitcode_oauth_client_id_present ? '● 已配置' : 'GitCode OAuth App Client ID';
      $2('f_github_oauth_client_id').value = c.github_oauth_client_id || '';
      $2('f_github_oauth_client_id').placeholder = c.github_oauth_client_id_present ? '● 已配置' : 'GitHub OAuth App Client ID';
      $2('f_gitcode_oauth_client_secret').value = '';
      $2('f_gitcode_oauth_client_secret').placeholder = os.gitcode ? '● 已配置（重新输入以更新）' : 'GitCode OAuth App Client Secret';
      $2('f_github_oauth_client_secret').value = '';
      $2('f_github_oauth_client_secret').placeholder = os.github ? '● 已配置（重新输入以更新）' : 'GitHub OAuth App Client Secret';
      $2('f_gitcode_oauth_redirect_uri').value = c.gitcode_oauth_redirect_uri || '';
      $2('f_gitcode_oauth_redirect_uri').placeholder = '留空自动推断';
      $2('f_github_oauth_redirect_uri').value = c.github_oauth_redirect_uri || '';
      $2('f_github_oauth_redirect_uri').placeholder = '留空自动推断';
      $2('f_bot_usernames').value = (c.bot_usernames || []).join(', ');

      // GitLab 配置
      $2('f_gitlab_mode').value = c.gitlab_mode || 'single';
      $2('f_gitlab_default_instance').value = c.gitlab_default_instance || '';
      _gitlabInstances = JSON.parse(JSON.stringify(c.gitlab_instances || []));
      renderGitlabInstances();

      // Gitea 配置
      $2('f_gitea_mode').value = c.gitea_mode || 'single';
      $2('f_gitea_default_instance').value = c.gitea_default_instance || '';
      _giteaInstances = JSON.parse(JSON.stringify(c.gitea_instances || []));
      renderGiteaInstances();
    }).catch(function (e) { toast('加载配置失败: ' + e.message, false); });
  }

  function applyToField(fieldId, present, tail) {
    var el = $2(fieldId);
    if (present) { el.value = ''; el.placeholder = '● 已配置 (…' + tail + ')'; }
    else { el.value = ''; el.placeholder = '○ 未配置'; }
  }

  function saveConfig() {
    $2('saveBtn').disabled = true;
    var body = {};
    var gitcodeTok = $2('f_gitcode_api_token').value.trim();
    var githubTok = $2('f_github_bot_token').value.trim();
    if (gitcodeTok) body.gitcode_api_token = gitcodeTok;
    if (githubTok) body.github_bot_token = githubTok;
    var gcdClientId = $2('f_gitcode_oauth_client_id').value.trim();
    if (gcdClientId && gcdClientId.indexOf('●') < 0) body.gitcode_oauth_client_id = gcdClientId;
    var gcdClientSec = $2('f_gitcode_oauth_client_secret').value.trim();
    if (gcdClientSec) body.gitcode_oauth_client_secret = gcdClientSec;
    var gcdRedirect = $2('f_gitcode_oauth_redirect_uri').value.trim();
    if (gcdRedirect) body.gitcode_oauth_redirect_uri = gcdRedirect;
    var ghClientId = $2('f_github_oauth_client_id').value.trim();
    if (ghClientId && ghClientId.indexOf('●') < 0) body.github_oauth_client_id = ghClientId;
    var ghClientSec = $2('f_github_oauth_client_secret').value.trim();
    if (ghClientSec) body.github_oauth_client_secret = ghClientSec;
    var ghRedirect = $2('f_github_oauth_redirect_uri').value.trim();
    if (ghRedirect) body.github_oauth_redirect_uri = ghRedirect;
    var us = $2('f_bot_usernames').value.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    if (us.length) body.bot_usernames = us;

    // 收集 GitLab 配置
    body.gitlab_mode = $2('f_gitlab_mode').value;
    body.gitlab_default_instance = $2('f_gitlab_default_instance').value.trim();
    var glRows = document.querySelectorAll('#gitlab_instances_container > div');
    var glList = [];
    for (var i = 0; i < glRows.length; i++) {
      var row = glRows[i];
      var key = (row.querySelector('.gl_id').value || '').trim();
      var name = (row.querySelector('.gl_name').value || '').trim();
      var base = (row.querySelector('.gl_base_url').value || '').trim();
      var cid = (row.querySelector('.gl_client_id').value || '').trim();
      var csec = (row.querySelector('.gl_client_secret').value || '').trim();
      var ruri = (row.querySelector('.gl_redirect_uri').value || '').trim();
      var enabled = row.querySelector('.gl_enabled').checked;
      var allowed = (row.querySelector('.gl_allowed_usernames').value || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
      glList.push({
        id: key,
        name: name || key,
        base_url: base,
        client_id: cid,
        client_secret: csec, // 若留空，后端会保留 previous_item 的 secret
        redirect_uri: ruri,
        enabled: enabled,
        allowed_usernames: allowed
      });
    }
    body.gitlab_instances = glList;

    // 收集 Gitea 配置
    body.gitea_mode = $2('f_gitea_mode').value;
    body.gitea_default_instance = $2('f_gitea_default_instance').value.trim();
    var gtRows = document.querySelectorAll('#gitea_instances_container > div');
    var gtList = [];
    for (var j = 0; j < gtRows.length; j++) {
      var gtRow = gtRows[j];
      var gtKey = (gtRow.querySelector('.gt_id').value || '').trim();
      var gtName = (gtRow.querySelector('.gt_name').value || '').trim();
      var gtBase = (gtRow.querySelector('.gt_base_url').value || '').trim();
      var gtCid = (gtRow.querySelector('.gt_client_id').value || '').trim();
      var gtCsec = (gtRow.querySelector('.gt_client_secret').value || '').trim();
      var gtRuri = (gtRow.querySelector('.gt_redirect_uri').value || '').trim();
      var gtEnabled = gtRow.querySelector('.gt_enabled').checked;
      var gtAllowed = (gtRow.querySelector('.gt_allowed_usernames').value || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
      gtList.push({
        id: gtKey,
        name: gtName || gtKey,
        base_url: gtBase,
        client_id: gtCid,
        client_secret: gtCsec,
        redirect_uri: gtRuri,
        enabled: gtEnabled,
        allowed_usernames: gtAllowed
      });
    }
    body.gitea_instances = gtList;

    api('/config', { method: 'PUT', body: body }).then(function (d) {
      toast('配置已保存', true);
      showCfg();
    }).catch(function (e) { toast('保存失败: ' + e.message, false); })
      .finally(function () { $2('saveBtn').disabled = false; });
  }

  function verifyToken(provider, fieldId) {
    var tok = $2(fieldId).value.trim();
    if (!tok) { toast('请先填入 token 再校验', false); return; }
    api('/config/verify-token', { method: 'POST', body: { provider: provider, token: tok } }).then(function (d) {
      var st = $2('s_' + fieldId.replace('f_', ''));
      if (d.ok) { st.textContent = '✔ ' + (d.login || d.name); toast('token 有效: ' + d.login, true); }
      else { st.textContent = '✗ 无效'; toast('token 无效或不具备访问权限', false); }
    }).catch(function (e) { toast('校验失败: ' + e.message, false); });
  }

  // ====== 初始化 ======
  function init() {
    $2('loginBtn').onclick = login;
    document.getElementById('loginPass').addEventListener('keydown', function (e) { if (e.key === 'Enter') login(); });
    // 已登录则直接进主界面
    if (getToken()) { showApp(false); showCfg(); }
  }

  window.AdminApp = { login: login, changePwd: changePwd, closePwdModal: closePwdModal, openPwdModal: openPwdModal,
                      logout: logout, showCfg: showCfg, saveConfig: saveConfig, verifyToken: verifyToken,
                      renderGitlabInstances: renderGitlabInstances, addGitlabInstanceRow: addGitlabInstanceRow,
                      removeGitlabInstanceRow: removeGitlabInstanceRow,
                      renderGiteaInstances: renderGiteaInstances, addGiteaInstanceRow: addGiteaInstanceRow,
                      removeGiteaInstanceRow: removeGiteaInstanceRow };
  // 暴露到全局——admin.html 内联 onclick 引用裸函数名
  window.login = login;
  window.changePwd = changePwd;
  window.closePwdModal = closePwdModal;
  window.openPwdModal = openPwdModal;
  window.logout = logout;
  window.showCfg = showCfg;
  window.saveConfig = saveConfig;
  window.verifyToken = verifyToken;
  window.renderGitlabInstances = renderGitlabInstances;
  window.addGitlabInstanceRow = addGitlabInstanceRow;
  window.removeGitlabInstanceRow = removeGitlabInstanceRow;
  window.renderGiteaInstances = renderGiteaInstances;
  window.addGiteaInstanceRow = addGiteaInstanceRow;
  window.removeGiteaInstanceRow = removeGiteaInstanceRow;
  document.addEventListener('DOMContentLoaded', init);
})();