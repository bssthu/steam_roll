# 类似工具与实现取舍

调研日期：2026-11-15。

## 借鉴的交互

- [Playnite 的随机游戏选择器](https://api.playnite.link/docs/manual/features/randomGamePicker.html)：在游戏库里直接点骰子，快速决定玩什么。本工具把抽取放在页面主要位置，并允许抽取后继续重抽。
- [Playnite 的筛选功能](https://api.playnite.link/docs/manual/features/filtersAndFiltersPresets.html)：先缩小游戏范围再选择。本工具支持分组并集、安装状态、时长与最近游玩时间的交集。
- [Pickagame 更新记录](https://pickaga.me/changelog)：搜索索引中的官方说明提到随机选择、游玩时长等筛选和 Roll Again。页面直接访问返回 403，因此仅作为交互思路，没有依赖其接口或抓取实现。

本项目围绕本机 Steam 的实际分组、安装状态和隐藏列表实现；不需要把个人资料公开，不依赖第三方账号服务。抽取采用浏览器加密随机源和拒绝采样，避免取模偏差；本轮排除已抽游戏，分组重叠不增加某款游戏的概率。

## 数据格式参考

- [SteamDatabase / SteamAppInfo](https://github.com/SteamDatabase/SteamAppInfo/blob/master/README.md)：核实 `appinfo.vdf` v39–v41 的记录头、v41 字符串表。项目自行实现所需的只读解析器。
- [SteamLibraryOrganizer](https://github.com/okabeeer/SteamLibraryOrganizer)：核实新版集合在 `userdata/<account>/config/cloudstorage/cloud-storage-namespace-1.json` 的位置，以及本地分组、游玩记录和安装清单的组合读取方式。该工具包含修改收藏分组的功能，本项目没有引入其代码或写入流程。

实际实现还核对了本机数据：新版收藏 JSON、旧隐藏标记、多个游戏库、v41 二进制名称缓存、中文封面及哈希子目录封面。原始账户内容不放入项目或测试夹具。

## 排除已移除 Demo 和过期试玩

历史游玩记录不能代表当前拥有权限。本机核对确认，已移除的 Demo 可以继续出现在 `localconfig.vdf`，过期免费周末也会保留时长和旧收藏信息。

- [Depressurizer 的许可证读取器](https://github.com/julianxhokaxhiu/Depressurizer/blob/master/src/Depressurizer.Core/Models/SteamLicenseParser.cs) 及其引用的 [license_parser.py](https://github.com/wynick27/steam-missing-covers-downloader/blob/master/license_parser.py) 提供了 `licensecache` 格式线索：按账号编号生成 Valve 随机字节流，XOR 后读取 protobuf。本项目自行实现所需的只读解码，并额外验证末尾 CRC32；无需账号密码或 API Key。
- [Steam 的许可证消息定义](https://github.com/SteamDatabase/Protobufs/blob/master/steam/steammessages_clientserver.proto) 定义包编号、许可状态及限时额度。解码时不保留访问令牌、购买地区和所有者字段。
- [SteamKit 的许可状态枚举](https://github.com/SteamRE/SteamKit/blob/master/Resources/SteamLanguage/enums.steamd) 用于区分过期、撤销、未激活与仍有效许可；`0x200` 不是许可过期，不能误判。结合包信息的 `expirytime` 与时长限额过滤，多份许可按有效权限取并集。

许可证及包映射仅在内存中读取；无法核实时不回退到历史游戏列表。测试只使用合成许可与游戏，不保存用户的许可证、真实清单或凭证。

## 许可与客户端可见性是两层规则

继续对照用户提供的 Steam 列表后发现：通用包 `0` 授予的公共下载权限、某些仅安装后显示的内容，以及独立 App ID 的跨平台版本，都可能有有效许可，却不出现在当前客户端列表里。

处理依据来自本机 `appinfo.vdf` 的 `extended/visibleonlywheninstalled`、`common/visibleonlyonavailableplatforms` 与 `oslist`（兼容 `extended/validoslist`）。这些字段也可在 [GameNative 的 SteamApp 数据模型](https://github.com/utkarshdalal/GameNative/blob/master/app/src/main/java/app/gamenative/data/SteamApp.kt) 中核对部分对应字段。通用包条目还要区分是否另有有效的独立账号许可；已安装的通用包游戏继续保留。

不使用同名去重、固定游戏黑名单或目标数量裁剪。对照清单只用于验收，回归测试使用合成 App ID，覆盖有效未安装游戏、有效 Demo、同名不同平台版本，以及通用包与独立许可重叠的情况。
