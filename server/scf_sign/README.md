# 扫码函数：私有桶 + 短时效签名

解决一个问题：**下载地址被截图、被转发之后，流量账一直算在作者头上。**

做法是两层：

1. 桶设成**私有**——直链本身失效，谁拿到旧地址也拉不动文件；
2. 客户端每次下载前，向这个小函数换一个 **10 分钟有效**的签名地址。

地址还是会出现在浏览器地址栏里（这是下载本身决定的），但它**会过期**。
转发出去过一会儿就没用了。

---

## 一、先建一个只能读这一个桶的子账号

不要用主账号 AK。阿里云控制台 → RAM 访问控制 → 用户 → 新建用户（勾选「使用永久
AccessKey」）→ 权限策略选自定义，内容：

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["oss:GetObject"],
      "Resource": ["acs:oss:*:*:<你的桶名>/windows-manager/*"]
    }
  ]
}
```

拿到 AccessKeyId / AccessKeySecret 备用。

## 二、部署函数（腾讯云 SCF）

1. 控制台 → 云函数 → 新建 → **从头开始**，运行环境 **Python 3.10**，地域选离你近的
   （广州 / 上海），内存 128 MB 足够，超时 5 秒。
2. 函数代码：把本目录的 `index.py` 贴进在线编辑器。
3. 依赖：`requirements.txt` 里只有 `oss2`。在上传代码时选「在线依赖安装」，
   或者本地 `pip install -r requirements.txt -t ./` 后连同代码一起打成 zip 上传
   （注意 `index.py` 要在 zip 根目录）。
4. 环境变量：

| 变量 | 值 |
|---|---|
| `OSS_AK` | 上面那个子账号的 AccessKeyId |
| `OSS_SK` | 对应 Secret |
| `OSS_BUCKET` | 桶名 |
| `OSS_ENDPOINT` | `https://<桶名>.oss-cn-beijing.aliyuncs.com` |
| `SIGN_TTL` | `600`（秒） |

5. 触发器：添加 **API 网关**触发器，新建一个，路径随便（例如 `/`），
   **勾选「集成响应」**——不勾的话客户端拿到的是网关包装过的错误格式。
6. 发布后拿到访问地址，形如
   `https://xxxxxxxx.apigw.tencentcs.com/release/`。

## 三、验证

```bash
curl -s "https://xxxxxxxx.apigw.tencentcs.com/release/health"
curl -s "https://xxxxxxxx.apigw.tencentcs.com/release/latest"
curl -s "https://xxxxxxxx.apigw.tencentcs.com/release/download"
```

`/latest` 应返回 `version` / `notes` / `size` / `url`，其中 `url` 带
`Signature=...&Expires=...`。把那个 url 复制到浏览器里应该能直接下载；
**等 10 分钟再试，应该 403** —— 这一步没过，说明桶还是公共读，签名形同虚设。

## 四、把桶改成私有

在 `tools/sync_oss.py` 上传时加 `--private`，或者控制台把桶 ACL 设成私有。
**顺序很重要**：先改客户端（第五步）并发出新版本，再关公共读。
反过来会让还在用旧版的人点下载就 403。

## 五、把地址填回客户端

```bash
python tools/obfuscate.py https://xxxxxxxx.apigw.tencentcs.com/release --key sign
```

把输出的 `_SIGN_ENC = bytes(...)` 整段替换 `core/updater.py` 里的同名常量，
重新打包。客户端拿到地址后会自己拼 `/latest` 和 `/download`。

## 六、成本与限额

- SCF：每月 100 万次调用、40 万 GBs 免费，实际用量每月几百次 → **0 元**
- API 网关：每月 100 万次免费额度，同样用不到零头
- OSS 流量：这才是花钱的地方，签名只保证「被转发的地址过期」，不减少正常下载

想再收紧：API 网关 → 限流（60 次/分钟/IP）。别做 Referer 白名单——
浏览器地址栏跳转的下载请求不带 Referer，会把真实用户一起挡掉。

## 七、这层防不住什么

说得直白些：函数地址是公开的，谁都能调它换签名，就像谁都能点客户端里的
「打开」。它能做的是**让地址有寿命**，不是**让人拿不到文件**。

真要「只有付费用户能下」，得加身份：客户端传一个许可码，函数校验通过才签。
那是另一套东西（要发码、要能吊销），需要的话再做。
