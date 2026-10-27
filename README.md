# 媒体达量管理

[![Release](https://img.shields.io/github/v/release/luowenfu/media-up-limit)](https://github.com/luowenfu/media-up-limit/releases)
[![Docker Hub](https://img.shields.io/docker/v/luowenfu/media-up-limit?label=Docker%20Hub&color=2496ED)](https://hub.docker.com/r/luowenfu/media-up-limit)

同时看多台 qBittorrent 和 Emby 的上传，按周期统计，达量后自动限速或停播。

![qBittorrent 设备与流量](./assets/qb.png)

## qBittorrent 达量限速：能做什么、怎么用

### 能做什么

- 统计每台 qB 在当前周期的上传量，到达设定档位后自动改 qB 的全局上传限速
- 多档规则逐级生效，例如 300GB 限 2MB/s、500GB 限 512KB/s
- 周期结束（每月 1 日、每周一或每天零点，按设置）自动恢复原限速

### 怎么用

1. 在「设备」里添加 qB 的地址、用户名和密码，确认显示在线
2. 在该设备的规则里选周期、填达量值和对应限速，可加多档
3. 保存后无需其他操作，到量自动限速，周期切换自动恢复

### 手动覆盖

- **内部覆盖**：在本页面手动限速或解除，规则行显示「内部覆盖 XXXKB」
- **外部覆盖**：直接在 qB 里改了全局限速，本程序识别后显示「外部覆盖 XXXKB」，不会立刻改回
- 两种覆盖都只管当前档；上传量到达更高一档时，规则自动接管并按新档限速

### 需要满足

| 条件 | 说明 |
| --- | --- |
| qB 开启 WebUI | 本程序通过 WebUI API 读取流量、修改限速 |
| 网络能访问 qB | 容器到 qB 的地址和端口要通 |
| 账号有权限 | 填写的 WebUI 用户名、密码正确 |
| 程序持续运行 | 停机期间不统计、不执行限速 |

### 注意

- **备用限速**：限速改的是 qB 的全局限速；如果 qB 开着「备用速度限制」，实际生效的是备用限速，请关闭或留意
- **有延迟**：上传量按轮询统计，到量后可能要等一个轮询周期才限速，实际上传会略超设定值
- **离线**：qB 离线期间无法统计和修改限速，恢复在线后按当前用量重新判定

## Emby

![Emby 播放与流量](./assets/emby.png)

## Emby 达量停播：原理与实现条件

> 仅在 Emby 流量采集为 Lucky 模式时生效。

### 一、功能概述

按周期统计每个 Emby 用户外网播放的上行流量。用户用到额度以后，下一次开播时会先弹出提示并暂停，5 秒后停止这次播放。局域网播放不受影响。这个功能默认只记录、不执行（dry run）。

### 二、工作原理

**1. 流量怎么统计**

- 外网上传量来自 Lucky 反代的连接流量。程序再拿这些流量去对 Emby 的用户和会话进行归属、裁决等，算出每个人用了多少。
- 浏览海报、选片产生的流量记作「选片流量」。打开「选片流量计入用户」后，这部分也算进额度。
- 用量按设定的周期累计（按月等），重置时间点可配置，时区跟着全局配置走。

**2. 会话怎么监控**

- 用的是轮询，不依赖 Webhook。每个采集周期调一次 Emby 的会话接口，拿到当前在线的播放会话（暂停中的也算）。
- 只看外网播放会话：远端地址是公网才处理，内网地址直接跳过。
- 每个会话用「会话 ID + 媒体 ID」区分。同一会话里换了片，或开播时间变了，都算新的一场。会话短暂掉线后 30 秒内又出现，不算重新开播。

**3. 判定规则**

- 一场播放只在开始时判定一次。播到一半才超额度的，这场不打断，下次开播再拦。
- 程序刚启动或刚启用这个功能时，已经在播的会话不处理，免得误伤。
- 额度分「每人额度」「共用额度」「独立额度」。独立额度优先；共用模式下，设了独立额度的用户不占共用池。额度填 0 表示不限。

**4. 达量后怎么停播**

1. 给客户端发提示并暂停播放。
2. 5 秒后发停止命令。
3. 确认是否已停下；如果播放进度还在走且仍达量，再来一轮，两轮间隔至少 30 秒。
4. 发送失败最多重试 3 次；会话没有遥控通道或已经结束的，不再重试。
5. 提高额度后，用户再开播即放行。

**5. 提示文案**

- 提示方式可选弹窗（dialog）或轻提示（toast）。
- 文案可选预设，也可自定义；文案里的 `{when}` 会替换成额度恢复时间。

### 三、实现条件

| 项目 | 要求 |
| --- | --- |
| Emby 权限 | API 密钥需能读取用户、会话，并能向会话发送命令 |
| Emby 版本 | 支持 Sessions 遥控接口（发消息、暂停、停止） |
| 客户端 | 需支持远程遥控；不支持的客户端无法弹提示或停播 |
| 流量采集 | 必须为 Lucky 模式，靠 Lucky 反代连接流量归属用户 |
| 网络 | 外网播放需经 Lucky 反代，程序能访问 Emby 与 Lucky |
| 插件 | 不需要安装 Emby 插件 |
| 配置 | 在页面 Emby 设置里开启「达量停播」，设好额度、周期和提示文案，确认无误后关闭「只记录不执行」 |

**限制与注意**

- 默认只记录、不执行（dry run），要真正停播需手动关闭。
- 播放中途超额度不打断，下次开播才拦。
- 判定依赖采集间隔，开播到停止会有一个采集周期左右的延迟。
- 局域网播放不拦。
- 读取用量失败时不拦，避免误停。

## 合并视图
![合并视图](./assets/mergeView.png)

## 使用

浏览器打开 `http://<主机IP>:8765`

首次打开会先设账号，设完自动登录。设备和全局设置都在页面里改。手机浏览器也能用。

### Docker Compose

```yaml
services:
  media-up-limit:
    image: luowenfu/media-up-limit:latest
    container_name: media-up-limit
    restart: unless-stopped
    network_mode: host
    environment:
      TZ: Asia/Shanghai
    volumes:
      - ./data:/data
```

也可以用 `ghcr.io/luowenfu/media-up-limit:latest`。

从旧镜像 `luowenfu/qb-up-limit` 升级时，只改镜像名，数据目录不用动。已保存的 qB、Emby、Lucky 连接会在下次启动时自动改封，不用重填。

### 反代后的真实 IP

前面是局域网反代时，会自动认出真实 IP。前面是公网反代（例如 Cloudflare）时，设置环境变量 `TRUSTED_PROXY_COUNT=1`，有几层就填几。填 `0` 则不信任转发头。

## 不要公开这些文件

- `data/config.yaml`
- `data/.qb_secrets`、`data/.emby_secrets`
- `data/.web_auth`、`data/.web_secret`、`data/.data_key`
- `data/*.db`、`data/app.log*`、`data/emby_events/`

本项目仅供学习测试。

## 许可证

[MIT](LICENSE)

## 致谢

- [qbittorrent-api](https://github.com/rmartin16/qbittorrent-api)
- [Flask](https://flask.palletsprojects.com/)
