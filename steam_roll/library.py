import json
import os
import re
import sys
import time
from pathlib import Path

from .formats import FormatError, appinfo, at, text_vdf
from .licenses import decode_licenses, licensed_apps, packageinfo


class LibraryError(ValueError):
    pass


def stable_read(path):
    """Never open a Steam file for anything except binary reading."""
    for _ in range(3):
        before = path.stat()
        with path.open('rb') as stream:
            data = stream.read()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and len(data) == after.st_size:
            return data
    raise LibraryError('Steam 正在更新本地缓存，请稍后刷新')


def read_vdf(path):
    return text_vdf(stable_read(path).decode('utf-8-sig'))


def integer(value):
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        return 0


def image_mime(header):
    if header.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if header.startswith(b'RIFF') and header[8:12] == b'WEBP':
        return 'image/webp'
    if header[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if header[4:8] == b'ftyp' and header[8:12] in (b'avif', b'avis'):
        return 'image/avif'
    return None


def discover():
    if os.environ.get('STEAM_ROLL_STEAM_PATH'):
        return Path(os.environ['STEAM_ROLL_STEAM_PATH']).resolve()
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 0, winreg.KEY_READ) as key:
                return Path(winreg.QueryValueEx(key, 'SteamPath')[0]).resolve()
        except OSError:
            pass
    for path in [Path.home() / '.steam/steam', Path('C:/Program Files (x86)/Steam')]:
        if (path / 'userdata').is_dir():
            return path.resolve()
    raise LibraryError('未找到 Steam。请设置 STEAM_ROLL_STEAM_PATH 后重新启动。')


def account_list(root):
    users = {}
    login = root / 'config/loginusers.vdf'
    if login.exists():
        users = at(read_vdf(login), 'users')
    active = 0
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam\ActiveProcess', 0, winreg.KEY_READ) as key:
                active = int(winreg.QueryValueEx(key, 'ActiveUser')[0])
        except OSError:
            pass
    result = []
    for path in (root / 'userdata').iterdir():
        if not path.name.isdecimal() or not (path / 'config/localconfig.vdf').is_file():
            continue
        user = users.get(str(int(path.name) + 76561197960265728), {})
        result.append({'id': path.name, 'name': user.get('personaname') or f'本地账号 {path.name}',
                       'active': int(path.name) == active, 'recent': user.get('mostrecent') == '1'})
    result.sort(key=lambda x: (not x['active'], not x['recent'], x['id']))
    if not result:
        raise LibraryError('没有找到本地账号。请先在 Steam 中登录并打开游戏库。')
    return result


def collections(user):
    hidden, groups, candidates = set(), {}, set()
    legacy_exists = False
    for path in [user / '7/remote/sharedconfig.vdf', user / 'config/sharedconfig.vdf']:
        if not path.exists():
            continue
        legacy_exists = True
        apps = at(read_vdf(path), 'userroamingconfigstore', 'software', 'valve', 'steam', 'apps')
        for key, value in apps.items():
            if not key.isdecimal() or not isinstance(value, dict):
                continue
            aid = int(key)
            candidates.add(aid)
            if integer(value.get('hidden')):
                hidden.add(aid)
            for name in at(value, 'tags').values():
                if isinstance(name, str):
                    groups.setdefault('legacy:' + name, {'id': 'legacy:' + name, 'name': name, 'ids': set(), 'dynamic': False})['ids'].add(aid)
    folder = user / 'config/cloudstorage'
    main = folder / 'cloud-storage-namespace-1.json'
    pending = folder / 'cloud-storage-namespace-1.modified.json'
    entries = {}
    for path in [main, pending]:
        if not path.exists():
            continue
        data = json.loads(stable_read(path).decode('utf-8-sig'))
        if not isinstance(data, list):
            raise LibraryError('Steam 分组缓存格式未知，已停止读取以保护隐藏游戏')
        for pair in data:
            if not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[1], dict):
                raise LibraryError('Steam 分组缓存不完整，请稍后刷新')
            key, entry = pair
            if isinstance(key, str) and key.startswith('user-collections.'):
                entries[key] = entry
    modern_hidden = False
    warnings = []
    for key, entry in entries.items():
        if entry.get('is_deleted'):
            if key == 'user-collections.hidden':
                modern_hidden = True
            continue
        value = entry.get('value')
        value = json.loads(value) if isinstance(value, str) else value
        if not isinstance(value, dict) or not isinstance(value.get('added', []), list) or not isinstance(value.get('removed', []), list):
            raise LibraryError('Steam 分组内容无效，已停止读取')
        # An invalid member must never silently become a visible game.
        try:
            added = {int(x) for x in value.get('added', [])}
            removed = {int(x) for x in value.get('removed', [])}
        except (ValueError, TypeError):
            raise LibraryError('Steam 分组成员无效，已停止读取') from None
        members = added - removed
        cid = key.removeprefix('user-collections.')
        if cid == 'hidden':
            hidden.update(members)
            modern_hidden = True
            continue
        name = str(value.get('name') or cid)
        dynamic = bool(value.get('filter') or value.get('filterSpec'))
        if dynamic:
            warnings.append(f'动态分组“{name}”仅显示本地明确列出的成员，Steam 动态规则未展开。')
        groups[cid] = {'id': cid, 'name': name, 'ids': members, 'dynamic': dynamic}
        candidates.update(members)
        # Modern collection with the same name supersedes the obsolete tag.
        groups.pop('legacy:' + name, None)
    if not modern_hidden and not legacy_exists:
        raise LibraryError('找不到可验证的隐藏列表。请在 Steam 打开游戏库并完成同步后重试。')
    if not modern_hidden:
        warnings.append('正在使用旧版隐藏列表；建议打开 Steam 游戏库完成新版缓存同步。')
    return hidden, groups, candidates, warnings


def installed_games(root):
    paths = {root.resolve()}
    manifest = root / 'steamapps/libraryfolders.vdf'
    warnings = []
    if manifest.exists():
        for value in at(read_vdf(manifest), 'libraryfolders').values():
            name = value.get('path') if isinstance(value, dict) else value
            if isinstance(name, str) and name:
                paths.add(Path(name).resolve())
    installed = {}
    for path in sorted(paths):
        steamapps = path / 'steamapps'
        if not steamapps.is_dir():
            warnings.append(f'游戏库不可访问：{path}。其中游戏不会标为已安装。')
            continue
        for file in steamapps.glob('appmanifest_*.acf'):
            try:
                data = at(read_vdf(file), 'appstate')
                aid = integer(data.get('appid'))
                dirname = data.get('installdir', '')
                target = (steamapps / 'common' / dirname).resolve()
                safe = target.is_relative_to((steamapps / 'common').resolve()) and bool(dirname)
                complete = bool(integer(data.get('stateflags')) & 4)
                installed[aid] = {'name': data.get('name', ''), 'installed': safe and complete and target.is_dir(),
                                  'owner': str(integer(data.get('lastowner')))}
            except (OSError, ValueError):
                warnings.append(f'无法读取安装清单 {file.name}，请稍后刷新。')
    return installed, warnings


def cover_path(root, user, aid, common=None):
    cache = root / 'appcache/librarycache'
    assets = {'library_capsule': [], 'library_header': []}
    for asset in ['library_capsule', 'library_header']:
        images = at(common or {}, 'library_assets_full', asset, 'image')
        for language in ['schinese', 'english', 'tchinese']:
            value = images.get(language, '')
            if isinstance(value, str):
                first = value.split('/')[0]
                if re.fullmatch('[a-fA-F0-9]{40}', first):
                    if re.fullmatch(r'[a-fA-F0-9]{40}/[a-zA-Z0-9_]+\.(jpg|png|webp)', value):
                        assets[asset].append(value)
                    assets[asset].append(first)
    for directory, names in [
        (user / 'config/grid', [f'{aid}p.png', f'{aid}p.jpg', f'{aid}.png', f'{aid}.jpg']),
        (cache / str(aid), assets['library_capsule'] + ['library_600x900_schinese.jpg', 'library_600x900.jpg', 'library_600x900.png']),
        (cache, [f'{aid}_library_600x900.jpg']),
        (cache / str(aid), assets['library_header'] + ['header_schinese.jpg', 'header.jpg', 'library_hero.jpg']),
        (cache, [f'{aid}_header.jpg']),
    ]:
        for name in names:
            p = directory / name
            if p.is_file() and p.resolve().is_relative_to(root.resolve()):
                # Some Steam cache .jpg files actually contain HTTP error HTML.
                with p.open('rb') as stream:
                    if image_mime(stream.read(16)):
                        return p
    return None


def online_cover(aid, common):
    images = at(common, 'library_assets_full', 'library_capsule', 'image')
    for language in ['schinese', 'english', 'tchinese']:
        value = images.get(language, '')
        if isinstance(value, str) and re.fullmatch(r'(?:[a-fA-F0-9]{40}/)?[a-zA-Z0-9_]+\.(jpg|png|webp)', value):
            return f'https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{aid}/{value}'
    return f'https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{aid}/library_600x900.jpg'


def visible_on_this_client(metadata, installed, platform=None):
    """Apply appinfo's visibility flags, without guessing from names or genre.

    Platform support alone does not hide an owned game: Steam must explicitly
    mark the app as visible only on available platforms (e.g. split Mac ports).
    """
    common, extended = at(metadata, 'common'), at(metadata, 'extended')
    enabled = lambda value: integer(value) > 0 or str(value).lower() == 'true'
    if enabled(extended.get('visibleonlywheninstalled')) and not installed:
        return False
    if enabled(common.get('visibleonlyonavailableplatforms')):
        current = platform or ('windows' if sys.platform == 'win32' else 'macos' if sys.platform == 'darwin' else 'linux')
        oslist = common.get('oslist') or extended.get('validoslist')
        if isinstance(oslist, str) and oslist.strip():
            available = {item.strip().lower() for item in oslist.split(',')}
            if current not in available:
                return False
    return True


def load_library(root, account=None):
    accounts = account_list(root)
    account = account or accounts[0]['id']
    if account not in {a['id'] for a in accounts}:
        raise LibraryError('无效的本地账号')
    user = root / 'userdata' / account
    hidden, groups, _, warnings = collections(user)
    local = at(read_vdf(user / 'config/localconfig.vdf'), 'userlocalconfigstore', 'software', 'valve', 'steam', 'apps')
    for key, value in local.items():
        if key.isdecimal():
            if isinstance(value, dict) and integer(value.get('hidden')):
                hidden.add(int(key))
    try:
        licenses = decode_licenses(stable_read(user / 'config/licensecache'), account)
        packages = packageinfo(stable_read(root / 'appcache/packageinfo.vdf'), {item.package_id for item in licenses})
        now = int(time.time())
        candidates, missing_packages, next_expiry = licensed_apps(licenses, packages, now)
        account_apps, _, _ = licensed_apps(licenses, packages, now, include_default_package=False)
    except (OSError, FormatError) as exc:
        raise LibraryError('无法核实当前游戏许可证，请打开 Steam 完成同步后刷新；不会使用历史游玩记录替代当前游戏库。') from exc
    if missing_packages:
        warnings.append(f'{len(missing_packages)} 个有效许可缺少本地包信息，相关游戏暂未纳入，请在 Steam 完成同步后刷新。')
    installed, notices = installed_games(root)
    warnings.extend(notices)
    # Package 0 is a common download entitlement, not an account's library.
    # Its otherwise unowned games become visible only when installed locally.
    # A separate valid account license continues to admit an uninstalled game.
    candidates.intersection_update(account_apps | {aid for aid, item in installed.items() if item['installed']})
    # Only the current account's valid licenses admit games. History, collection
    # references and remaining installation files cannot revive a removed license.
    candidates.difference_update(hidden)
    app_data = stable_read(root / 'appcache/appinfo.vdf')
    metadata = appinfo(app_data, candidates)
    demo_parents = {
        aid: integer(at(item, 'common').get('parent') or at(item, 'extended').get('demoofappid'))
        for aid, item in metadata.items() if str(at(item, 'common').get('type', '')).lower() == 'demo'
    }
    parent_ids = set(demo_parents.values()) - {0} - hidden - metadata.keys()
    if parent_ids:
        metadata.update(appinfo(app_data, parent_ids))
    games, covers = [], {}
    missing = 0
    for aid in sorted(candidates):
        common = at(metadata.get(aid, {}), 'common')
        is_installed = installed.get(aid, {}).get('installed', False)
        if not visible_on_this_client(metadata.get(aid, {}), is_installed):
            continue
        kind = str(common.get('type', '')).lower()
        if kind not in ('game', 'demo'):
            if not kind:
                missing += 1
            continue
        parent_type = str(at(metadata.get(demo_parents.get(aid), {}), 'common').get('type', '')).lower()
        if kind == 'demo' and parent_type and parent_type != 'game':
            continue
        info = local.get(str(aid), {})
        localized = at(common, 'name_localized')
        name = localized.get('schinese') or common.get('name') or installed.get(aid, {}).get('name') or f'游戏 {aid}'
        membership = [cid for cid, group in groups.items() if aid in group['ids']]
        cover = cover_path(root, user, aid, common)
        if cover:
            covers[aid] = cover
        minutes = integer(info.get('playtime'))
        games.append({'id': aid, 'name': name, 'originalName': common.get('name', name),
                      'minutes': minutes, 'lastPlayed': integer(info.get('lastplayed')),
                      'installed': is_installed,
                      'groups': membership or ['ungrouped'], 'type': kind,
                      'onlineCover': online_cover(aid, common),
                      'cover': f'/api/cover/{aid}?account={account}' if cover else None})
    if missing:
        warnings.append(f'{missing} 个本地条目缺少类型信息，暂未纳入游戏列表。请在 Steam 中打开游戏库后刷新。')
    display_groups = []
    for group in groups.values():
        count = sum(group['id'] in game['groups'] for game in games)
        if count:
            display_groups.append({k: group[k] for k in ['id', 'name', 'dynamic']} | {'count': count})
    ungrouped = sum('ungrouped' in game['groups'] for game in games)
    if ungrouped:
        display_groups.append({'id': 'ungrouped', 'name': '未分组', 'dynamic': False, 'count': ungrouped})
    payload = {'games': games, 'groups': display_groups, 'accounts': accounts, 'account': account,
               'updatedAt': int(time.time()), 'warnings': warnings,
               'nextLicenseExpiry': next_expiry,
               'sourceNote': '按本机有效许可证读取游戏库；已排除隐藏、过期及已移除条目。Steam 状态变化后请刷新。'}
    return payload, covers
