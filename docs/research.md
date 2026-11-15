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
