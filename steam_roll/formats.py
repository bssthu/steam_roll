"""Small strict readers for Valve text KeyValues and appinfo v39-v41."""
import re
import struct


class FormatError(ValueError):
    pass


def text_vdf(text):
    tokens = []
    pattern = re.compile(r'\s+|//[^\n]*|"((?:\\.|[^"\\])*)"|([{}])|([^\s{}"]+)')
    pos = 0
    for m in pattern.finditer(text.lstrip('\ufeff')):
        if m.start() != pos:
            raise FormatError('无法解析 VDF 文本')
        pos = m.end()
        if m.group(1) is not None:
            tokens.append(('text', re.sub(r'\\([\\"nt])', lambda x: {'n': '\n', 't': '\t'}.get(x[1], x[1]), m[1])))
        elif m.group(2):
            tokens.append((m[2], m[2]))
        elif m.group(3):
            tokens.append(('text', m[3]))
    if pos != len(text.lstrip('\ufeff')):
        raise FormatError('VDF 文本不完整')
    index = 0

    def obj(nested=False, depth=0):
        nonlocal index
        if depth > 64:
            raise FormatError('VDF 嵌套过深')
        result = {}
        while index < len(tokens):
            kind, key = tokens[index]
            index += 1
            if kind == '}' and nested:
                return result
            if kind != 'text' or index >= len(tokens):
                raise FormatError('VDF 键值不完整')
            kind, value = tokens[index]
            index += 1
            if kind == '{':
                value = obj(True, depth + 1)
            elif kind != 'text':
                raise FormatError('VDF 值无效')
            result[key.lower()] = value
        if nested:
            raise FormatError('VDF 缺少结束括号')
        return result
    return obj()


def at(data, *keys):
    for key in keys:
        data = data.get(key.lower(), {}) if isinstance(data, dict) else {}
    return data if isinstance(data, dict) else {}


class BinaryKV:
    def __init__(self, data, strings=None):
        self.data, self.pos, self.strings = data, 0, strings

    def take(self, count):
        end = self.pos + count
        if end > len(self.data):
            raise FormatError('appinfo 数据被截断')
        result = self.data[self.pos:end]
        self.pos = end
        return result

    def number(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self):
        end = self.data.find(b'\0', self.pos)
        if end < 0:
            raise FormatError('appinfo 字符串不完整')
        value = self.data[self.pos:end].decode('utf-8', 'replace')
        self.pos = end + 1
        return value

    def object(self, depth=0):
        if depth > 64:
            raise FormatError('appinfo 嵌套过深')
        result = {}
        while True:
            kind = self.number('<B')
            if kind == 8:
                return result
            if self.strings is None:
                key = self.string()
            else:
                index = self.number('<I')
                if index >= len(self.strings):
                    raise FormatError('appinfo 字符串索引无效')
                key = self.strings[index]
            if kind == 0:
                value = self.object(depth + 1)
            elif kind == 1:
                value = self.string()
            elif kind in (2, 4, 6):
                value = self.number('<I')
            elif kind == 3:
                value = self.number('<f')
            elif kind == 7:
                value = self.number('<Q')
            elif kind == 10:
                value = self.number('<q')
            else:
                raise FormatError(f'appinfo 未支持的字段类型 {kind}')
            result[key.lower()] = value


def appinfo(data, wanted):
    reader = BinaryKV(data)
    magic = reader.number('<I')
    reader.number('<I')
    if magic not in (0x07564427, 0x07564428, 0x07564429):
        raise FormatError(f'尚未支持的 appinfo 版本 {magic:#x}')
    strings = None
    table_offset = len(data)
    if magic == 0x07564429:
        table_offset = reader.number('<Q')
        table = BinaryKV(data[table_offset:])
        count = table.number('<I')
        if count > 1_000_000:
            raise FormatError('appinfo 字符串表无效')
        strings = [table.string() for _ in range(count)]
    result = {}
    while reader.pos < table_offset:
        appid = reader.number('<I')
        if not appid:
            return result
        size = reader.number('<I')
        header = 60 if magic >= 0x07564428 else 40
        if size < header or reader.pos + size > table_offset:
            raise FormatError('appinfo 记录长度无效')
        if appid in wanted:
            payload = data[reader.pos + header:reader.pos + size]
            result[appid] = at(BinaryKV(payload, strings).object(), 'appinfo')
        reader.pos += size
    raise FormatError('appinfo 缺少结束记录')
