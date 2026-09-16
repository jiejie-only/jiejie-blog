# 安全说明 · 杰杰的博客

架构：**静态页面 + 轻量 Python 服务**。阅读无需登录；`/admin` 写作必须登录。

## 已内置的防护

| 项 | 做法 |
| --- | --- |
| 认证 | 单管理员；口令 PBKDF2-SHA256（26 万次）哈希存储于 `data/config.json` |
| 会话 | 签名 Cookie（HttpOnly、SameSite=Lax），默认 12 小时过期；写接口校验 CSRF |
| 登录限速 | 同 IP 5 分钟内最多 8 次失败尝试，超出返回 429 |
| CSP | 全站 `default-src 'self'`，无第三方脚本 |
| 其它响应头 | nosniff、DENY frame、Referrer-Policy、Permissions-Policy |
| 路径防护 | 静态服务拒绝 `data/`、`tools/`、点文件、`..` 穿越 |
| 前端 | 无 CDN；主题偏好仅存 localStorage |
| Markdown XSS | 标题/摘要/正文 HTML 转义；链接与图片 URL 消毒，拒绝 `javascript:` / 危险 `data:` |
| 粘贴/上传图片 | 需登录+CSRF；仅 PNG/JPEG/GIF/WebP 按魔数校验；≤5MB；随机文件名；每会话 10 分钟约 24 次；单日约 120 张 |
| 体积限制 | 单篇正文约 800KB 上限 |
| 路径 | slug 自动规范化；静态拒绝 `..`、`data/`、`tools/`、点文件 |

## 部署清单

1. **保护 `data/`**：权限 `600`，不要提交到 Git，不要通过 Web 直接暴露（Nginx `deny`）。
2. 使用强管理员密码；生产用 `JIEJIE_PASS` 注入，不要用默认打印口令上线。
3. 全站 HTTPS；启用后设置 `JIEJIE_SECURE_COOKIE=1`。
4. Nginx 按 `nginx/site.conf` 做反向代理，并保留 HSTS 等头。
5. 用 systemd / supervisor 守护 `server.py`，绑定 `127.0.0.1:8080`，仅由反代对外。
6. 定期备份 `posts/` 与 `data/config.json`（密钥离线保存）。

## 若扩展

- 多用户、评论、统计：需再评估会话与注入面；当前 Markdown 转 HTML 会转义原文，降低 XSS 风险。
- 不要把管理入口改到公开导航；直接访问 `/admin/login` 即可。

## 响应问题

发现漏洞请邮件：`hello@example.com`（请替换）。
