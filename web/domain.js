export function filterGames(games, filters, now = Date.now() / 1000) {
  const query = filters.search.trim().toLocaleLowerCase();
  return games.filter(game => {
    if (query && !`${game.name} ${game.originalName} ${game.id}`.toLocaleLowerCase().includes(query)) return false;
    if (filters.groups.size && !game.groups.some(id => filters.groups.has(id))) return false;
    if (filters.installed === 'yes' && !game.installed) return false;
    if (filters.installed === 'no' && game.installed) return false;
    if (filters.playtime === 'unplayed' && game.minutes !== 0) return false;
    if (filters.playtime === 'under2' && game.minutes >= 120) return false;
    if (filters.playtime === 'under10' && game.minutes >= 600) return false;
    if (filters.playtime === 'over10' && game.minutes < 600) return false;
    const age = (now - game.lastPlayed) / 86400;
    if (filters.recent === 'never' && game.lastPlayed !== 0) return false;
    if (filters.recent === 'week' && (!game.lastPlayed || age > 7)) return false;
    if (filters.recent === 'month' && (!game.lastPlayed || age > 30)) return false;
    if (filters.recent === 'old30' && (!game.lastPlayed || age < 30)) return false;
    if (filters.recent === 'old180' && (!game.lastPlayed || age < 180)) return false;
    return true;
  });
}

export function candidatePool(filtered, selected, manual, drawn, noRepeat) {
  return filtered.filter(game => (!manual || selected.has(game.id)) && (!noRepeat || !drawn.has(game.id)));
}

export function randomIndex(length, random = array => crypto.getRandomValues(array)) {
  if (!Number.isSafeInteger(length) || length < 1 || length > 0xffffffff) throw new RangeError('Empty or invalid pool');
  const limit = 0x100000000 - (0x100000000 % length);
  const value = new Uint32Array(1);
  do { random(value); } while (value[0] >= limit);
  return value[0] % length;
}

export function hours(minutes) {
  if (!minutes) return '未游玩';
  if (minutes < 60) return `${minutes} 分钟`;
  return `${(minutes / 60).toLocaleString('zh-CN', {maximumFractionDigits: 1})} 小时`;
}

export function lastPlayed(timestamp) {
  return timestamp ? new Date(timestamp * 1000).toLocaleDateString('zh-CN') : '暂无记录';
}
