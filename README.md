# Landscape OpenWrt

[![Build](https://github.com/otaku-say/landscape-openwrt/actions/workflows/build.yml/badge.svg)](https://github.com/otaku-say/landscape-openwrt/actions/workflows/build.yml)

以 `piaoyizy/openwrt-x86:latest` 为基础，加入 Landscape 官方最新正式 Release 的 `redirect_pkg_handler-x86_64-static`，作为 Landscape IPv4/IPv6 流出口。保留 OpenWrt、LuCI 和 **PassWall**，移除 HomeProxy、OpenClash、Momo 及不再使用的 Mihomo/Ruby 等独占依赖。不自行重编译固件，不内置订阅或节点。

PassWall 运行时发现的 Xray、sing-box、Shadowsocks、v2ray-plugin、GeoView/规则数据等继续保留，不把共享核心误当成其他插件的独占依赖。ttyd 可以在容器中使用，因此保留并修复端口与 HTTPS 页面入口。构建先按实际 opkg 依赖图卸载，再将精简 rootfs 重打包到最终镜像，删除的文件不会残留在基础镜像层中。精简清单位于 `/usr/share/landscape-openwrt/prune-manifest.json`。

- 镜像：`ghcr.io/otaku-say/landscape-openwrt:latest`，仅 `linux/amd64`。
- 每天北京时间 **08:23** 检查上游镜像的 amd64 manifest digest 和接应程序最新正式版。GitHub 调度可能延迟。
- 上游 digest、接应程序版本/文件 SHA256、适配源码均未改变时，不重建。
- 候选镜像通过启动和双栈转发测试后才发布，最后更新 `latest`。
- 不使用个人 GitHub 密钥作为工作流 Secret，只使用仓库内置 `GITHUB_TOKEN`。

## 运行原理

```text
局域网设备，网关保持 10.10.10.1
  -> Landscape 分流规则
  -> Docker veth 上的 Landscape 标记流量
  -> redirect_pkg_handler --mode route：移除标记并送入协议栈
  -> OpenWrt firewall4 / PassWall
  -> Docker bridge -> Landscape 正常 WAN 出口
```

这里选用 **route 模式**，不是把所有流量强制送到固定 `12345` 端口。PassWall 自行管理透明代理端口、策略路由和防火墙。接应程序启动或注册成功，并不表示订阅、代理节点或 IPv6 代理已经工作。

原镜像 Docker Hub 的 macvlan 部署方法不用于此适配。接应程序依赖 veth peer 和 cgroup v2；必须使用一个独立 Docker **bridge** 网络，不能使用 `host`、macvlan、`init: true`，不要再增加第二个容器网络。

## 部署

在 Landscape 宿主机执行：

```bash
git clone https://github.com/otaku-say/landscape-openwrt.git
cd landscape-openwrt
docker compose pull
docker compose up -d
```

`docker-compose.yaml` 已按 LAN 地址 `10.10.10.1/24` 编写中文注释，不需要 `.env`：

| 用途 | 地址 |
| --- | --- |
| LuCI HTTP | `http://10.10.10.1:8080` |
| LuCI HTTPS | `https://10.10.10.1:8443`，初始证书通常自签名 |
| SSH | `ssh -p 2222 root@10.10.10.1` |
| ttyd 终端 | `http://10.10.10.1:7681`，使用 OpenWrt 登录账户 |
| 容器 IPv4 | `172.30.80.2/24` |
| 容器 IPv6 | `fd70:6c61:6e64:80::2/64` |

确认 `8080/8443/2222/7681` 和上述独立网段没有被占用，有冲突只调整 Compose；不要把 `10.10.10.1` 配给容器。其他用户把管理端口的宿主机地址改为自己的 LAN 地址，不需要修改运营商 IPv6。

管理端口只绑定宿主机 LAN IPv4，不映射 53 端口。容器获得公网 IPv6 后，默认防火墙拒绝以 `2000::/3` 为目标的 TCP/UDP 22/53/80/443/7681 访问，避免暴露管理服务和开放 DNS 递归，IPv4 映射和 ULA 管理地址不受影响。仍应在 Landscape 保留 WAN 防火墙；自行更改管理端口时也要调整保护规则。

HTTP LuCI 可直接内嵌 ttyd；HTTPS LuCI 中点击 **Open terminal** 在新窗口打开，避免浏览器阻止混合内容。默认 ttyd 使用 HTTP，不要向公网暴露；需要加密终端可使用 SSH，或自行配置 ttyd TLS/反向代理。若修改宿主机 ttyd 映射端口，需在 LuCI 的 ttyd 配置中设置相应 URL override。

官方接应挂载路径：

```text
/root/.landscape-router/unix_link -> /ld_unix_link，只读
```

lkit 环境可能使用 `/root/.lkit/landscape/data/unix_link`，请按真实目录修改。目录不存在时 Compose 会报错，不会悄悄创建一个没有 socket 的空目录。

上游公开的初始账户为 `root / password`，以当前上游镜像实际设置为准。首次登录后立即修改密码，不要向 WAN 开放管理端口。本仓库不保存你的密码。

首次 GHCR 发布默认可能为 Private，即使源码仓库公开也一样。仓库所有者需在 [镜像设置](https://github.com/users/otaku-say/packages/container/landscape-openwrt/settings) 将 Package visibility 改为 Public，之后才能匿名拉取。构建成功不等于包已经公开。

## 必须完成的 Landscape 网桥设置

Compose 首次创建 `ld-owrt0` 后，在 **Landscape 宿主机 Web 界面**完成：

1. 将 `ld-owrt0` 的 **ZONE 设为 LAN**。v0.24.3 只对 `docker0` 特殊显示 LANv6，普通 `undefined` 网桥不会显示该入口。
2. 开启 **LANv6**，选择 **纯 RA（SLAAC）**，新增所选 WAN 的 **上游 PD 前缀**，分配一个未被其他 LAN/容器网络占用的 `/64`。不要填写固定公网前缀；M/O 和 DHCPv6 保持关闭。
3. 同时开启 **LAN 路由转发服务（LR）**。没有 LR 时不能仅凭 Flow 源地址规则假定该桥已进入正确的数据转发路径。无需为这次部署添加猜测性的静态可达子网。
4. 该桥的 DHCPv4 保持关闭，地址仍由 Docker 分配。不要改动现有 `br_lan`、`docker0` 或把 veth 加入其他网桥。

容器会保留 Docker ULA 地址和默认网关，并接收 RA 得到动态公网 IPv6；在 IPv6 转发启用时自动设置 `accept_ra=2`，netifd 重启/接口更新后通过 hotplug 重设。只学习前缀和地址，`accept_ra_defrtr=0` 保留 Docker 网关。运营商前缀更新由内核按 RA lifetime 处理，不会把当前公网地址写死到 UCI。

这些是 **宿主机 Landscape 配置**，镜像不能跨网络命名空间代为完成；不会修改宿主机其他接口，也不会写入固定 `eth1`、ISP 地址或链路本地网关。多 WAN 用户自行选择上游和出口策略，不能据此保证任意双 WAN 自动故障切换。首次获得 RA 可能需要等待一个通告周期。

## Landscape 分流配置

1. Compose 已带 `ld_flow_edge: "true"` 标签；若通过 Landscape 界面创建，则勾选“用作 Flow 出口”，并填入相同 bridge、双栈地址、挂载、权限和 sysctl。
2. 容器启动后查看接应日志，等待 `send success` 注册成功，再把所需流的出口指向 `landscape-openwrt`。
3. 默认流和容器自身新建连接必须走正常 WAN，**不能再次指向这个容器**，否则代理节点连接会循环。不要一开始就把所有设备切换过去，先用单个测试设备。
4. 局域网设备继续使用原来的 `10.10.10.1` 网关和 Landscape DHCP。容器内 DHCPv4、DHCPv6、RA、NDP 服务已关闭。
5. 在 LuCI 的 PassWall 中导入订阅并启用。ACL 必须覆盖原始客户端网段 `10.10.10.0/24` 及实际 LAN IPv6 前缀，不能只覆盖容器的 `172.30.80.0/24`。

## 双栈边界

- Compose 创建 IPv4 + ULA IPv6 bridge；启动脚本读取 Docker 已分配的地址和网关，写为 OpenWrt `lan` 静态接口，避免 OpenWrt 生成 `br-lan` 抢占或清除 Docker 地址。
- OpenWrt 的 IPv4/IPv6 转发、同接口转发以及 LAN 区域 NAT44/NAT66 已启用；关闭反向路径过滤、ICMP redirects、流量卸载和 full-cone，避免绕过透明代理。
- ULA 不是公网 IPv6。默认方案需要上述 LAN + LANv6 + LR 设置和可用的上游 PD。只有按公网源前缀匹配的默认路由时，ULA 源地址不能依赖后续 NAT66 来补救先前失败的路由查找。不要把排障时手写的 `default from fd70:... via fe80:...` 当成通用部署要求。
- 容器获得公网地址和 ping6 成功，证明动态地址获取及当时的 IPv6 连通性，但尚不能排除此前临时路由的影响，也不能证明代理已生效。部署验收时要撤销自己添加的临时路由并复测。
- `route` 模式不采用 TProxy 模式的 ICMP 丢弃逻辑，ICMP 交给 OpenWrt 正常路由和防火墙。不要把 ICMP 直连成功当成代理成功。
- IPv6 流量是否代理取决于插件的 IPv6 透明代理选项、ACL 和实际核心能力；未配置时可能直连，不能声称“无 IPv6 泄漏”。
- 内核来自 Landscape 宿主机，镜像中的 OpenWrt 内核模块不能替换宿主内核。宿主机需支持 eBPF/TC、nftables、conntrack、IPv4/IPv6 NAT，以及所选插件需要的 TPROXY/TUN。缺失时在宿主机加载匹配模块或调整内核，不要安装不匹配的 OpenWrt kmod。
- 本镜像仅改变接应和容器初始化，不保证上游各插件版本一定支持特定协议，例如 VLESS XHTTP。请在 LuCI 中确认当前插件和核心版本。

## 配置与升级

持久化 `/etc/config` 和 `/etc/dropbear`。`/etc/config/network` 由 Docker 的静态地址每次启动自动重建，动态 RA 地址不会被固化；LuCI 的网络地址修改不会跨重建保留。PassWall 订阅、节点和其他 UCI 配置不被启动脚本整体覆盖。

默认上游 DNS 改为 `LAND_DNS_ADDR=223.5.5.5`，不依赖宿主机 LAN DNS。`/etc/resolv.conf` 继续指向本机 dnsmasq。对于旧版已初始化配置，仅在 dnsmasq 仍是单个旧默认 `10.10.10.1`、`noresolv=1` 且 PassWall 未启用时迁移，并保存 `/etc/config/.landscape-dhcp-before-dns-v2`。自定义 DNS、多条/域名规则、本地代理端口和启用中的 PassWall 不覆盖。此后只更新仍匹配镜像上次所管理值的 DNS。

从多插件版升级前先禁用 HomeProxy/OpenClash/Momo，并保存当前镜像 digest 与配置备份。新版停用持久化防火墙中对应的旧 include，不删除你的插件配置文件。新版 Compose 不再挂载 `openclash-data`，旧 Docker 卷仍保留但不使用，不会自动删除。沿用原 Compose 项目目录和 `openwrt-config` 卷，才能继续使用原 PassWall 设置。

不挂载整个 `/etc`、`/lib` 或根目录，防止旧数据遮住更新的系统、服务脚本和接应程序。OpenWrt 硬件 preinit 已替换为容器专用空阶段，避免探测/挂载宿主启动盘、升级引导器或重命名网卡；保留 `/sbin/init` 和 procd 作为真正启动/监督机制。关闭容器中的 NTP 校时、全局 sysctl/网卡调优、自动重启和上游跑分任务，避免特权容器影响宿主机；保留 cron 以支持代理插件的订阅刷新。

**注意：** `/etc/shadow`、用户新装的软件包以及挂载目录以外的插件文件不在持久卷中。容器重建后 root 密码会恢复上游初始设置，必须重新修改；新增软件和额外文件应先备份。不要在容器中刷写完整固件或执行 sysupgrade。

更新前使用 LuCI“系统 → 备份/升级”导出配置，并另外备份订阅、插件文件和 Docker 数据卷。然后：

```bash
docker compose pull
docker compose up -d
```

仓库每天自动发布镜像，但**不会自动替换本地容器**。`docker compose down` 保留命名卷；不要使用 `down -v`，它会删除配置。

标签保持简洁：`latest`、`openwrt-<实际版本>`、`redirect_pkg_handler-v<版本>`、二者组合。上游同版本重编译时这些标签会更新；需要确定回滚目标时保存已部署的完整 `image@sha256:...`，不要依赖可变标签。完整来源 digest 与校验值在 OCI labels 和 `/usr/share/landscape-openwrt/upstream.json` 中。

## 检查与排障

```bash
docker exec landscape-openwrt logread -e redirect_pkg_handler
docker exec landscape-openwrt /etc/init.d/landscape-redirect status
docker exec landscape-openwrt ip -4 route
docker exec landscape-openwrt ip -6 route
docker exec landscape-openwrt ip -6 addr show dev eth0 scope global
docker exec landscape-openwrt sysctl net.ipv6.conf.eth0.accept_ra
docker exec landscape-openwrt nslookup www.baidu.com 127.0.0.1
docker exec landscape-openwrt nft list chain inet fw4 srcnat_lan
docker inspect --format '{{json .State.Health}}' landscape-openwrt
```

接应日志主要进入 OpenWrt `logread`，不能只看 `docker logs`。注册失败检查 socket 路径和 Landscape 服务；BPF 加载失败检查 cgroup v2、权限和宿主内核兼容性。接应程序跟踪最新正式版，建议宿主 Landscape 也更新到相同正式系列；不能保证任意旧宿主与新接应程序兼容。

健康检查仅代表本地服务、路由、防火墙、dnsmasq 进程与 RA 参数就绪，**不验证** 公网地址获取、外部 DNS、Landscape 注册或代理连通性。GitHub CI 另行验证模拟注册 socket、双栈 LuCI、NAT44/NAT66、隔离 DNS A/AAAA 转发和迁移保护、RA 动态获取/前缀更换/netifd 重启、ttyd WebSocket/PTY 键盘输入、公网 IPv6 管理端口保护和配置卷重建。

CI 的 IPv6 使用文档测试前缀和模拟 RA，不是真实运营商 PD 或 Landscape eBPF Flow，不能替代 LAN → Landscape 标记流 → 代理节点的端到端验收。

实际切流前在同一测试设备分别检查 IPv4/IPv6 出口 IP、TCP、UDP、DNS、节点故障时的直连行为，以及局域网访问。DNS 分流也要独立核对，不能仅依据网页打开成功判断无泄漏。

## 工作流与来源

手动重建：Actions → Check upstreams and publish → Run workflow → `force=true`。首次尚无可读镜像时可选 `first_build=true`。下载必须匹配 GitHub 官方资产 SHA256；上游缺少资产、校验失败、架构改变或启动测试失败时停止发布。

公开仓库长期没有活动可能被 GitHub 暂停定时工作流，因此定时任务每 40 天至多提交一次 `.github/activity` 心跳。这类提交不改变镜像输入，不触发重建；需要保留内置 token 的 `contents: write` 权限。

- [Landscape 流出口文档](https://landscape.whileaway.dev/zh/features/traffic-flow.html)
- [Landscape 接应程序源码与正式发布](https://github.com/ThisSeanZhang/landscape/releases)
- [官方接应应用示例](https://github.com/landscape-router/landscape-apps)
- [OpenWrt 基础镜像](https://hub.docker.com/r/piaoyizy/openwrt-x86)
- [基础镜像构建仓库](https://github.com/piaoyizy/openwrt)

本仓库适配代码采用 GPL-3.0；Landscape 接应程序使用其原始 GPL-3.0 许可。对应版本源码链接写入构建元数据。基础固件及第三方插件保持各自许可，不能把整个组合镜像视为单一软件的重新授权。
