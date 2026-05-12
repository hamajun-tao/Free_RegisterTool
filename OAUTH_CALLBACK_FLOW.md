# OAuth Callback 流程说明

## 你看到的 OAuth Callback URL

```
http://127.0.0.1:49567/oauth/callback?code=eyJraWQiOiJrZXktMTU2NDAyODA3OCIsImFsZyI6IkhTMzg0In0...&state=b3a7882b-3dc2-4021-b86b-89316b974b4a
```

这个 URL 是 **ChatGPT OAuth 授权流程的回调地址**，用于完成账号注册后的授权确认。

## 完整流程解析

### 1. OAuth 授权流程

```
用户注册 ChatGPT
    ↓
验证邮箱/手机
    ↓
OpenAI 要求授权（Allow 按钮）
    ↓
点击 Allow 后重定向到 OAuth Callback
    ↓
系统捕获 callback URL 中的 code
    ↓
使用 code 换取 access_token
    ↓
账号注册完成
```

### 2. Callback URL 的作用

这个 callback URL 包含：
- **code**: 授权码（JWT 格式），用于换取 access_token
- **state**: 状态参数，用于防止 CSRF 攻击

系统会：
1. 捕获这个 callback URL
2. 提取 `code` 参数
3. 调用 OpenAI API 用 code 换取 `access_token`
4. 保存 access_token 到账号数据中

### 3. 账号数据保存位置

账号注册成功后，数据会保存到：

#### 本地数据库
```
c:\Desktop\auto_reg-main\auto_reg-main\account_manager.db
```

表：`accounts`

字段包括：
- `email`: 邮箱
- `token`: access_token（主要凭证）
- `extra`: JSON 字段，包含：
  - `access_token`: OAuth access token
  - `refresh_token`: 刷新令牌
  - `id_token`: ID 令牌
  - `session_token`: 会话令牌
  - `client_id`: 客户端 ID

### 4. 自动上传到外部系统

注册成功后，系统会**自动**将账号上传到配置的外部系统：

#### 4.1 Sub2API（你的情况）

**配置位置**：`.env` 文件
```env
SUB2API_API_URL=http://localhost:8080
SUB2API_API_KEY=Kns1wb8pVcQ2SURrxL
```

**上传流程**：
```
账号注册成功
    ↓
提取 access_token、refresh_token、id_token
    ↓
构建 Sub2API 格式的 payload
    ↓
POST 到 Sub2API 的 /api/v1/admin/accounts
    ↓
Sub2API 保存账号
    ↓
返回上传结果
    ↓
本地数据库记录同步状态
```

**上传的数据**：
```json
{
  "email": "user@example.com",
  "access_token": "eyJhbGc...",
  "refresh_token": "eyJhbGc...",
  "id_token": "eyJhbGc...",
  "session_token": "...",
  "expires_at": 1234567890,
  "organization_id": "org-xxx",
  "group_ids": [1, 2, 3]
}
```

#### 4.2 其他支持的外部系统

系统还支持上传到：

1. **CPA (Codex Protocol API)**
   - 配置：`CPA_API_URL`, `CPA_API_KEY`
   - 用途：账号管理和分发

2. **CodexProxy**
   - 配置：`CODEX_PROXY_URL`
   - 支持两种模式：
     - `at`: 上传 access_token
     - `rt`: 上传 refresh_token

3. **Grok2API**（Grok 平台）
   - 配置：`GROK2API_URL`, `GROK2API_KEY`

4. **Kiro Manager**（Kiro 平台）
   - 配置：`KIRO_MANAGER_PATH`

### 5. 查看上传状态

#### 方法 1：通过 API
```bash
GET http://localhost:8000/api/accounts?platform=chatgpt
```

响应中的 `extra.sync_statuses` 字段：
```json
{
  "email": "user@example.com",
  "extra": {
    "sync_statuses": {
      "sub2api": {
        "ok": true,
        "uploaded": true,
        "uploaded_at": "2026-05-09T06:00:00Z",
        "message": "上传成功"
      },
      "cpa": {
        "ok": true,
        "uploaded": true,
        "uploaded_at": "2026-05-09T06:00:00Z"
      }
    }
  }
}
```

#### 方法 2：通过前端界面
1. 打开 http://localhost:5173
2. 进入"账号管理"页面
3. 查看账号列表
4. 点击账号详情，查看"同步状态"

#### 方法 3：直接查询 Sub2API
```bash
GET http://localhost:8080/api/v1/admin/accounts
Authorization: Bearer Kns1wb8pVcQ2SURrxL
```

### 6. 手动上传账号

如果自动上传失败，可以手动上传：

#### 通过 API
```bash
POST http://localhost:8000/api/accounts/{account_id}/actions/upload_sub2api
Content-Type: application/json

{
  "api_url": "http://localhost:8080",
  "api_key": "Kns1wb8pVcQ2SURrxL"
}
```

#### 通过前端界面
1. 进入账号详情页
2. 点击"操作"按钮
3. 选择"上传 Sub2API"
4. 填写 API URL 和 API Key
5. 点击"执行"

### 7. 配置说明

#### 启用 Sub2API 自动上传

在 `.env` 文件中配置：
```env
# Sub2API 配置
SUB2API_API_URL=http://localhost:8080
SUB2API_API_KEY=your_api_key_here

# 可选：指定账号分组
# SUB2API_GROUP_IDS=1,2,3
```

或通过 API 配置：
```bash
POST http://localhost:8000/api/config
Content-Type: application/json

{
  "sub2api_api_url": "http://localhost:8080",
  "sub2api_api_key": "your_api_key_here",
  "sub2api_group_ids": "1,2,3"
}
```

#### 禁用自动上传

清空配置即可：
```bash
POST http://localhost:8000/api/config
Content-Type: application/json

{
  "sub2api_api_url": "",
  "sub2api_api_key": ""
}
```

### 8. 故障排查

#### 问题 1：账号未自动上传到 Sub2API

**检查步骤**：
1. 确认 Sub2API 配置正确
   ```bash
   GET http://localhost:8000/api/config
   ```

2. 查看账号同步状态
   ```bash
   GET http://localhost:8000/api/accounts/{account_id}
   ```

3. 检查后端日志
   ```
   c:\Desktop\auto_reg-main\auto_reg-main\backend-live-restart9.out.log
   ```

4. 手动重试上传
   ```bash
   POST http://localhost:8000/api/accounts/{account_id}/actions/upload_sub2api
   ```

#### 问题 2：Sub2API 返回错误

**常见错误**：
- `401 Unauthorized`: API Key 错误
- `404 Not Found`: API URL 错误
- `400 Bad Request`: 账号数据格式错误
- `500 Internal Server Error`: Sub2API 服务器错误

**解决方案**：
1. 验证 API Key 是否正确
2. 确认 Sub2API 服务正在运行
3. 检查账号数据是否完整（access_token, refresh_token 等）

#### 问题 3：OAuth Callback 未捕获

**可能原因**：
- 浏览器自动化失败
- 网络超时
- OpenAI 授权页面变化

**解决方案**：
1. 检查代理配置
2. 增加超时时间
3. 查看浏览器日志

### 9. 数据流向总结

```
用户点击 Allow
    ↓
OAuth Callback URL (包含 code)
    ↓
系统捕获 callback
    ↓
用 code 换取 access_token
    ↓
保存到本地数据库 (account_manager.db)
    ↓
自动上传到 Sub2API (如果配置了)
    ↓
Sub2API 保存账号
    ↓
本地记录同步状态
```

### 10. 最佳实践

1. **配置验证**：注册前先验证 Sub2API 配置
   ```bash
   curl http://localhost:8080/api/v1/admin/accounts \
     -H "Authorization: Bearer your_api_key"
   ```

2. **监控上传**：定期检查同步状态
   ```bash
   GET http://localhost:8000/api/accounts?platform=chatgpt&page=1&page_size=100
   ```

3. **失败重试**：对于上传失败的账号，使用批量重试
   ```python
   # 查询所有未上传的账号
   accounts = get_accounts(sync_status="failed")
   
   # 批量重试
   for account in accounts:
       retry_upload(account.id)
   ```

4. **日志监控**：关注后端日志中的上传错误
   ```bash
   tail -f backend-live-restart9.out.log | grep -i "sub2api"
   ```

## 总结

**OAuth Callback URL 的最终去向**：

1. ✅ **本地数据库**：`account_manager.db` 的 `accounts` 表
2. ✅ **Sub2API**：`http://localhost:8080/api/v1/admin/accounts`（如果配置了）
3. ✅ **其他外部系统**：CPA、CodexProxy、Grok2API 等（如果配置了）

**关键点**：
- OAuth callback 只是授权流程的一部分
- 真正的账号数据（access_token）是通过 callback 中的 code 换取的
- 账号数据会自动保存到本地数据库
- 如果配置了外部系统（如 Sub2API），会自动上传
- 所有上传状态都会记录在账号的 `extra.sync_statuses` 中
