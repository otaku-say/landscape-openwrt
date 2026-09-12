# Landscape ImmortalWrt PassWall

[![Build](https://github.com/otaku-say/landscape-openwrt/actions/workflows/build.yml/badge.svg)](https://github.com/otaku-say/landscape-openwrt/actions/workflows/build.yml)

从 **ImmortalWrt 最新正式版官方 x86_64 rootfs** 构建，安装 **PassWall 官方最新 Release 的 25.12+ APK 和简体中文包**，保留完整代理依赖以及 Landscape 官方最新正式版 `redirect_pkg_handler-x86_64-static`。仅 `linux/amd64`，不再使用 `piaoyizy/openwrt-x86`。

镜像：`ghcr.io/otaku-say/landscape-openwrt:latest`。

## 组件与来源

- 系统底层：从 [ImmortalWrt tags](https://github.com/immortalwrt/immortalwrt/tags) 按版本号选择最新正式版，排除 RC；下载官方 `rootfs.tar.gz` 并核验官方 SHA256。
- PassWall：[官方 Release](https://github.com/Openwrt-Passwall/openwrt-passwall/releases) 中 `25.12+_luci-app-passwall-<版本>.apk` 与 `25.12+_luci-i18n-passwall-zh-cn-<版本>.apk`，两者必须匹配，均核验 GitHub 资产 SHA256。
- 系统 APK 源：**https://mirrors.ustc.edu.cn/immortalwrt**，保持官方包签名验证，首次启动不会改到其他镜像源。
- PassWall 专用依赖源：官方 Release 推荐的 [openwrt-passwall-build](https://github.com/moetayuko/openwrt-passwall-build) 的 25.12/x86_64 `passwall_packages`，独立保留；不冒用中科大系统源替代专用依赖源。验证固定公钥指纹、索引签名，并按本次解析到的版本安装。
- 完整核心：**GeoView、ChinaDNS-NG、Xray、sing-box、Hysteria**，以及 NaiveProxy、Shadow-TLS、Shadowsocks Rust、SSR、simple-obfs、v2ray-plugin、xray-plugin、GeoIP/GeoSite、DNS/透明代理辅助工具和 HAProxy。
- 核对依据：[PassWall 完整 .run 参考包](https://github.com/bcseputetto/Are-u-ok/releases/download/iStoreOS_25.12/PassWall_26.9.9_x86_64_all_sdk_25.12.run) 中的 21 个依赖 APK + 安装脚本中的 HAProxy。只参考清单，不运行该安装脚本或固定使用其旧核心版本。清单见 `scripts/passwall-packages.txt`。
- 不安装 HomeProxy、OpenClash、Momo、PassWall2、ttyd。保留 LuCI、SSH 和系统必要组件；移除不适合容器的固件升级界面与硬件自动管理工具。

GitHub Release 的两个本地 PassWall APK 使用 SHA256 校验后离线安装，只有这一步允许未知 APK 签名；远程系统/依赖源不关闭签名或 HTTPS 验证。

实际安装版本记录在 `/usr/share/landscape-openwrt/packages.json`，上游 URL、摘要、源码版本在 `upstream.json`；不内置订阅、代理节点或登录密码。

## 全新部署

这是大版本重构，**不迁移、不兼容旧 OpenWrt 配置卷**。新版 Compose 使用全新 `immortalwrt-config`、`immortalwrt-dropbear` 卷；旧卷不被删除，但不会挂载。不要把旧 `/etc/config` 或完整备份恢复到新版。

```bash
git clone https://github.com/otaku-say/landscape-openwrt.git
cd landscape-openwrt
```

先编辑 `docker-compose.yaml`：

```yaml
environment:
  LAND_ROOT_PASSWORD: "CHANGE_ME_BEFORE_START"
  TZ: "Asia/Shanghai"
  LAND_DNS_ADDR: "223.5.5.5"
  LAND_REDIRECT_LOG_LEVEL: "INFO"
```

**必须替换 `LAND_ROOT_PASSWORD`**，至少 12 字符，不接受空值、占位值或换行。不需要 `.env`；密码含 `$` 时在 Compose 中写成 `$$`。这是运行时变量，不是 Docker build arg，不写入公开镜像或 UCI。Docker 管理员仍可通过容器配置查看环境变量，应限制 Compose 文件与 Docker 管理权限。

用户名为 **root**，LuCI 和 SSH 使用同一个密码。每次启动都按该变量设置 root 密码；修改变量后执行 `docker compose up -d`，容器重建后密码不会退回上游默认值。在 LuCI 中单独修改的密码会在下次启动时被 Compose 值覆盖。

`TZ` 支持 IANA 名称，例如 `Asia/Shanghai`、`Etc/UTC`、`Europe/Berlin`。镜像包含完整时区数据，每次启动同步 LuCI 的系统时区和本地时间，自动处理夏令时；修改后执行 `docker compose up -d`。不会调整宿主机系统时钟、硬件时钟或开启 NTP 校时。

保留你的宿主机管理地址、socket 路径和网段设置。仓库当前 Compose 默认：

| 用途 | 地址 |
| --- | --- |
| LuCI HTTP | `http://10.10.10.1:8000` |
| LuCI HTTPS | `https://10.10.10.1:8443`，首次生成自签证书 |
| SSH | `ssh -p 2222 root@10.10.10.1` |
| 容器 IPv4 | `172.30.80.2/24` |
| 容器 ULA IPv6 | `fd70:6c61:6e64:80::2/64` |
| Landscape socket | `/root/.lkit/landscape/data/unix_link` |

非 lkit 部署通常将 socket 改为 `/root/.landscape-router/unix_link`。保持同一网络与 bridge 名称 `ld-owrt0`，无需重设你已配置好的 Landscape LAN 服务。管理端口有冲突时调整宿主机端口。

```bash
docker compose pull
docker compose up -d
```

沿用旧部署目录时先更新仓库并检查 Compose 差异，替换为新版卷配置；不要执行 `down -v`。这次不会自动恢复旧订阅，请在全新 PassWall 中重新配置。

## Landscape 与动态 IPv6

1. 只接一个 Docker bridge 网络，使用 cgroup v2，保留 `ld_flow_edge: "true"` 和只读 socket 挂载；不使用 host/macvlan 网络、额外网卡或 `init: true`。
2. 在 Landscape 将 `ld-owrt0` 设为 **LAN**，同时开启 **LANv6** 和 **LAN 路由转发（LR）**。
3. LANv6 选纯 RA（SLAAC），从所选 WAN 的上游 PD 分配未占用的 `/64`。不填写固定运营商前缀，M/O 与 DHCPv6 关闭，桥上的 DHCPv4 也关闭。
4. 容器保留 Docker ULA 和静态默认网关，使用 `accept_ra=2` 接收动态公网地址、`accept_ra_defrtr=0` 保留 Docker 网关。网络重载后 hotplug 自动重设 RA 参数；公网地址不会固化到 UCI。
5. 配置容器自身流量走正常 WAN，不能再次导向自身，否则形成循环。多 WAN 上游选择和故障切换仍由宿主机策略决定。
6. 容器获得公网 IPv6 后，默认拒绝以 `2000::/3` 为目标的 TCP/UDP 22/53/80/443 访问，避免管理端口及开放 DNS 递归暴露。LAN IPv4 端口映射、ULA 管理访问保留；宿主机也应保留 WAN 防火墙。

```text
LAN 设备 -> Landscape Flow -> Docker veth 标记流量
  -> redirect_pkg_handler --mode route
  -> ImmortalWrt firewall4 / PassWall -> Landscape WAN
```

接应使用 route 模式，不强制固定 TProxy 端口。容器内 DHCPv4/DHCPv6/RA/NDP 服务关闭，但仍接收宿主机 RA；开启 NAT44/NAT66、同接口转发，禁用 flow offloading/full-cone。没有宿主机有效路由时，后续 NAT66 不能补救路由查找失败；不再将排障时固定 `eth1`/`fe80::...` 的路由当成部署配置。

内核仍来自宿主机，必须支持 eBPF/TC、nftables、conntrack、IPv6 NAT 及代理核心所需 TPROXY/TUN；镜像中的 kmod 不会替换宿主内核。不要在容器中刷写固件或运行 sysupgrade。

## DNS 与配置

首次将 dnsmasq 上游设为 `LAND_DNS_ADDR`，系统解析指向 `127.0.0.1`。初始化后 DNS 由 LuCI/PassWall 管理，不再包含旧镜像 DNS 迁移逻辑。PassWall 的订阅、节点、ACL 持久化在本代 `/etc/config` 卷；Docker 地址对应的 `network` 每次启动重建。

不要挂载整个 `/etc`、`/lib` 或根目录，否则会遮住新版系统。自装软件包、核心手动更新及卷以外的文件不会随重建保留。密码由 Compose 每次重设；SSH 主机密钥单独持久化。

## 每日自动更新

每天北京时间 **08:23** 检查，GitHub 定时运行可能延迟：

- PassWall 最新正式 Release、对应 APK 和中文包 SHA256。
- ImmortalWrt 最新正式 tag、官方 rootfs SHA256。
- PassWall 专用依赖索引、Landscape 最新正式接应程序、适配源码版本。

任一输入变化才构建；全部未变直接跳过。只有通过真实容器测试的候选镜像才推送，最后更新 `latest`。上游缺包、校验失败、依赖不满足或测试失败不会发布。公钥轮换需要审阅更新，不自动接受陌生签名。

自动更新的是 GHCR 镜像，不会替换你正在运行的容器：

```bash
docker compose pull
docker compose up -d
```

标签：`latest`、`immortalwrt-<版本>`、`passwall-<版本>`、`redirect_pkg_handler-v<版本>`、`immortalwrt-<版本>-passwall-<版本>`。旧 `openwrt-*` 标签不再更新；需要回滚时保存完整镜像 digest。

工作流用 GitHub 内置 `GITHUB_TOKEN`，不需要个人 PAT Secret。手动触发 Actions 的 `force=true` 可重建。每日检查每 40 天至多产生一次不触发镜像构建的心跳提交，避免长期无活动导致定时任务暂停。

## 验证与边界

```bash
docker exec landscape-openwrt logread -e redirect_pkg_handler
docker exec landscape-openwrt ip -6 addr show dev eth0 scope global
docker exec landscape-openwrt ip -6 route
docker exec landscape-openwrt nslookup www.baidu.com 127.0.0.1
docker exec landscape-openwrt apk info -e luci-app-passwall xray-core sing-box hysteria
docker inspect --format '{{json .State.Health}}' landscape-openwrt
```

CI 验证实际 LuCI 密码登录、错误密码拒绝、重建后密码变更、PassWall 页面与包版本、代理核心可执行、dnsmasq A/AAAA、模拟接应注册、双栈 NAT、RA 获取/前缀更换/netifd 重启与配置保留。真实 Landscape eBPF Flow、订阅节点、代理 TCP/UDP/IPv6 和 DNS 泄漏仍需部署后单独验收；ping6 成功不等于代理成功。

健康检查只代表本地服务、路由和 RA 参数就绪，不承诺外部网络或节点可用。宿主机之前添加的临时 IPv6 路由不会被镜像删除，撤销后需复测。

适配代码采用 GPL-3.0，ImmortalWrt、PassWall、接应程序和各依赖保持各自许可。来源与精确版本记录随镜像提供。
