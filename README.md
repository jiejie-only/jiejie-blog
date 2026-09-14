# 杰杰的博客

个人博客：宣纸色阅读界面 + **站内登录写作**。  
公开页面仍是静态 HTML；用自带的 `server.py` 托管即可在浏览器里登录后发文章。

## 快速开始

```powershell
cd E:\test\jiejie-blog
python server.py
```

- 博客首页：`http://127.0.0.1:8080/`
- 登录写作：`http://127.0.0.1:8080/admin/login`

**首次启动**会在终端打印管理员用户名/密码，并写入 `data/config.json`（请妥善保存）。

也可用 `start-blog.bat` 一键启动。

## 写文章 / 管理文章

1. 打开 `/admin/login`，用管理员账号登录  
2. 左侧是**已发布列表**，可筛选标题  
3. **新建**：填表后点「发布文章」  
4. **编辑**：点列表里的「编辑」→ 改内容 →「保存修改」  
5. **删除**：列表「删除」或编辑页「删除此篇」（会同时去掉目录条目）
6. **导入**：左侧「导入」，支持多选 `.md` / `.txt` / `.html` / `.docx`  
   - Markdown 可带 front matter（`title` / `date` / `tag` / `excerpt`）  
   - HTML 会抽取标题并转成 Markdown  
   - Word 文档会读标题样式与段落  

自动维护：`posts/<slug>.html` + `.md`，以及 `posts.html`、首页「最新文章」。

支持 Markdown：`##` 标题、列表、`>` 引用、代码块、**加粗**、*斜体*。

## 目录结构

```
jiejie-blog/
├── server.py               # 整站服务（静态 + 登录写作 API）
├── admin/
│   ├── login.html          # 登录页
│   └── write.html          # 写文章页
├── data/config.json        # 账号与密钥（勿提交、勿公开）
├── index.html / posts.html / about.html
├── posts/                  # 文章 .html + .md
├── assets/                 # css / js / favicon
├── nginx/site.conf         # 反向代理 + 安全头示例
├── SECURITY.md
└── README.md
```

## 修改管理员密码

删除 `data/config.json` 后重启，会生成新口令；或在启动前设置：

```powershell
$env:JIEJIE_USER = "admin"
$env:JIEJIE_PASS = "你的新密码"
python server.py
```

## 部署到云服务器

1. 上传项目（**务必保护 `data/` 目录权限**，不要提交到公开仓库）。
2. 在服务器上：

```bash
export JIEJIE_HOST=0.0.0.0
export JIEJIE_PORT=8080
export JIEJIE_PASS='强密码'   # 首次
python3 server.py
```

3. 建议用 systemd 做进程守护，Nginx 做 HTTPS 反向代理：

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

完整安全头与 HTTPS 示例见 `nginx/site.conf`。  
若已启用 HTTPS，请设置 `JIEJIE_SECURE_COOKIE=1` 让 Cookie 带 `Secure`。

详细安全说明见 [SECURITY.md](SECURITY.md)。

## 设计说明

- 配色：纸 `#F3EFE6` / 墨 `#1A2420` / 苔 `#2F5C48` / 朱砂 `#C4512B`
- 无第三方前端依赖；深浅色主题、移动端可用
- 写作接口需登录会话 + CSRF，登录有频率限制
