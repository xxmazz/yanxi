# 共享评审账号与HTTPS部署准备

访问控制是可选配置。三项环境变量同时留空时，保留现有受信内网开发方式；开启后，浏览器以HTTP Basic账号登录，所有页面、API、PDF、原页图片、导出、接口文档和未知路径先经过认证。没有为公开演示开后门，health也不匿名放行。

## 配置账号

在已安装后端依赖的环境中运行：

```sh
.venv/bin/python scripts/hash_password.py
```

按提示两次输入至少12位的独立评审密码，不在命令参数中填写。脚本只输出YANXI_AUTH_PASSWORD_HASH配置，采用随机盐及固定参数scrypt；格式使用冒号，可直接放入由serve.sh读取的.env，避免美元符号被shell展开。不要将实际配置或评审凭据提交到Git。

在.env中配置：

```dotenv
YANXI_AUTH_USER=reviewer
# 粘贴上面脚本输出的完整哈希，以下占位文本不能直接运行。
YANXI_AUTH_PASSWORD_HASH=粘贴生成的完整哈希
YANXI_AUTH_ORIGIN=https://实际评审域名
YANXI_HOST=127.0.0.1
YANXI_PORT=18474
```

账号、哈希、访问源必须同时填写；缺项、非预期哈希参数或非法地址会在加载模型前报错，不能静默降为匿名模式。账号限英文字母、数字、点、下划线及短横线。应用检查实际请求协议与Host，HTTPS配置下明文HTTP请求不会触发密码挑战或被接受。仅localhost、127.0.0.1、::1可显式配置HTTP，用于回环测试。

启动前限制.env文件权限，按原后端说明启动。用户名和密码由部署者通过评审材料单独提供，前端不保存或注入密钥。此入口是共享账号、共享文献库；账号持有人能使用现有上传和修改功能，没有个人文献空间、角色权限和独立会话退出。需要撤销旧密码时，更换哈希并重启服务。

## HTTPS反向代理

仓库提供[域名配置示例](../deploy/Caddyfile.example)。实际域名的DNS和服务器80/443端口具备条件后，由Caddy接收HTTPS并转发到127.0.0.1:18474。后端保持回环监听，不直接把18474端口映射到公网。serve.sh显式只信任127.0.0.1代理的转发协议头；不读取任意X-Forwarded-Host作为认证来源。使用其他代理时须保持相同边界。

模板目前未在实际域名上部署或完成Caddy运行验收；安装与证书条件应对照[Caddy反向代理文档](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)。公网DNS、真实证书、外网浏览器登录及评审期间保活仍需另行验证，不能以本机TLS测试替代。

Vite开发服务器和静态预览不承担此认证。正式入口应反代提供完整前端构建与API的FastAPI服务。

## 请求与来源检查

- 前端api()统一添加X-Yanxi-Request: 1；开启认证时，修改请求及会消耗模型的论文检索GET必须带此标记。
- 修改请求携带Origin时必须精确匹配配置的访问源，null、重复Origin及其他源被拒绝；跨站/同站的子资源请求亦拒绝，保留顶层页面或PDF导航。
- CLI同样提供认证和请求标记；例如curl --user reviewer会交互询问密码，避免把密码写进命令历史：

```sh
curl --user reviewer https://实际评审域名/api/health
curl --user reviewer -H 'X-Yanxi-Request: 1' \
  -F file=@你的论文.pdf https://实际评审域名/api/documents
```

认证响应使用no-store、禁止跨站框架嵌入和同源资源策略；不会把密码、Authorization头或认证哈希写入响应。PDF保留原文件下载、Range/206和HEAD行为。没有WebSocket接口，开启认证时拒绝该协议。

## 验证范围

隔离行为测试覆盖全站入口、正确/错误凭据、重复和超长认证头、缺请求标记、跨源写入、跨站检索、伪造Host和代理头、HTTPS配置下HTTP拒绝、缺项配置及文件Range/HEAD。真实本机TLS验收另见[访问验证记录](REVIEW_ACCESS_VERIFICATION_20261029.json)。

本轮新增12项行为测试，后端合计145项通过；真实回环HTTPS验收72项通过，包括39组匿名路径/方法401、错误凭据401、跨站/缺标记403、带认证上传及15页解析、原件哈希、Range/206、HEAD和构图。问答无模型时明确503；另一个回环HTTP进程在HTTPS配置下拒绝匿名、正确凭据及伪造转发头请求，返回426。全部临时监听、私钥与证书目录已清理。报告记录基线提交及实际候选源码哈希，不把旧提交号当作新增认证代码已包含在内。

```sh
.venv/bin/python scripts/verify_review_access.py \
  --fixture-pdf /你的资料目录/1706.03762v7.pdf \
  --output runtime/review-access/REPORT.json
```

脚本创建独立回环服务、临时证书与随机密码，客户端用该证书正常验证TLS，测试后停止进程；不读取生产.env、不启动模型、不占用服务器GPU。它不创建正式评审凭据，不开放公网，不代表浏览器密码弹窗或外网HTTPS验收已经完成。

实现依据：[Python scrypt](https://docs.python.org/3/library/hashlib.html#hashlib.scrypt)、[常量时间摘要比较](https://docs.python.org/3/library/hmac.html#hmac.compare_digest)、[Starlette ASGI中间件](https://starlette.dev/middleware/#pure-asgi-middleware)。
