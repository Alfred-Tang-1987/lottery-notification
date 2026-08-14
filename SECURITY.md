# 安全政策

## 报告漏洞

**请勿公开开 Issue 报告安全漏洞。** 请使用 GitHub 私有漏洞报告（Repository → Security → Advisories → Report a vulnerability），或按仓库 owner 主页公开联系方式私下报告。预期响应：72 小时内确认收到。

## 范围

重点关注：认证 / 邀请码机制、用户数据隔离（IDOR）、渠道密钥加密（Fernet）、密码重置流程、通知内容泄露。

## 自托管安全基线

- `.env` 密钥必须自行生成（`scripts/init-env.sh`），**切勿照抄任何示例值**
- HTTPS 部署保持 `COOKIE_SECURE=true`；HTTP 仅限受信局域网
- 历史提交者邮箱含早期开发环境的指纹信息（已知并接受，见 spec 2026-08-14 E3）；当前提交使用 GitHub noreply 邮箱
