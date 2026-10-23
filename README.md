# 媒体达量管理

[![Release](https://img.shields.io/github/v/release/luowenfu/media-up-limit)](https://github.com/luowenfu/media-up-limit/releases)
[![Docker Hub](https://img.shields.io/docker/v/luowenfu/media-up-limit?label=Docker%20Hub&color=2496ED)](https://hub.docker.com/r/luowenfu/media-up-limit)

同时看多台 qBittorrent 和 Emby 的上传，按周期统计，达量后自动限速或停播。

浏览器打开 `http://<主机IP>:8765`

## qBittorrent

每月上传到 500GB，自动把全局限速设成 512KB/s，下月 1 日再恢复。周期也可以按周或按天。

- 多台 qB，看实时上传、下载和是否在线
- 多级达量规则，达量后改全局限速，周期切换时按设置恢复
- 可手动限速或解除，也能先写好下个周期的计划
- 流量图表、事件记录、系统日志，数据写在本地 SQLite

![qBittorrent 设备与流量](./assets/image-20260624005902778.png)

## Emby

外网上传靠采集 Lucky 反代的连接流量，再和 Emby 的用户、会话进行归属、裁决等；浏览海报记成选片流量，可按需归属用户。

- 达量停播：可按总额度或每人额度，也能单独给某个用户设额度。达量后再开播会提示并停止，局域网不受影响。默认只记录，不执行
- 播放会话区分局域网和外网，外网 IP 默认打码
- 播放记录、选片记录、活动日志，以及按用户的图表
- 可按播放进度给记录卡片换底色
- 和 qB 分开看，或用合并视图放在一起
- 地址栏加上 `?emby_debug=1` 可打开调试面板

![Emby 播放与流量](./assets/image-20260624005902779.png)

![播放记录与选片记录](./assets/image-20260624005902780.png)

## 使用

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
