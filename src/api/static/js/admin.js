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
      var authMode = inst.auth_mode || (inst.enabled === false ? 'none' : (inst.allow_token_login ? 'token' : 'oauth'));
      itemDiv.innerHTML = [
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">',
        '  <b style="font-size:13px;color:var(--t)">实例 #' + (idx + 1) + ' (' + (inst.id || '新建') + ')</b>',
        '  <button type="button" class="s-btn" style="color:var(--r);border-color:#f3c2c2" onclick="removeGitlabInstanceRow(' + idx + ')">删除</button>',
        '</div>',
        '<div class="f-row"><label style="width:140px">ID (唯一标识)</label><input class="gl_id" value="' + (inst.id || '') + '" placeholder="如 official 或 cndatacom"></div>',
        '<div class="f-row"><label style="width:140px">名称 (显示名)</label><input class="gl_name" value="' + (inst.name || '') + '" placeholder="如 研发中心 GitLab 或 GitLab.com"></div>',
        '<div class="f-row"><label style="width:140px">Base URL</label><input class="gl_base_url" value="' + (inst.base_url || '') + '" placeholder="如 https://gitlab.devops.cndatacom.com"></div>',
        '<div class="f-row"><label style="width:140px">认证登录模式</label>',
        '  <div style="display:flex;gap:16px;align-items:center;font-size:13px">',
        '    <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="radio" name="gl_auth_mode_' + idx + '" value="none" ' + (authMode === 'none' ? 'checked' : '') + ' onchange="toggleGitlabAuthSection(' + idx + ')"> 未启用</label>',
        '    <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="radio" name="gl_auth_mode_' + idx + '" value="oauth" ' + (authMode === 'oauth' ? 'checked' : '') + ' onchange="toggleGitlabAuthSection(' + idx + ')"> OAuth 登录</label>',
        '    <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="radio" name="gl_auth_mode_' + idx + '" value="token" ' + (authMode === 'token' ? 'checked' : '') + ' onchange="toggleGitlabAuthSection(' + idx + ')"> Token 登录 / 系统凭据</label>',
        '  </div>',
        '</div>',
        '<div id="gl_oauth_sec_' + idx + '" style="display:' + (authMode === 'oauth' ? 'block' : 'none') + '">',
        '  <div class="f-row"><label style="width:140px">Client ID</label><input class="gl_client_id" value="' + (inst.client_id || '') + '" placeholder="GitLab OAuth Application ID"></div>',
        '  <div class="f-row"><label style="width:140px">Client Secret</label><input class="gl_client_secret" type="password" placeholder="' + (inst.client_secret_present ? '● 已配置（留空保持不变）' : 'GitLab OAuth Secret') + '"></div>',
        '  <div class="f-row"><label style="width:140px">Redirect URI</label><input class="gl_redirect_uri" value="' + (inst.redirect_uri || '') + '" placeholder="如 https://your-domain/api/auth/callback/gitlab"></div>',
        '</div>',
        '<div id="gl_token_sec_' + idx + '" style="display:' + (authMode === 'token' ? 'block' : 'none') + '">',
        '  <div class="f-row"><label style="width:140px">系统 Access Token</label><input class="gl_token" type="password" placeholder="' + (inst.token_present ? '● 已配置（留空保持不变）' : '用于该实例的仓库同步或兜底') + '">',
        '    <button type="button" class="s-btn" onclick="verifyInstanceToken(\'gitlab\', ' + idx + ')" style="margin-left:8px">校验 Token</button><span id="s_gl_token_' + idx + '" style="margin-left:8px;font-size:12px;color:var(--t2)"></span>',
        '  </div>',
        '  <div style="font-size:12px;color:var(--t3);margin-left:140px;margin-bottom:8px">可配置系统级 Bot Token（提供全局拉取仓库兜底），普通用户亦可在登录页输入各自的 PAT。</div>',
        '</div>',
        '<div class="f-row"><label style="width:140px">用户白名单</label><input class="gl_allowed_usernames" value="' + ((inst.allowed_usernames || []).join(', ')) + '" placeholder="逗号分隔，留空表示允许所有人"></div>'
      ].join('');
      container.appendChild(itemDiv);
    });
  }

  window.toggleGitlabAuthSection = function (idx) {
    var radios = document.getElementsByName('gl_auth_mode_' + idx);
    var val = 'none';
    for (var i = 0; i < radios.length; i++) { if (radios[i].checked) { val = radios[i].value; break; } }
    var oSec = $2('gl_oauth_sec_' + idx);
    var tSec = $2('gl_token_sec_' + idx);
    if (oSec) oSec.style.display = (val === 'oauth' ? 'block' : 'none');
    if (tSec) tSec.style.display = (val === 'token' ? 'block' : 'none');
  };

  function addGitlabInstanceRow() {
    _gitlabInstances.push({
      id: 'gl_' + Date.now().toString(36),
      name: '',
      base_url: '',
      auth_mode: 'oauth',
      client_id: '',
      client_secret: '',
      redirect_uri: window.location.origin + '/api/auth/callback/gitlab',
      token: '',
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
      var authMode = inst.auth_mode || (inst.enabled === false ? 'none' : (inst.allow_token_login ? 'token' : 'oauth'));
      itemDiv.innerHTML = [
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">',
        '  <b style="font-size:13px;color:var(--t)">实例 #' + (idx + 1) + ' (' + (inst.id || '新建') + ')</b>',
        '  <button type="button" class="s-btn" style="color:var(--r);border-color:#f3c2c2" onclick="removeGiteaInstanceRow(' + idx + ')">删除</button>',
        '</div>',
        '<div class="f-row"><label style="width:140px">ID (唯一标识)</label><input class="gt_id" value="' + (inst.id || '') + '" placeholder="如 official 或 private_gitea"></div>',
        '<div class="f-row"><label style="width:140px">名称 (显示名)</label><input class="gt_name" value="' + (inst.name || '') + '" placeholder="如 研发 Gitea 或 Gitea.com"></div>',
        '<div class="f-row"><label style="width:140px">Base URL</label><input class="gt_base_url" value="' + (inst.base_url || '') + '" placeholder="如 http://172.16.145.202:30344"></div>',
        '<div class="f-row"><label style="width:140px">认证登录模式</label>',
        '  <div style="display:flex;gap:16px;align-items:center;font-size:13px">',
        '    <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="radio" name="gt_auth_mode_' + idx + '" value="none" ' + (authMode === 'none' ? 'checked' : '') + ' onchange="toggleGiteaAuthSection(' + idx + ')"> 未启用</label>',
        '    <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="radio" name="gt_auth_mode_' + idx + '" value="oauth" ' + (authMode === 'oauth' ? 'checked' : '') + ' onchange="toggleGiteaAuthSection(' + idx + ')"> OAuth 登录</label>',
        '    <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="radio" name="gt_auth_mode_' + idx + '" value="token" ' + (authMode === 'token' ? 'checked' : '') + ' onchange="toggleGiteaAuthSection(' + idx + ')"> Token 登录 / 系统凭据</label>',
        '  </div>',
        '</div>',
        '<div id="gt_oauth_sec_' + idx + '" style="display:' + (authMode === 'oauth' ? 'block' : 'none') + '">',
        '  <div class="f-row"><label style="width:140px">Client ID</label><input class="gt_client_id" value="' + (inst.client_id || '') + '" placeholder="Gitea OAuth Application Client ID"></div>',
        '  <div class="f-row"><label style="width:140px">Client Secret</label><input class="gt_client_secret" type="password" placeholder="' + (inst.client_secret_present ? '● 已配置（留空保持不变）' : 'Gitea OAuth Client Secret') + '"></div>',
        '  <div class="f-row"><label style="width:140px">Redirect URI</label><input class="gt_redirect_uri" value="' + (inst.redirect_uri || '') + '" placeholder="如 http://your-domain/api/auth/callback/gitea"></div>',
        '</div>',
        '<div id="gt_token_sec_' + idx + '" style="display:' + (authMode === 'token' ? 'block' : 'none') + '">',
        '  <div class="f-row"><label style="width:140px">系统 Access Token</label><input class="gt_token" type="password" placeholder="' + (inst.token_present ? '● 已配置（留空保持不变）' : '用于该实例的仓库同步或兜底') + '">',
        '    <button type="button" class="s-btn" onclick="verifyInstanceToken(\'gitea\', ' + idx + ')" style="margin-left:8px">校验 Token</button><span id="s_gt_token_' + idx + '" style="margin-left:8px;font-size:12px;color:var(--t2)"></span>',
        '  </div>',
        '  <div style="font-size:12px;color:var(--t3);margin-left:140px;margin-bottom:8px">可配置系统级 Bot Token（提供全局拉取仓库兜底），普通用户亦可在登录页输入各自的 PAT。</div>',
        '</div>',
        '<div class="f-row"><label style="width:140px">用户白名单</label><input class="gt_allowed_usernames" value="' + ((inst.allowed_usernames || []).join(', ')) + '" placeholder="逗号分隔，留空表示允许所有人"></div>'
      ].join('');
      container.appendChild(itemDiv);
    });
  }

  window.toggleGiteaAuthSection = function (idx) {
    var radios = document.getElementsByName('gt_auth_mode_' + idx);
    var val = 'none';
    for (var i = 0; i < radios.length; i++) { if (radios[i].checked) { val = radios[i].value; break; } }
    var oSec = $2('gt_oauth_sec_' + idx);
    var tSec = $2('gt_token_sec_' + idx);
    if (oSec) oSec.style.display = (val === 'oauth' ? 'block' : 'none');
    if (tSec) tSec.style.display = (val === 'token' ? 'block' : 'none');
  };

  function addGiteaInstanceRow() {
    _giteaInstances.push({
      id: 'gt_' + Date.now().toString(36),
      name: '',
      base_url: '',
      auth_mode: 'oauth',
      client_id: '',
      client_secret: '',
      redirect_uri: window.location.origin + '/api/auth/callback/gitea',
      token: '',
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
      $2('f_server_base_url').value = c.server_base_url || '';
      $2('f_webhook_url').value = c.webhook_url || '';

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
    var sbUrl = $2('f_server_base_url').value.trim();
    if (sbUrl) body.server_base_url = sbUrl;
    var whUrl = $2('f_webhook_url').value.trim();
    if (whUrl) body.webhook_url = whUrl;

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
      var tok = row.querySelector('.gl_token') ? (row.querySelector('.gl_token').value || '').trim() : '';

      var glRadios = row.querySelectorAll('input[type="radio"][name^="gl_auth_mode_"]');
      var glAuthMode = 'none';
      for (var rIdx = 0; rIdx < glRadios.length; rIdx++) {
        if (glRadios[rIdx].checked) { glAuthMode = glRadios[rIdx].value; break; }
      }

      var allowed = (row.querySelector('.gl_allowed_usernames').value || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
      glList.push({
        id: key,
        name: name || key,
        base_url: base,
        auth_mode: glAuthMode,
        enabled: (glAuthMode !== 'none'),
        allow_token_login: (glAuthMode === 'token'),
        client_id: cid,
        client_secret: csec, // 若留空，后端会保留 previous_item 的 secret
        redirect_uri: ruri,
        token: tok,           // 若留空，后端会保留 previous_item 的 token
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
      var gtTok = gtRow.querySelector('.gt_token') ? (gtRow.querySelector('.gt_token').value || '').trim() : '';

      var gtRadios = gtRow.querySelectorAll('input[type="radio"][name^="gt_auth_mode_"]');
      var gtAuthMode = 'none';
      for (var gIdx = 0; gIdx < gtRadios.length; gIdx++) {
        if (gtRadios[gIdx].checked) { gtAuthMode = gtRadios[gIdx].value; break; }
      }

      var gtAllowed = (gtRow.querySelector('.gt_allowed_usernames').value || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
      gtList.push({
        id: gtKey,
        name: gtName || gtKey,
        base_url: gtBase,
        auth_mode: gtAuthMode,
        enabled: (gtAuthMode !== 'none'),
        allow_token_login: (gtAuthMode === 'token'),
        client_id: gtCid,
        client_secret: gtCsec,
        redirect_uri: gtRuri,
        token: gtTok,
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

  function verifyInstanceToken(provider, idx) {
    var prefix = provider === 'gitlab' ? 'gl' : 'gt';
    var containerId = provider === 'gitlab' ? 'gitlab_instances_container' : 'gitea_instances_container';
    var rows = document.querySelectorAll('#' + containerId + ' > div');
    if (!rows[idx]) return;
    var row = rows[idx];
    var tok = (row.querySelector('.' + prefix + '_token').value || '').trim();
    var base = (row.querySelector('.' + prefix + '_base_url').value || '').trim();
    var st = document.getElementById('s_' + prefix + '_token_' + idx);
    if (!tok) {
      if (st) st.textContent = '请先输入 Token';
      toast('请先填入 Token 再校验', false);
      return;
    }
    if (st) st.textContent = '校验中...';
    api('/config/verify-token', { method: 'POST', body: { provider: provider, token: tok, base_url: base } }).then(function (d) {
      if (d.ok) {
        if (st) { st.style.color = 'var(--g, #107c10)'; st.textContent = '✔ ' + (d.login || d.name); }
        toast(provider.toUpperCase() + ' Token 有效: ' + (d.login || d.name), true);
      } else {
        if (st) { st.style.color = 'var(--r, #dc2828)'; st.textContent = '✗ 无效'; }
        toast('Token 无效或权限不足', false);
      }
    }).catch(function (e) {
      if (st) { st.style.color = 'var(--r, #dc2828)'; st.textContent = '✗ ' + e.message; }
      toast('校验失败: ' + e.message, false);
    });
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
                      verifyInstanceToken: verifyInstanceToken,
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
  window.verifyInstanceToken = verifyInstanceToken;
  window.renderGitlabInstances = renderGitlabInstances;
  window.addGitlabInstanceRow = addGitlabInstanceRow;
  window.removeGitlabInstanceRow = removeGitlabInstanceRow;
  window.renderGiteaInstances = renderGiteaInstances;
  window.addGiteaInstanceRow = addGiteaInstanceRow;
  window.removeGiteaInstanceRow = removeGiteaInstanceRow;
  document.addEventListener('DOMContentLoaded', init);
})();