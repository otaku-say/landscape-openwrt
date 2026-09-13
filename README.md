# Landscape ImmortalWrt PassWall

[![Build](https://github.com/otaku-say/landscape-openwrt/actions/workflows/build.yml/badge.svg)](https://github.com/otaku-say/landscape-openwrt/actions/workflows/build.yml)

作为 **Landscape Flow 的双栈透明代理出口**：官方接应程序将标记流量交给 ImmortalWrt，由 PassWall 按节点和分流策略代理。采用最新正式版官方 x86_64 rootfs、PassWall 官方 APK 和简体中文包，保留完整代理依赖。仅 `linux/amd64`，可按 `.env` 部署到不同网络，不内置某个家庭 LAN、WAN 或运营商前缀。

镜像：`ghcr.io/otaku-say/landscape-openwrt:latest`。

## 管理与网络设计

**通过容器固定 IPv4 或 ULA IPv6 直接管理，不发布任何宿主机端口。** `.env` 中的端口就是 uhttpd、Dropbear 的实际监听端口，不是 Docker 映射端口。

| 用途 | 默认入口 |
| --- | --- |
| LuCI HTTP | `http://172.30.66.2` |
| LuCI HTTPS | `https://172.30.66.2`，使用自签证书 |
| SSH | `ssh root@172.30.66.2` |
| ULA HTTP | `http://[fd70:6c61:6e64:66::2]` |
| ULA HTTPS | `https://[fd70:6c61:6e64:66::2]` |
| ULA SSH | `ssh root@fd70:6c61:6e64:66::2` |

**Landscape 部署必须为容器网桥开启 LAN 路由转发（LR）。** 管理与透明代理共用完整的 Landscape 转发路径，不兼容关闭此桥 LR 后的混合转发状态。部分接口走 eBPF、部分走内核时，宿主机 MASQUERADE 可能误改 LAN 回包地址；本项目不为该状态增加宿主机 NAT 补丁或专用服务。

网桥保留双栈 `nat-unprotected` gateway mode，以允许访问原生容器端口并保留出站 masquerade。该设置不产生管理端口 DNAT，**也不替代 LR**。容器内部 NAT44/NAT66、官方 route-mode 接应、PassWall 的透明代理规则不因管理入口而削减。

运行要求：**Docker Engine 28+、Docker Compose v2+、宿主机双栈转发开启，以及正确配置的 Landscape LAN/LR 服务**。客户端应以 Landscape 为网关，或有经 Landscape 到容器网段的路由；宿主机自定义防火墙不能阻断该路径。

`nat-unprotected` 不为该网桥过滤未发布端口，访问控制由容器 firewall4 和宿主机 WAN 防火墙负责：

- 管理端口及 DNS 的 IPv4 访问仅允许 RFC1918 私网来源；非私网来源拒绝。
- 拒绝目标为公网 IPv6 `2000::/3` 的管理端口及 DNS 访问，保留 ULA 管理。
- 防护端口随 `.env` 配置同步，改端口后不会仍只保护 22/80/443。
- 以上规则作用于容器本机输入，不限制 PassWall 的转发路径。自行新增监听服务时需配置相应访问控制。
- 不创建宿主机 SNAT 补丁、反向代理、特权辅助容器或宿主机防火墙修改脚本。

设计依据见 [Docker port publishing / gateway modes](https://docs.docker.com/engine/network/port-publishing/#gateway-modes)。Docker 的其他 bridge 网络仍受跨桥隔离约束；这里的直连入口面向经宿主机路由的 LAN 客户端。

## 全新部署

使用新的部署配置与空卷。默认卷为 `landscape-openwrt-direct-config`、`landscape-openwrt-direct-dropbear`，不读取或转换此前部署的配置，不导入完整系统备份。已有容器、网络、卷及宿主机临时规则不会被本项目自动删除。

```bash
git clone https://github.com/otaku-say/landscape-openwrt.git
cd landscape-openwrt
cp -n .env.example .env
chmod 600 .env
```

编辑 `.env`，每项都有中文说明，无需 `source` 或 `export`：

```dotenv
LAND_ROOT_PASSWORD='CHANGE_ME_BEFORE_START'
TZ=Asia/Shanghai
LUCI_HTTP_PORT=80
LUCI_HTTPS_PORT=443
SSH_PORT=22
```

**必须替换密码占位值。** 不限制密码长度或复杂度，短密码和纯数字均可；不接受空值、占位值或换行。用单引号包住密码，`$`、`#` 和空格按字面读取，不需要把 `$` 改成 `$$`。

root 的 LuCI 和 SSH 使用同一个运行时密码。密码不是 build arg，不写入公开镜像或 UCI，设置后从 PID 1 的环境中移除；Docker 管理员仍能读取容器配置，应限制 `.env` 文件与 Docker 管理权限。真实 `.env` 不会提交或加入构建上下文。

端口变量可省略，镜像及 Compose 默认均为 HTTP 80、HTTPS 443、SSH 22。三个管理端口必须互不相同，范围为 1–65535，不带前导零，不能使用 DNS 的 53。每次启动都从镜像自带的服务配置生成 uhttpd、Dropbear 设置，再应用端口变量，不保留额外的旧监听入口。端口与密码修改后执行 `docker compose up -d` 生效；在 LuCI 中直接修改这些受控设置会在下次启动时被运行时参数覆盖。

`TZ` 支持 IANA 时区，例如 `Asia/Shanghai`、`Etc/UTC`、`Europe/Berlin`；包含完整时区数据，每次启动同步 LuCI 和本地时间，自动处理夏令时。不调整宿主机时钟、硬件时钟或启动 NTP 校时。

其余默认设置：

| 设置 | 默认值 |
| --- | --- |
| Docker 网络 / 宿主机网桥 | `landscape-openwrt` / `br-openwrt` |
| IPv4 子网 / 网关 / 容器 | `172.30.66.0/24` / `172.30.66.1` / `172.30.66.2` |
| ULA 子网 / 网关 / 容器 | `fd70:6c61:6e64:66::/64` / `fd70:6c61:6e64:66::1` / `fd70:6c61:6e64:66::2` |
| Landscape socket | `/root/.lkit/landscape/data/unix_link` |
| 接口自定义 DNS（`LAND_DNS_ADDR`） | `8.8.8.8`，支持英文逗号分隔多个 IPv4/IPv6 地址 |

非 lkit 部署通常使用 `/root/.landscape-router/unix_link`。socket 目录必须已存在。网桥名称最多 15 字符；网段应避开 LAN、VPN 和其他 Docker 网络。IPv4 只使用未占用的 RFC1918 私网段，同时调整子网、网关、容器地址；公网 IPv6 由 RA 动态获取，不在 Compose 填运营商前缀。

```bash
docker compose pull
docker compose up -d
```

Docker 不能在已有网络上原地更改 gateway mode。全新部署需创建带上述选项的新网络；网络名称如已被其他部署占用，应先规划停机释放，或使用不同名称和不重叠的网段。不要执行 `down -v` 或删除未备份的数据来腾位置。

## 接应程序日志

接应日志默认仅输出 `ERROR`，不再输出正常注册、心跳等 `INFO` 内容。`.env` 可配置：

```dotenv
LAND_REDIRECT_LOG_LEVEL=ERROR
```

要完全静默则设为 `LAND_REDIRECT_LOG_LEVEL=OFF`：除了向官方程序传入 `--log-level OFF`，还关闭 procd 对该接应进程 stdout/stderr 的日志收集。接应注册、转发、自动重启仍正常工作；PassWall、dnsmasq、系统日志服务不受影响。排障可临时改为 `WARN`、`INFO`、`DEBUG` 或 `TRACE`。

已有部署的 `.env` 若仍写着 `INFO`，升级镜像不会覆盖该显式设置，需要自行改为 `ERROR` 或 `OFF`，然后执行 `docker compose up -d --no-deps --force-recreate openwrt`。运行时会同步 UCI 中的 `landscape.container.log_level`，无需清空配置卷。省略或留空此变量时采用 `ERROR`。

LuCI 中的 `daemon.info: redirect_pkg_handler[...]` 是 procd 收集 stdout 时使用的 syslog 级别，不等于接应内部的日志级别：`ERROR` 内容仍可能显示在该前缀下。`OFF` 才是完全不收集该进程输出；历史日志不会因此删除，也不会隐藏服务启动失败等由管理脚本产生的错误。

## 固定容器 MAC

`.env` 中的 `EDGE_MAC_ADDRESS` 固定容器 `eth0` 的 MAC，默认使用有规律的本地管理单播地址：

```dotenv
EDGE_MAC_ADDRESS=AA:BB:CC:DD:EE:FF
```

Compose 在 `services.openwrt.networks.edge.mac_address` 设置该地址，不是修改宿主机网桥的 MAC。保持原有项目名、网络和卷名时，重建容器后 MAC 仍相同，IPv4/ULA 与 PassWall 持久配置保持原设置。多个实例必须使用不同的 MAC，例如将末尾改为 `FE`；不能使用 `01:02:03:04:05:06` 等组播地址。

已有部署只在实际 `.env` 中新增这一项，不要用示例文件覆盖现有密码和配置。应用前确认使用支持网络级 `mac_address` 的新版 Compose v2。修改后在 Debian 虚拟机的原 Compose 目录执行：

```bash
docker compose config --quiet
docker compose up -d --no-deps --force-recreate openwrt
docker compose exec -T openwrt cat /sys/class/net/eth0/address
```

重建会短暂中断该容器提供的代理服务；仅执行 `docker compose restart` 不会应用 MAC 改动。无需重建镜像或执行 `down -v`。内核通常显示为小写 `aa:bb:cc:dd:ee:ff`，与设置值相同。若曾在 LuCI 中手动覆盖 `eth0` 的 MAC，先移除该覆盖，以 Compose 为准。

## Landscape 与动态 IPv6

1. 只连接一个 Docker bridge，使用 cgroup v2，保留 `ld_flow_edge: "true"` 和只读 socket 挂载；不使用 host/macvlan 网络、额外网卡或 `init: true`。
2. 在 Landscape 将 `.env` 指定的网桥（默认 `br-openwrt`）设为 **LAN**，开启 **LAN 路由转发（LR）**，并保证客户端 LAN 入口的 Landscape 转发配置正确。
3. 为此桥配置 **LANv6** 获取动态公网 IPv6。LAN/LR/LANv6 是透明代理出口的部署配置，不为管理页面另建一套宿主机转发方案。
4. LANv6 选纯 RA（SLAAC），从所选 WAN 的上游 PD 分配未占用的 `/64`，关闭 M/O、DHCPv6 和桥上的 DHCPv4。
5. 容器保留 Docker ULA 和静态默认网关，设置 `accept_ra=2`、`accept_ra_defrtr=0`。netifd 重载后由 hotplug 恢复参数，公网地址不固化到 UCI。
6. 容器自身流量应走正常 WAN，不能再次导向自身；多 WAN 上游选择与故障切换由宿主机策略决定。

```text
管理：LAN 设备 -> Landscape LAN/LR -> 容器 IP:原生端口
代理：LAN 设备 -> Landscape Flow -> Docker veth 标记流量
      -> redirect_pkg_handler --mode route -> firewall4 / PassWall -> WAN
```

接应使用 route 模式，不固定 TProxy 端口。容器关闭 DHCPv4/DHCPv6/RA/NDP 发送服务，开启 NAT44/NAT66 和同接口转发，禁用 flow offloading/full-cone。上游有效路由仍由宿主机提供。

内核来自宿主机，必须支持 eBPF/TC、nftables、conntrack、IPv6 NAT 与代理所需 TPROXY/TUN；镜像中的 kmod 不会替换宿主内核。不要在容器中刷写固件或运行 sysupgrade。

### 透明代理能力检查

**PassWall 官方 APK 安装后保持原样，不打源码补丁、不绕过上游检查、不伪造模块列表。** 官方页面用 `lsmod` 中的模块名称判断可用性；安装在容器中的 OpenWrt kmod 无法替代宿主机实际运行的内核模块。宿主机模块尚未加载时，先在宿主机执行：

```bash
sudo modprobe -a nft_redir nft_tproxy nft_socket
```

然后刷新 PassWall 页面。需要宿主机开机预载时，可将这三个模块名各占一行加入其 `/etc/modules-load.d/landscape-openwrt.conf`，保留已有内容。若加载失败，应检查宿主机的匹配内核模块包，而不是修改 PassWall。

镜像另提供独立、非侵入式的能力诊断：

```bash
docker exec landscape-openwrt /usr/libexec/landscape-proxy-check
```

该命令检查 dnsmasq nftset、策略路由，并通过 `nft --check` 实际验证 IPv4/IPv6 REDIRECT、TCP/UDP TPROXY 和透明 socket 表达式，不安装测试规则。内核可按自身机制加载宿主机的匹配模块。启动与健康检查使用该诊断，PassWall 页面保留上游的判定方式，不注入任何替代代码。

对于把相关能力编译进内核的其他宿主机，诊断可能通过但官方 `lsmod` 检查仍不通过；这是上游检测限制，本项目不会通过伪造结果隐藏它。

若检查失败，LuCI 仍可用于诊断，但容器健康检查不会通过。查看 `/tmp/landscape-proxy-check.log`，由宿主机安装/加载与其内核版本匹配的 `nft_redir`、`nft_tproxy`、`nft_socket` 等模块；不能通过给容器安装另一版本的 OpenWrt kmod 修复宿主内核缺失。

## 宿主控制台隔离

容器只通过 LuCI、SSH 或 `docker exec -it landscape-openwrt sh` 管理，不在 Debian/PVE 的串口或虚拟终端提供登录。镜像的 `/etc/inittab` 仅保留系统启动、关机项，不运行 `askfirst` / `askconsole`。

每次启动，在官方 `/sbin/init` 执行前，`landscape-console` 会检查 Docker 的私有 `/dev` tmpfs，并在当前容器挂载命名空间内将 `/dev/console`、`/dev/kmsg`、虚拟终端及已存在的串口控制台设备绑定到 `/dev/null`。这样也能阻止 procd 在启动、关机时按宿主内核 `console=` 参数写入串口。保留 `/dev/tty`、`/dev/pts`、`/dev/ptmx`，不影响 SSH 和 `docker exec -it`。不改宿主设备、内核启动参数或 getty 服务，也不修改 procd、PassWall 的官方程序。

这项隔离用于防止特权容器误用宿主控制台，不是对恶意特权进程的安全边界。它会抑制硬件控制台与 kmsg 输出；OpenWrt 服务日志仍通过 `docker exec landscape-openwrt logread` 查看。健康检查会验证设备屏蔽仍然有效。

从发生过串口争用的旧镜像升级后，先拉取并重建容器，再检查：

```bash
docker compose pull
docker compose up -d
docker exec landscape-openwrt /usr/libexec/landscape-console --check
docker top landscape-openwrt -eo pid,ppid,tty,stat,comm
```

检查成功时第一条诊断没有输出，进程中不应再有占用 `ttyS0` / `tty1` 的容器登录程序。已有 Debian 串口会话若尚未恢复，从 Debian SSH 执行一次 `systemctl restart serial-getty@ttyS0.service`（仅适用于串口确实是 `ttyS0` 的部署）。该命令终止并重建串口登录会话，不重启系统，也无需长期重复执行或禁用宿主 getty。

`/etc/inittab` 不在配置卷中，因此这项修复随镜像更新，不覆盖现有 PassWall、DNS 和 SSH 配置；不要为了升级删除配置卷。

## 配置与组件

`LAND_DNS_ADDR` 只设置「网络 → 接口 → 自定义 DNS 服务器」（`network.lan.dns`），每次启动重新应用。支持单个地址，或使用英文逗号分隔多个 IPv4/IPv6 地址；逗号两侧可有空格，不支持空项、域名、URL、端口、CIDR 或换行。示例：

```dotenv
LAND_DNS_ADDR=8.8.8.8,1.1.1.1
# 也可混合 IPv4/IPv6；IPv6 不带方括号：
# LAND_DNS_ADDR=8.8.8.8,2606:4700:4700::1111
```

每个地址分别写入一个 UCI DNS 列表项，不把整串逗号文本当作一个服务器。多个地址不代表严格按填写顺序进行主备切换，实际查询策略由解析服务决定。修改 `.env` 后执行 `docker compose up -d` 重新创建容器应用参数。

**不再把 `LAND_DNS_ADDR` 写入「DHCP/DNS → DNS 转发」（`dhcp.@dnsmasq[0].server`）。** 新部署不添加默认转发条目，dnsmasq 使用 netifd 根据接口配置生成的 `/tmp/resolv.conf.d/resolv.conf.auto`；系统解析仍指向 `127.0.0.1`。之后转发规则和 DNS 策略由 LuCI/PassWall 管理，重复启动不会清空或覆盖。

已有配置卷不会自动删除旧默认项或改写已有 PassWall DNS 设置。旧版升级后，若要让普通 dnsmasq 改用接口 DNS，在 LuCI 的 DNS 转发列表中仅移除不再需要的旧默认地址（如 `8.8.8.8`），保留按域名转发和 PassWall 条目；在未由 PassWall 接管的配置下，取消「忽略解析文件」，确认解析文件是 `/tmp/resolv.conf.d/resolv.conf.auto`，然后保存并应用。不要指向 `/etc/resolv.conf`，它包含本机回环 DNS，可能形成循环；也不要为更新清空配置卷或手工重置初始化标记。

PassWall 订阅、节点和 ACL 保存在配置卷；`network`、`uhttpd`、`dropbear` 由运行时参数重新生成，root 密码与时区每次启动重新应用。SSH 主机密钥单独持久化。

不要挂载整个 `/etc`、`/lib` 或根目录。卷以外的自装软件包、手工更新核心不会随重建保留。

- 系统：[ImmortalWrt 正式 tag](https://github.com/immortalwrt/immortalwrt/tags)，排除 RC，官方 rootfs 核验官方 SHA256。
- PassWall：[官方 Release](https://github.com/Openwrt-Passwall/openwrt-passwall/releases) 的 `25.12+` APK 与简体中文 APK，核验 GitHub 资产 SHA256，并检查版本匹配。
- 系统 APK 源：**https://mirrors.ustc.edu.cn/immortalwrt**，保持官方包签名验证。
- 专用依赖源：官方 Release 推荐的 [openwrt-passwall-build](https://github.com/moetayuko/openwrt-passwall-build) `25.12/x86_64/passwall_packages`，验证固定公钥、索引签名和解析版本。
- 完整代理核心：GeoView、ChinaDNS-NG、Xray、sing-box、Hysteria、NaiveProxy、Shadow-TLS、Shadowsocks Rust、SSR、simple-obfs、v2ray-plugin、xray-plugin、GeoIP/GeoSite、DNS/透明代理工具和 HAProxy。清单见 `scripts/passwall-packages.txt`。
- 不安装 HomeProxy、OpenClash、Momo、PassWall2、ttyd；保留 LuCI、SSH 和必要系统组件，移除固件升级与硬件管理工具。

两个 GitHub 本地 PassWall APK 仅在 SHA256 验证后的离线事务允许未知 APK 签名；远程系统与依赖源不关闭签名或 HTTPS 验证。完整依赖按所提供的 [.run 参考包](https://github.com/bcseputetto/Are-u-ok/releases/download/iStoreOS_25.12/PassWall_26.9.9_x86_64_all_sdk_25.12.run) 清单核对，不运行其安装脚本。

实际版本在 `/usr/share/landscape-openwrt/packages.json`，上游 URL、摘要、源码版本在 `upstream.json`；不内置订阅、节点或登录密码。

## 每日自动更新

每天北京时间 **08:23** 检查稳定版 ImmortalWrt rootfs、PassWall APK/中文包、依赖索引、Landscape 接应程序和适配源码。任一输入变化才构建，全部未变直接跳过；GitHub 定时任务可能延迟。

仅通过真实容器测试的候选镜像才推送，最后更新 `latest`。缺包、摘要错误、依赖失败或测试失败均不发布；公钥轮换需要审阅，不自动接受陌生签名。

自动更新镜像不会替换正在运行的容器：

```bash
docker compose pull
docker compose up -d
```

标签为 `latest`、`immortalwrt-<版本>`、`passwall-<版本>`、`redirect_pkg_handler-v<版本>`、`immortalwrt-<版本>-passwall-<版本>`。完整 digest 可用于固定镜像。

工作流使用 GitHub 内置 `GITHUB_TOKEN`。Actions 的 `force=true` 可强制重建；定时任务每 40 天至多提交一次不触发构建的心跳，避免长期无活动使定时任务暂停。

## 验证与边界

```bash
docker port landscape-openwrt
# 正常无输出：没有宿主机发布端口。
docker exec landscape-openwrt uci get uhttpd.main.listen_http
docker exec landscape-openwrt uci get uhttpd.main.listen_https
docker exec landscape-openwrt uci get 'dropbear.@dropbear[0].Port'
docker exec landscape-openwrt logread -e redirect_pkg_handler
docker exec landscape-openwrt ip -6 addr show dev eth0 scope global
docker exec landscape-openwrt nslookup www.baidu.com 127.0.0.1
docker inspect --format '{{json .State.Health}}' landscape-openwrt
```

CI 除基础管理测试外，还创建隔离 LAN/WAN：对 TCP/UDP 流量添加 Landscape VLAN 标记，由镜像中的真实官方接应程序去标记，再由实际启用的 PassWall 经隔离 VLESS 节点访问目标。分别验证客户端和容器本机代理、IPv4/IPv6 目标与两种地址族的代理节点，通过目标观察到的来源确认流量确实经过代理，而非直接绕行。CI 的 TC 转发夹具模拟启用 LR 的双向路径，不修改宿主机 NAT 来让测试通过。

控制台测试使用专用伪终端模拟宿主 `ttyS0`、`tty1`、`console`、`kmsg`，并为候选容器提供隔离的 `console=ttyS0,115200n8`。检查正常启动、停止、同容器重启和带配置卷重建期间没有控制台输出或终端属性改写，同时验证容器内独立 PTY、procd 和服务日志可用。测试夹具自身也有注入输出和属性变更的反向测试，避免把无法观测误当作通过。CI 不运行 PVE xterm.js，仍需现场验收网页终端。

另检查容器 IPv4/ULA 出网 NAT 保留、代理开启时的管理可达性、默认和自定义管理端口、旧端口关闭、访问限制、真实 LuCI/SSH 密码登录、SSH 身份与 PassWall 配置持久化，以及 dnsmasq A/AAAA、模拟注册和 SLAAC 前缀更新。

CI 没有运行完整 Landscape 路由器，不能据此承诺关闭 LR 仍可用。现场仍需验证真实 Flow 分类、运营商 WAN/PD、实际订阅、TCP/UDP/IPv6 和 DNS 泄漏；不得将测试节点当作用户订阅或真实 ISP 验收。

健康检查代表本地服务、路由和 RA 参数就绪，不承诺外网或代理节点可用。镜像不修改宿主机已有临时 IPv6 路由或管理 SNAT 规则。

适配代码采用 GPL-3.0，各上游组件保持原有许可。
