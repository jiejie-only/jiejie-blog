(function () {
  "use strict";
  var form = document.getElementById("form");
  var btn = document.getElementById("btn");
  var statusEl = document.getElementById("status");
  var user = document.getElementById("username");
  var pass = document.getElementById("password");
  var toggle = document.getElementById("toggle-pass");

  toggle.addEventListener("click", function () {
    var show = pass.type === "password";
    pass.type = show ? "text" : "password";
    toggle.textContent = show ? "隐藏" : "显示";
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    btn.disabled = true;
    statusEl.textContent = "验证中…";
    statusEl.className = "status";
    fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        username: user.value.trim(),
        password: pass.value
      })
    }).then(function (res) {
      return res.json().then(function (data) {
        if (res.ok && data.ok) {
          statusEl.textContent = "登录成功";
          statusEl.className = "status ok";
          location.href = data.redirect || "/admin/write";
          return;
        }
        statusEl.textContent = data.error || "登录失败";
        statusEl.className = "status err";
        btn.disabled = false;
      });
    }).catch(function () {
      statusEl.textContent = "网络错误，请重试";
      statusEl.className = "status err";
      btn.disabled = false;
    });
  });
})();
